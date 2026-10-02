#!/usr/bin/env python3
"""Read-only dead-code report for the SponsorScout package.

Usage:
    python tools/find_dead_code.py                 # everything
    python tools/find_dead_code.py --imports
    python tools/find_dead_code.py --i18n
    python tools/find_dead_code.py --no-allowlist  # also show deliberate keepers

Reports:
  * unused imports            (module-level names never referenced)
  * unreferenced private defs (`_name` defined once and called nowhere)
  * dead i18n keys            (`_("...")` never used in the package)

Nothing is ever deleted or modified: deletion stays a human decision.

Names kept on purpose (public API of a documented module, or an intentional
side effect) belong in ``tools/find_dead_code.allow``, one ``name`` (or
``path::name``) per line, optionally followed by ``# reason``.

Exit code 0 = clean, 1 = findings.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "sponsorscout"
ALLOW_FILE = Path(__file__).with_name("find_dead_code.allow")

# The i18n scan used to be a line-level regex that toggled a "inside a Markdown
# fence" flag and matched ``_("...")`` textually.  Both were wrong for Python
# source: implicit string concatenation hid real keys, and keys dispatched
# dynamically (``_(h)`` over HEADERS/CARD_KEYS) looked unused while being live
# UI text.  It is an AST walk now — see ``_i18n_keys_used`` / ``_i18n_keys_dispatched``.


def _iter_sources(include_tests: bool = False):
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in f"/{rel}" and not include_tests:
            continue
        yield rel, path


def _load_allowlist() -> dict[str, str]:
    allowed: dict[str, str] = {}
    if not ALLOW_FILE.exists():
        return allowed
    for line in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, reason = line.partition("#")
        name = name.strip()
        if name:
            allowed[name] = reason.strip()
    return allowed


def _is_allowed(finding: str, allowed: dict[str, str]) -> bool:
    entry = finding.split(": ", 1)[-1]
    return entry in allowed or entry.split("::", 1)[-1] in allowed


# ── unused imports ───────────────────────────────────────────────────────────

def find_unused_imports(include_tests: bool) -> list[str]:
    findings: list[str] = []
    for rel, path in _iter_sources(include_tests):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        imported: dict[str, int] = {}
        star = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported[alias.asname or alias.name.split(".")[0]] = node.lineno
                    star = star or alias.name == "*"
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue  # compiler directive, not a runtime name
                for alias in node.names:
                    if alias.name == "*":
                        star = True
                        continue
                    imported[alias.asname or alias.name] = node.lineno
        if star:
            continue
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    used.add(cur.id)
        for name, lineno in sorted(imported.items(), key=lambda kv: kv[1]):
            if name in used:
                continue
            if f'"{name}"' in source or f"'{name}'" in source:
                continue  # re-exported via __all__
            findings.append(f"{rel}:{lineno}: unused import {name!r}")
    return findings


# ── unreferenced private defs ────────────────────────────────────────────────

def find_unreferenced_defs(include_tests: bool) -> list[str]:
    defined: list[tuple[str, str, int]] = []
    for rel, path in _iter_sources(include_tests):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if name.startswith("_") and not name.startswith("__"):
                    defined.append((rel, name, node.lineno))
    if not defined:
        return []
    haystack = "\n".join(
        path.read_text(encoding="utf-8")
        for _rel, path in _iter_sources(include_tests=True)
    )
    return [
        f"{rel}:{lineno}: unreferenced private def {name!r}"
        for rel, name, lineno in defined
        if haystack.count(name) <= 1
    ]


# ── dead i18n keys ───────────────────────────────────────────────────────────

#: Module-level constants whose string-literal elements are translated at
#: runtime through ``_(h)``-style dispatch instead of a literal ``_("...")``
#: call.  Without this, every header / card label / column title is reported
#: as dead and a well-meaning cleanup deletes LIVE UI text (the Dashboard and
#: Search headers silently lose their Italian labels).  Keep in sync with the
#: ``HEADERS`` / ``HEADERS_RUNS`` / ``CARD_KEYS`` lists in ``ui/tabs/*.py``.
_DISPATCH_CONSTANTS = ("HEADERS", "HEADERS_RUNS", "HEADERS_LOG", "CARD_KEYS")


def _i18n_keys_used(path: Path) -> set[str]:
    """Literal ``_("...")`` keys used in one module, via the AST.

    AST rather than the line regex: the regex misses implicit string
    concatenation (``_("a" "b")``) and matches unrelated text, while the AST
    sees exactly the ``_(<str constant>)`` calls the real translation helper
    receives.  This mirrors ``tests/test_i18n_parity.py``.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    return keys


def _i18n_keys_dispatched(path: Path) -> set[str]:
    """Strings translated indirectly via ``_(variable)`` over a known list."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    dispatched: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") in _DISPATCH_CONSTANTS
                   for t in node.targets):
            continue
        for sub in ast.walk(node.value):
            if (isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)):
                dispatched.add(sub.value)
    return dispatched


def find_dead_i18n_keys(include_tests: bool) -> list[str]:
    sys.path.insert(0, str(ROOT))
    try:
        from sponsorscout.i18n import LANGUAGES
    except Exception as exc:  # pragma: no cover
        return [f"could not import sponsorscout.i18n: {exc}"]

    used: set[str] = set()
    for _rel, path in _iter_sources(include_tests):
        used |= _i18n_keys_used(path)
        used |= _i18n_keys_dispatched(path)

    return [
        f"i18n: unused EN key {key!r}"
        for key in sorted(LANGUAGES.get("en", {}))
        if key not in used
    ]


# ── entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    # Findings include i18n keys with arrows/dashes/stars.  The Windows
    # console defaults to cp1252, which raises UnicodeEncodeError on those and
    # would turn a report into a crash — so force UTF-8 and degrade
    # unencodable characters instead of failing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--imports", action="store_true", help="only unused imports")
    ap.add_argument("--i18n", action="store_true", help="only dead i18n keys")
    ap.add_argument("--tests", action="store_true",
                    help="also scan sponsorscout/tests")
    ap.add_argument("--no-allowlist", action="store_true",
                    help="also list names suppressed by find_dead_code.allow")
    args = ap.parse_args(argv)

    allowed = _load_allowlist()
    only_imports, only_i18n = args.imports, args.i18n
    do_imports = only_imports or not (only_imports or only_i18n)
    do_defs = not (only_imports or only_i18n)
    do_i18n = only_i18n or not (only_imports or only_i18n)

    findings: list[str] = []
    if do_imports:
        findings += find_unused_imports(args.tests)
    if do_defs:
        findings += find_unreferenced_defs(args.tests)
    if do_i18n:
        findings += find_dead_i18n_keys(args.tests)

    kept = [f for f in findings if _is_allowed(f, allowed)]
    real = [f for f in findings if f not in kept]

    if args.no_allowlist:
        for f in kept:
            print(f"KEPT (allowlisted): {f}")
    for f in real:
        print(f)

    if real:
        print(f"\n{len(real)} finding(s). "
              "This tool never deletes: review, then remove by hand.")
        return 1
    print(f"clean ({len(kept)} allowlisted name(s) suppressed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

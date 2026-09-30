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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "sponsorscout"
ALLOW_FILE = Path(__file__).with_name("find_dead_code.allow")

_TS_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_I18N_CALL_RE = re.compile(r"_\(\s*(\"|')((?:\\.|(?!\1).)*)\1")


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

def find_dead_i18n_keys(include_tests: bool) -> list[str]:
    sys.path.insert(0, str(ROOT))
    try:
        from sponsorscout.i18n import LANGUAGES
    except Exception as exc:  # pragma: no cover
        return [f"could not import sponsorscout.i18n: {exc}"]

    used: set[str] = set()
    for _rel, path in _iter_sources(include_tests):
        in_fence = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if _TS_FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for _q, key in _I18N_CALL_RE.findall(line):
                used.add(key)

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

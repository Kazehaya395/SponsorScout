"""i18n completeness guard.

Regression: 82 literal `_("...")` keys used across the app (tooltips, dialog
buttons, status messages, data-management forms) had no Italian entry, so the
Italian UI silently fell back to English — e.g. the Tools tab showed a
"Riprendi" button and English hover texts after switching language.

This test walks the shipped package with `ast` (regex cannot handle implicit
string concatenation, and it would also match `__init__`, attribute calls,
etc.) and asserts every literal key has an Italian translation.
"""
import ast
import pathlib

import pytest

from sponsorscout.i18n import LANGUAGES

#: Shipped application code only. `extra_for_dev_purpose` mirrors the
#: algorithms with its own strings and is not bundled; `tests` passes keys
#: inline on purpose.
_EXCLUDE_PARTS = ("extra_for_dev_purpose", "tests")


def _ui_keys() -> set:
    root = pathlib.Path(__file__).resolve().parents[1]
    keys = set()
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if any(part in rel for part in _EXCLUDE_PARTS):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        # Module-level string constants passed to _() (the tools.py
        # *_TOOLTIP / data_management.HINT_TEXT pattern: one const used by
        # both __init__ and retranslate()).  Without resolving these the
        # walker only sees literal _("…") calls and silently skips the
        # longest strings in the app.
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            else:
                continue
            if (isinstance(target, ast.Name)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                consts[target.id] = value.value
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_"
                    and node.args):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.add(arg.value)
            elif isinstance(arg, ast.Name) and arg.id in consts:
                keys.add(consts[arg.id])
    return keys


def test_every_ui_key_has_italian_translation():
    """No UI string may fall back to English in the Italian locale."""
    it = LANGUAGES["it"]
    missing = sorted(k for k in _ui_keys() if k not in it)
    assert not missing, ("these keys are used in the UI but have no Italian "
                         "translation, so the Italian UI shows English:\n"
                         + "\n".join(repr(m) for m in missing))


def test_english_dict_has_no_orphan_keys():
    """English is the identity map; IT-only keys exist, EN-only ones don't."""
    en, it = set(LANGUAGES["en"]), set(LANGUAGES["it"])
    orphan = sorted(en - it)
    assert not orphan, ("keys defined in EN but not IT would never be "
                        "translated:\n" + "\n".join(repr(o) for o in orphan))


def test_translation_placeholders_match_keys():
    """Every {placeholder} must survive translation (format() safety)."""
    import re
    placeholder = re.compile(r"\{[^{}]*\}")
    problems = []
    for key, value in LANGUAGES["it"].items():
        if key not in LANGUAGES["en"] and key == value:
            continue  # IT-only identity entry
        if set(placeholder.findall(key)) != set(placeholder.findall(value)):
            problems.append((key, value))
    assert not problems, ("placeholder mismatch between key and translation:\n"
                          + "\n".join(f"{k!r} -> {v!r}" for k, v in problems))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))

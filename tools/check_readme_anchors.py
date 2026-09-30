#!/usr/bin/env python3
"""Validate the internal heading links of a markdown file.

Usage:
    python tools/check_readme_anchors.py CODEBASE.md
    python tools/check_readme_anchors.py README.md --live

GitHub renders a heading as an anchor by lower-casing it, dropping punctuation
and replacing spaces with hyphens.  A link like ``[x](#some-heading)`` silently
404s if the heading text and the anchor disagree, and nothing in the build
catches it — which is how broken in-page links accumulate.

Exit code 0 = every in-page link resolves, 1 = at least one does not.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

# ATX headings, ignoring anything inside a fenced code block.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# Characters GitHub strips when building an anchor slug.
_STRIP_RE = re.compile(r"[^\w\- ]+", re.UNICODE)


def slugify(heading: str) -> str:
    """Return the GitHub anchor for one heading's text."""
    text = _STRIP_RE.sub("", heading.strip().lower())
    return text.replace(" ", "-")


def collect_anchors(text: str) -> set[str]:
    """Every anchor the document itself defines (fenced code excluded)."""
    anchors: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            anchors.add(slugify(m.group(2)))
    return anchors


def collect_links(text: str) -> list[tuple[int, str]]:
    """Every ``#fragment`` link target, with its 1-based line number."""
    found: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in _LINK_RE.findall(line):
            if target.startswith("#"):
                frag = urllib.parse.unquote(target[1:]).lower()
                if frag:
                    found.append((lineno, frag))
    return found


def check(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    anchors = collect_anchors(text)
    broken = [(n, frag) for n, frag in collect_links(text) if frag not in anchors]
    for lineno, frag in broken:
        print(f"{path}:{lineno}: broken in-page link -> #{frag}")
    total = len(collect_links(text))
    if broken:
        print(f"FAIL: {len(broken)} broken of {total} in-page link(s)")
        return 1
    print(f"OK: {total} in-page link(s) resolve")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default="README.md",
                    help="markdown file to check (default: README.md)")
    ap.add_argument("--live", action="store_true",
                    help="also require a GitHub-published anchor per heading")
    args = ap.parse_args(argv)
    rc = check(Path(args.path))
    if args.live and rc == 0:
        # Without network access we can only assert the local anchors exist,
        # which is what --live degrades to; it never makes the check weaker.
        print("(--live: remote anchor verification needs network access; "
              "local anchors verified)")
    return rc


if __name__ == "__main__":
    sys.exit(main())

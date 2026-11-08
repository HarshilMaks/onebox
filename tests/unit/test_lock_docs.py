"""Keep the planning docs honest: a broken link, anchor, status tag or orphan fails the suite."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCK = REPOSITORY_ROOT / ".lock"
DOCS = [
    *sorted(LOCK.rglob("*.md")),
    REPOSITORY_ROOT / "README.md",
    *sorted((REPOSITORY_ROOT / "docs").glob("*.md")),
]

FENCE = re.compile(r"```.*?```", re.S)
LINK = re.compile(r"\]\(([^)\s]+)\)")
TAG = re.compile(r"\[([A-Z][A-Z-]+)\](?!\()")  # a bracket followed by "(" is a link label, not a tag
LEGEND_ENTRY = re.compile(r"^- \*\*\[([A-Z-]+)\]\*\*", re.M)


def _text(path: Path) -> str:
    return FENCE.sub("", path.read_text(encoding="utf-8"))


def _slug(heading: str) -> str:
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading.strip().lower()))


def _anchors(path: Path) -> set[str]:
    return {_slug(heading) for heading in re.findall(r"^#+\s*(.*)$", _text(path), re.M)}


def _links():
    for doc in DOCS:
        for target in LINK.findall(_text(doc)):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            reference, _, anchor = target.partition("#")
            resolved = doc if not reference else (doc.parent / reference).resolve()
            yield doc, resolved, reference, anchor


def test_every_relative_link_and_anchor_resolves():
    problems = []
    for doc, target, reference, anchor in _links():
        if not target.exists():
            problems.append(f"{doc.name}: missing file {reference}")
        elif anchor and target.suffix == ".md" and anchor not in _anchors(target):
            problems.append(f"{doc.name}: no heading #{anchor} in {target.name}")
    assert not problems, "\n".join(problems)


def test_status_tags_come_from_the_readme_legend():
    legend = set(LEGEND_ENTRY.findall((LOCK / "README.md").read_text(encoding="utf-8")))
    assert legend, "the README status legend could not be read"
    problems = [
        f"{doc.name}: [{tag}]" for doc in DOCS for tag in TAG.findall(_text(doc)) if tag not in legend
    ]
    assert not problems, "\n".join(problems)


README_MAX_LINES = 150


def test_readme_stays_a_front_page():
    length = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8").count("\n") + 1
    assert length <= README_MAX_LINES, f"README is {length} lines; move detail into docs/ and link to it"


def test_every_lock_doc_is_linked_from_another_doc():
    linked = {target for _, target, _, _ in _links()}
    orphans = [doc.name for doc in DOCS if doc.is_relative_to(LOCK) and doc.name != "README.md" and doc not in linked]
    assert not orphans, f"not linked from any doc: {orphans}"

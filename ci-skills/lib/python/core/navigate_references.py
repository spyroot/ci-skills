"""Read only selected own-text Markdown sections from registered references.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from lib.python.core import catalog
from lib.python.core.json_types import JSONObject
from lib.python.core.navigate_common import (
    MAX_CHOICES,
    MAX_TEXT_BYTES,
    NavigationError,
    choice,
    pointer,
)
from lib.python.core.provenance import ProvenanceError, skill_identity

SKILL_ROOT: Path = Path(__file__).resolve().parents[3]
HEADING: Final = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n)?$")
FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class Section:
    """One own-text slice and its hierarchy position.

    :param anchor: Stable heading slug, with a suffix for repeated headings.
    :param title: Original heading text.
    :param level: Markdown heading depth.
    :param text: Original lines up to the next heading.
    :param parent: Nearest parent anchor, or empty for a root section.

    .. attribute :: anchor

        Stable heading slug, with a suffix for repeated headings.

    .. attribute :: title

        Original heading text.

    .. attribute :: level

        Markdown heading depth.

    .. attribute :: text

        Original lines up to the next heading.

    .. attribute :: parent

        Nearest parent anchor, or empty for a root section.
    """

    anchor: str
    title: str
    level: int
    text: str
    parent: str


def slug(text: str) -> str:
    """Produce a stable lowercase identifier from a heading or reference name.

    :param text: Display name from a trusted installed reference.
    :returns: Heading identifier without Markdown punctuation.
    """
    return re.sub(r"[^a-z0-9_-]", "", text.casefold().replace(" ", "-")) or "section"


def references() -> Mapping[str, Mapping[str, str]]:
    """Expose readable Markdown references from the single proposition registry.

    :returns: Stable reference identifiers with their existing summary and path.
    """
    return {
        slug(name): record
        for name, record in catalog.PROPOSITION_REFERENCES.items()
        if Path(record["pointer"]).suffix == ".md"
    }


def resolve(reference: str) -> tuple[str, Mapping[str, str]]:
    """Resolve a registered reference id or an unambiguous filename stem.

    :param reference: Requested reference identifier.
    :returns: Canonical identifier and its existing registry record.
    :raises NavigationError: If no unique registered reference matches.
    """
    entries = references()
    if reference in entries:
        return reference, entries[reference]
    matches = [
        (name, entry)
        for name, entry in entries.items()
        if Path(entry["pointer"]).stem == reference
    ]
    if len(matches) != 1:
        raise NavigationError("reference_unknown", reference)
    return matches[0]


def sections(text: str) -> Sequence[Section]:
    """Split Markdown headings while treating fenced code as opaque.

    :param text: Complete local reference source, retaining its newlines.
    :returns: Document-order own-text sections; no descendant text is duplicated.
    :raises ValueError: If repeated-heading suffixes collide with an explicit heading.
    """
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, str, str, int, str]] = []
    counts: dict[str, int] = {}
    anchors: set[str] = set()
    parents: list[tuple[int, str]] = []
    fence_character, fence_length = "", 0
    for index, line in enumerate(lines):
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not fence_character:
                fence_character, fence_length = marker[0], len(marker)
            elif (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and not line[fence.end() :].strip()
            ):
                fence_character, fence_length = "", 0
            continue
        if fence_character:
            continue
        heading = HEADING.match(line)
        if not heading:
            continue
        level, title = len(heading.group(1)), heading.group(2)
        base = slug(title)
        suffix = counts.get(base, 0)
        counts[base] = suffix + 1
        anchor = f"{base}-{suffix}" if suffix else base
        if anchor in anchors:
            raise ValueError(f"reference anchor collision: {anchor}")
        anchors.add(anchor)
        while parents and parents[-1][0] >= level:
            parents.pop()
        headings.append(
            (index, anchor, title, level, parents[-1][1] if parents else "")
        )
        parents.append((level, anchor))
    return tuple(
        Section(
            anchor,
            title,
            level,
            "".join(
                lines[
                    start : headings[position + 1][0]
                    if position + 1 < len(headings)
                    else len(lines)
                ]
            ),
            parent,
        )
        for position, (start, anchor, title, level, parent) in enumerate(headings)
    )


def load(reference: str) -> tuple[str, Sequence[Section], str]:
    """Read one registered document, preserving exact source bytes and provenance.

    :param reference: Registered id or filename stem.
    :returns: Canonical id, parsed own-text sections, and source revision or content pin.
    :raises NavigationError: If the reference is unregistered.
    :raises OSError: If the registered local document cannot be read.
    :raises UnicodeError: If source bytes are not UTF-8.
    """  # noqa: DOC502 - Registry lookup and source reads propagate these errors.
    name, entry = resolve(reference)
    raw = (SKILL_ROOT / entry["pointer"]).read_bytes()
    pin = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    try:
        identity = skill_identity(SKILL_ROOT)
        if identity["revision"]["verified"] and identity["revision"]["value"]:
            pin = identity["revision"]["value"]
    except ProvenanceError:
        pass
    return name, sections(raw.decode("utf-8")), pin


def section_choice(reference: str, section: Section, query: str = "") -> JSONObject:
    """Offer one section without expanding its source text.

    :param reference: Canonical document id.
    :param section: Selected heading and its own text.
    :param query: Optional search query used to explain the hit.
    :returns: Read pointer with a short when-to-read hint.
    """
    hint = (
        f'Read for the passage that contains "{query}".'
        if query
        else f"Read when you need {section.title}."
    )
    if query and len(hint) > 120:
        hint = "Read for the passage matching the query."
    return {
        **choice(
            section.anchor, section.title, ["get", reference, section.anchor], "read"
        ),
        "when": hint,
    }


def section_pages(
    section: Section, children: Sequence[JSONObject]
) -> Sequence[tuple[str, Sequence[JSONObject]]]:
    """Partition own-text at line ends and page child pointers without omission.

    :param section: Selected source slice.
    :param children: Immediate subsections in document order.
    :returns: Text pages followed by any remaining child-pointer pages.
    :raises ValueError: If one source line exceeds the declared text-page budget.
    """
    texts: list[str] = []
    current, size = "", 0
    for line in section.text.splitlines(keepends=True):
        length = len(line.encode())
        if length > MAX_TEXT_BYTES:
            raise ValueError("reference line exceeds the text-page budget")
        if size + length > MAX_TEXT_BYTES:
            texts.append(current)
            current, size = "", 0
        current += line
        size += length
    texts.append(current)
    count = max(len(texts), (len(children) + MAX_CHOICES - 1) // MAX_CHOICES)
    return [
        (
            texts[index] if index < len(texts) else "",
            children[index * MAX_CHOICES : (index + 1) * MAX_CHOICES],
        )
        for index in range(count)
    ]


def section_document(
    reference: str, anchor: str, *, page: int = 1, tier: str = ""
) -> JSONObject:
    """Return one selected section and pointers to its immediate children.

    :param reference: Registered reference identifier.
    :param anchor: Selected heading identifier.
    :param page: One-based text and child-pointer page.
    :param tier: Requested card or part; unavailable for unannotated local Markdown.
    :returns: Existing reference-section schema document.
    :raises NavigationError: If reference, anchor, tier or page cannot be resolved.
    :raises OSError: If the registered reference cannot be read.
    :raises UnicodeError: If the selected source is not UTF-8.
    :raises ValueError: If one source line exceeds the text-page budget.
    """  # noqa: DOC503 - Source reading and page construction propagate these errors.
    name, entries, pin = load(reference)
    selected = [item for item in entries if item.anchor == anchor]
    if not selected:
        raise NavigationError("anchor_unknown", anchor)
    if tier:
        raise NavigationError("tier_unavailable", tier)
    children = [section_choice(name, item) for item in entries if item.parent == anchor]
    pages = section_pages(selected[0], children)
    if page < 1 or page > len(pages):
        raise NavigationError("page_out_of_range", str(page))
    text, choices = pages[page - 1]
    return {
        "kind": "reference_section",
        "schema_version": "1.0",
        "reference": name,
        "anchor": anchor,
        "pinned": pin,
        "text": text,
        "choices": list(choices),
        "continuation": pointer("read", ["get", name, anchor, "--page", str(page + 1)])
        if page < len(pages)
        else None,
    }

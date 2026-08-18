"""Deterministic normalization and comparison for curator metadata."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable


DATE_FILE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.md")
ENTRY_START_RE = re.compile(r"^###\s+\S.*$", re.MULTILINE)
ADDED_LINE_RE = re.compile(r"^- \*\*Added\*\*: \d{4}-\d{2}-\d{2}$", re.MULTILINE)
SHARED_BY_LINE_RE = re.compile(r"^- \*\*Shared by\*\*: ([^\r\n]+)$", re.MULTILINE)
EMBEDDED_FIELD_RE = re.compile(r"\*\*[^*\r\n]+\*\*\s*:")


def normalize_shared_by_display(value: str) -> str:
    """Normalize compatibility characters and whitespace without changing case."""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def shared_by_key(value: str) -> str:
    """Return the comparison-only key for a shared-by display value."""
    return normalize_shared_by_display(value).casefold()


def edit_distance(left: str, right: str) -> int:
    """Return deterministic Levenshtein distance using a single-row matrix."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def are_similar_shared_by(left: str, right: str) -> bool:
    """Treat only distinct names of length three or more and one edit apart as similar."""
    left_key = shared_by_key(left)
    right_key = shared_by_key(right)
    return bool(
        left_key
        and right_key
        and left_key != right_key
        and max(len(left_key), len(right_key)) >= 3
        and edit_distance(left_key, right_key) == 1
    )


def _candidate_rank(provided: str, candidate: str) -> tuple[float | int | str, ...]:
    provided_key = shared_by_key(provided)
    candidate_key = shared_by_key(candidate)
    ratio = SequenceMatcher(None, provided_key, candidate_key, autojunk=False).ratio()
    return (edit_distance(provided_key, candidate_key), -ratio, candidate_key, candidate)


def ordered_similar_shared_by(provided: str, existing: Iterable[str]) -> list[str]:
    """Return unique similar displays, strongest first with stable spelling tie-breaks."""
    candidates = {value for value in existing if are_similar_shared_by(provided, value)}
    return sorted(candidates, key=lambda value: _candidate_rank(provided, value))


def exact_shared_by_variants(provided: str, existing: Iterable[str]) -> list[str]:
    """Return distinct archived displays with the same normalized comparison key."""
    provided_key = shared_by_key(provided)
    return sorted({value for value in existing if shared_by_key(value) == provided_key})


def _accepted_entry_blocks(content: str) -> list[str]:
    """Return blocks accepted by the archive parser's critical title/date rules."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[str] = []
    for segment in re.split(r"(?=^### )", normalized, flags=re.MULTILINE):
        block = re.sub(r"\n---\s*$", "", segment.strip()).rstrip("\n")
        if ENTRY_START_RE.search(block) and ADDED_LINE_RE.search(block):
            blocks.append(block)
    return blocks


def dated_note_shared_by_values(vault: Path) -> list[str]:
    """Read accepted shared-by values from canonical dated notes only."""
    values: list[str] = []
    dated_notes = sorted(
        (
            path
            for path in vault.glob("*.md")
            if DATE_FILE_RE.fullmatch(path.name) and path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.name,
    )
    for dated_note in dated_notes:
        for block in _accepted_entry_blocks(dated_note.read_text(encoding="utf-8")):
            matches = SHARED_BY_LINE_RE.findall(block)
            if len(matches) != 1:
                continue
            display = matches[0]
            if (
                display
                and display == display.strip()
                and not EMBEDDED_FIELD_RE.search(display)
            ):
                values.append(display)
    return values

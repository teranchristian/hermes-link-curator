"""Validation utilities for link curator entries."""
from __future__ import annotations

import importlib.util
import itertools
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _metadata_helper_candidates(validate_file: Path) -> tuple[Path, Path]:
    dashboard_directory = validate_file.resolve().parent
    profile_or_repository = dashboard_directory.parent
    return (
        profile_or_repository / "skill-obsidian" / "scripts" / "metadata_consistency.py",
        profile_or_repository
        / "skills"
        / "note-taking"
        / "obsidian"
        / "scripts"
        / "metadata_consistency.py",
    )


def _load_metadata_consistency(validate_file: Path | None = None):
    """Load the helper from one of the two reviewed repository/profile layouts."""
    source = Path(__file__) if validate_file is None else validate_file
    candidates = _metadata_helper_candidates(source)
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        module_name = "_hermes_link_curator_metadata_consistency"
        spec = importlib.util.spec_from_file_location(module_name, candidate)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load metadata consistency helper at {candidate}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    expected = " or ".join(str(candidate) for candidate in candidates)
    raise RuntimeError(
        "metadata consistency helper is missing; expected a regular non-symlink file at "
        f"{expected}"
    )


metadata_consistency = _load_metadata_consistency()


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]

    @property
    def is_valid(self) -> bool:
        return self.valid


def is_safe_http_url(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    if any(unicodedata.category(character).startswith("C") for character in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def validate_entry(block: str) -> ValidationResult:
    """
    Check a raw entry block (markdown) for format correctness.
    Returns ValidationResult with errors (must fix) and warnings (should fix).
    """
    errors = []
    warnings = []

    # Optional metadata is valid when absent. When a field-like line is present,
    # require the canonical spelling and shape so malformed values cannot be
    # silently treated as missing.
    block_lines = block.split("\n")
    context_marker_re = re.compile(
        r'^[ \t]*-[ \t]+\*{0,3}Context\*{0,3}(?:[ \t]*:|[ \t]+|[ \t]*$)'
    )
    context_canonical_re = re.compile(r'^- \*\*Context\*\*: `(work|personal)`$')
    context_lines = [line for line in block_lines if context_marker_re.match(line)]
    if context_lines and (
        len(context_lines) != 1 or not context_canonical_re.fullmatch(context_lines[0])
    ):
        errors.append("Malformed **Context** field (expected `work` or `personal` in backticks)")

    shared_by_marker_re = re.compile(
        r'^[ \t]*-[ \t]+\*{0,3}Shared by\*{0,3}(?:[ \t]*:|[ \t]+|[ \t]*$)'
    )
    shared_by_canonical_re = re.compile(r'^- \*\*Shared by\*\*: ([^\r\n]+)$')
    embedded_field_re = re.compile(r'\*\*[^*\r\n]+\*\*\s*:')
    shared_by_lines = [line for line in block_lines if shared_by_marker_re.match(line)]
    shared_by_valid = False
    if len(shared_by_lines) == 1:
        shared_by_match = shared_by_canonical_re.fullmatch(shared_by_lines[0])
        if shared_by_match:
            value = shared_by_match.group(1)
            shared_by_valid = (
                bool(value.strip())
                and value == value.strip()
                and not embedded_field_re.search(value)
            )
    if shared_by_lines and (len(shared_by_lines) != 1 or not shared_by_valid):
        errors.append("Malformed **Shared by** field (expected one non-empty plain-text line)")

    # 1. Title line
    title_lines = [line for line in block_lines if line.startswith("### ")]
    if len(title_lines) != 1 or not re.fullmatch(r'###\s+\S.*', title_lines[0]):
        errors.append("Missing or malformed ### title line")

    # 2. URL: require one canonical field and reject unsafe manual edits.
    url_marker_re = re.compile(r'^[ \t]*-[ \t]+\*{0,3}URL\*{0,3}(?:[ \t]*:|[ \t]+|[ \t]*$)')
    url_canonical_re = re.compile(r'^- \*\*URL\*\*: (\S+)$')
    url_lines = [line for line in block_lines if url_marker_re.match(line)]
    url_match = url_canonical_re.fullmatch(url_lines[0]) if len(url_lines) == 1 else None
    if not url_match:
        errors.append("Missing **URL** field or empty URL")
    elif not is_safe_http_url(url_match.group(1)):
        errors.append("Unsafe **URL** field (expected a valid http:// or https:// URL)")

    # 3. Added date
    added_m = re.search(r'^- \*\*Added\*\*: (\d{4}-\d{2}-\d{2})$', block, re.MULTILINE)
    if not added_m:
        errors.append("Missing **Added**: YYYY-MM-DD")
    else:
        date_str = added_m.group(1)
        try:
            from datetime import datetime
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            errors.append(f"Invalid date format: {date_str} (expected YYYY-MM-DD)")

    # 4. Type
    type_m = re.search(
        r'^- \*\*Type\*\*: `(github|x-post|article|tool|video|paper|other)`$',
        block,
        re.MULTILINE,
    )
    if not type_m:
        warnings.append("Missing **Type** field (defaults to 'other')")

    # 5. Tags
    tags_m = re.search(r'^- \*\*Tags\*\*:\s*(.*)$', block, re.MULTILINE)
    tags = tags_m.group(1).split() if tags_m else []
    if not tags:
        warnings.append("No tags found (should have at least 1)")
    elif any(not re.fullmatch(r'#[a-z0-9]+(?:-[a-z0-9]+)*', tag) for tag in tags):
        errors.append("Malformed **Tags** field (use lowercase topic tags with internal hyphens)")

    # 6. Summary
    if not re.search(r'^- \*\*Summary\*\*:', block, re.MULTILINE):
        warnings.append("Missing **Summary** field")

    # 7. Trailing separator check
    lines = block.strip().split('\n')
    if lines and lines[-1].strip() == '---':
        warnings.append("Entry has trailing --- separator (should not end with ---)")

    # 8. Check for em-dash vs hyphen-minus in title line
    for tl in title_lines:
        if '—' in tl:
            warnings.append(f"Title uses em-dash (U+2014) instead of hyphen-minus (-): '{tl[:60]}...'")
        if '–' in tl:
            warnings.append(f"Title uses en-dash (U+2013) instead of hyphen-minus (-): '{tl[:60]}...'")

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


# Auto-discover vault path:
# This file lives at <profile>/dashboard/validate.py
# Vault lives at <profile>/vault
# Override with $HERMES_ARCHIVE_VAULT env var if needed.
import os

DEFAULT_VAULT = os.environ.get(
    "HERMES_ARCHIVE_VAULT",
    str(Path(__file__).resolve().parent.parent / "vault")
)


def validate_vault(vault_path: str = None) -> dict:
    """
    Run full validation on the vault.
    Returns a report dict.
    """
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))

    if vault_path is None:
        vault_path = DEFAULT_VAULT
    vault = Path(vault_path)
    index_path = vault / "INDEX.md"

    results = {
        "total_entries": 0,
        "valid": 0,
        "errors_only": 0,
        "warnings": 0,
        "broken": [],
        "warnings_list": [],
        "metadata_errors": [],
        "metadata_warnings": [],
    }

    if not index_path.exists():
        results["error"] = "INDEX.md not found"
        return results

    content = index_path.read_text()
    chunks = re.split(r'\n---\n', content)

    # Import parser
    from archive import _parse_entry

    for i, chunk in enumerate(chunks):
        if not re.search(r'\*\*URL\*\*', chunk):
            continue

        results["total_entries"] += 1

        # Parse test
        entry = _parse_entry(chunk.strip())
        val = validate_entry(chunk)

        if val.valid:
            results["valid"] += 1
        elif len(val.errors) > 0 and len(val.warnings) == 0:
            results["errors_only"] += 1
        else:
            results["warnings"] += 1

        if val.errors:
            title_match = re.search(r'^###\s+(.+?)\s*$', chunk, re.MULTILINE)
            title = title_match.group(1)[:60] if title_match else f"chunk {i}"
            results["broken"].append({
                "index": i,
                "title": title,
                "errors": val.errors,
            })

        if val.warnings:
            title_match = re.search(r'^###\s+(.+?)\s*$', chunk, re.MULTILINE)
            title = title_match.group(1)[:60] if title_match else f"chunk {i}"
            for w in val.warnings:
                results["warnings_list"].append({"title": title, "warning": w})

    # Cross-entry identity checks use canonical dated notes only. INDEX.md is
    # intentionally excluded so mirrored entries are never counted twice.
    shared_by_values = sorted(set(metadata_consistency.dated_note_shared_by_values(vault)))
    for left, right in itertools.combinations(shared_by_values, 2):
        left_key = metadata_consistency.shared_by_key(left)
        right_key = metadata_consistency.shared_by_key(right)
        names = [left, right]
        if left_key == right_key:
            results["metadata_errors"].append({
                "names": names,
                "error": (
                    "Exact normalized shared_by spelling inconsistency: "
                    f"{left!r} and {right!r}"
                ),
            })
        elif metadata_consistency.are_similar_shared_by(left, right):
            results["metadata_warnings"].append({
                "names": names,
                "warning": (
                    f"Possible similar shared_by people: {left!r} and {right!r}; "
                    "review them manually"
                ),
            })

    return results


if __name__ == "__main__":
    import json, sys

    vault_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VAULT
    report = validate_vault(vault_path)

    print(f"\n=== Vault Validation Report ===")
    print(f"Total entries: {report['total_entries']}")
    print(f"Fully valid:    {report['valid']}")
    print(f"Has errors:     {report['errors_only']}")
    print(f"Has warnings:   {report['warnings']}")

    if report.get("broken"):
        print(f"\n=== Entries with ERRORS ({len(report['broken'])}) ===")
        for item in report["broken"]:
            print(f"\n  [{item['index']}] {item['title']}")
            for e in item["errors"]:
                print(f"    ERROR: {e}")

    if report.get("warnings_list"):
        print(f"\n=== WARNINGS ({len(report['warnings_list'])}) ===")
        seen = set()
        for item in report["warnings_list"]:
            key = item["warning"]
            if key not in seen:
                print(f"  - {item['title']}: {item['warning']}")
                seen.add(key)

    if report.get("metadata_errors"):
        print(f"\n=== METADATA ERRORS ({len(report['metadata_errors'])}) ===")
        for item in report["metadata_errors"]:
            print(f"  ERROR: {item['error']}")

    if report.get("metadata_warnings"):
        print(f"\n=== METADATA WARNINGS ({len(report['metadata_warnings'])}) ===")
        for item in report["metadata_warnings"]:
            print(f"  WARNING: {item['warning']}")

    if not any(
        report.get(key)
        for key in ("broken", "warnings_list", "metadata_errors", "metadata_warnings")
    ):
        print("\nAll entries are well-formed.")

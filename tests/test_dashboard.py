from __future__ import annotations

import importlib
import html
import hashlib
import re
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
METADATA_HELPER = ROOT / "skill-obsidian" / "scripts" / "metadata_consistency.py"
DATE = "2026-08-17"


def load_modules(monkeypatch: pytest.MonkeyPatch, vault: Path) -> tuple[ModuleType, ModuleType, ModuleType]:
    monkeypatch.setenv("HERMES_ARCHIVE_VAULT", str(vault))
    monkeypatch.syspath_prepend(str(DASHBOARD))
    for name in ("main", "validate", "archive"):
        sys.modules.pop(name, None)
    archive = importlib.import_module("archive")
    validate = importlib.import_module("validate")
    main = importlib.import_module("main")
    return archive, validate, main


def entry_block(
    title: str = "Example entry",
    *,
    shared_by: str | None = None,
    context: str | None = None,
    summary: str = "Example summary.",
    tags: str = "#testing #productivity",
    url: str = "https://example.com",
    entry_type: str = "article",
    added: str = DATE,
) -> str:
    lines = [
        f"### {title}",
        f"- **URL**: {url}",
        f"- **Type**: `{entry_type}`",
        f"- **Tags**: {tags}",
        f"- **Added**: {added}",
    ]
    if shared_by is not None:
        lines.append(f"- **Shared by**: {shared_by}")
    if context is not None:
        lines.append(f"- **Context**: `{context}`")
    lines.append(f"- **Summary**: {summary}")
    return "\n".join(lines)


def write_index(vault: Path, *entries: str) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    body = "\n---\n".join(entries)
    (vault / "INDEX.md").write_text(f"# Index\n---\n{body}\n---\n")


def write_daily(vault: Path, *entries: str, date: str = DATE) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    body = "\n---\n".join(entries)
    (vault / f"{date}.md").write_text(f"# {date}\n\n{body}\n---\n")


@pytest.mark.parametrize(
    ("shared_by", "context"),
    [(None, None), ("Ibby", "work"), ("Ibby", None), (None, "personal")],
)
def test_parser_optional_field_combinations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shared_by: str | None,
    context: str | None,
) -> None:
    archive, _, _ = load_modules(monkeypatch, tmp_path / "vault")

    parsed = archive._parse_entry(entry_block(shared_by=shared_by, context=context))

    assert parsed is not None
    assert parsed.shared_by == shared_by
    assert parsed.context == context


def test_parser_trims_shared_by_and_fields_are_position_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive, _, _ = load_modules(monkeypatch, tmp_path / "vault")
    block = entry_block().replace(
        "- **Type**: `article`",
        "- **Context**: `work`\n- **Type**: `article`\n- **Shared by**:   Ibby   ",
    )

    parsed = archive._parse_entry(block)

    assert parsed is not None
    assert parsed.shared_by == "Ibby"
    assert parsed.context == "work"


def test_parser_keeps_summary_note_source_and_status_separate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    block = entry_block(summary="Full summary with\na second line.") + (
        "\n- **Note**: A preserved note."
        "\n- **Source**: User supplied source."
        "\n- **Status**: `reviewed`"
    )

    parsed = archive._parse_entry(block)

    assert parsed is not None
    assert parsed.summary == "Full summary with\na second line."
    assert parsed.note == "A preserved note."
    assert parsed.source == "User supplied source."
    assert parsed.status == "reviewed"
    assert validate.validate_entry(block).valid


def test_search_includes_shared_by_and_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        entry_block("Work link", shared_by="Ibby", context="work"),
        entry_block(
            "Weekend link",
            context="personal",
            url="https://example.com/personal",
            tags="#testing #weekend",
        ),
    )
    archive, _, _ = load_modules(monkeypatch, vault)

    assert [entry.title for entry in archive.search_entries("IBBY")] == ["Work link"]
    assert [entry.title for entry in archive.search_entries("WoRk")] == ["Work link"]
    assert [entry.title for entry in archive.search_entries("PERSONAL")] == ["Weekend link"]


@pytest.mark.parametrize(
    "line",
    [
        "- **Context**: work",
        "- **Context**: ``",
        "- **Context**: `Work`",
        "- **Context**: `social`",
        "- **Context**:",
        "- Context: `work`",
        "- *Context*: `work`",
        "- **Context** `work`",
        " - **Context**: `work`",
    ],
)
def test_validation_rejects_malformed_context_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, line: str
) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    block = entry_block().replace(f"- **Added**: {DATE}", f"- **Added**: {DATE}\n{line}")

    result = validate.validate_entry(block)

    assert not result.valid
    assert any("Context" in error for error in result.errors)


def test_validation_rejects_duplicate_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    block = entry_block(context="work").replace(
        "- **Context**: `work`", "- **Context**: `work`\n- **Context**: `personal`"
    )

    result = validate.validate_entry(block)

    assert not result.valid
    assert any("Context" in error for error in result.errors)


def test_validation_accepts_missing_fields_and_natural_context_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    block = entry_block(
        title="Context in distributed systems",
        summary="This summary discusses context and says it was shared by a colleague.",
    )

    result = validate.validate_entry(block)

    assert result.valid
    assert not result.errors
    assert not [warning for warning in result.warnings if "Context" in warning or "Shared by" in warning]


@pytest.mark.parametrize(
    "line",
    [
        "- **Shared by**:",
        "- Shared by: Ibby",
        "- *Shared by*: Ibby",
        "- **Shared by**:  Ibby",
        "- **Shared by**: Ibby ",
        "- **Shared by**: Ibby **URL**: https://evil.example",
        "- **Shared by**: Ibby\rInjected",
    ],
)
def test_validation_rejects_malformed_shared_by(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, line: str
) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    block = entry_block().replace(f"- **Added**: {DATE}", f"- **Added**: {DATE}\n{line}")

    result = validate.validate_entry(block)

    assert not result.valid
    assert any("Shared by" in error for error in result.errors)


def test_validation_rejects_duplicate_shared_by(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    block = entry_block(shared_by="Ibby").replace(
        "- **Shared by**: Ibby", "- **Shared by**: Ibby\n- **Shared by**: Alice"
    )

    result = validate.validate_entry(block)

    assert not result.valid
    assert any("Shared by" in error for error in result.errors)


def test_validator_loads_metadata_helper_from_repository_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")

    helper = validate._load_metadata_consistency(ROOT / "dashboard" / "validate.py")

    assert Path(helper.__file__).resolve() == METADATA_HELPER.resolve()
    assert helper.shared_by_key("  IBBY  ") == "ibby"


def test_validator_loads_metadata_helper_from_installed_profile_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    profile = tmp_path / "profile"
    validate_path = profile / "dashboard" / "validate.py"
    helper_path = (
        profile
        / "skills"
        / "note-taking"
        / "obsidian"
        / "scripts"
        / "metadata_consistency.py"
    )
    validate_path.parent.mkdir(parents=True)
    helper_path.parent.mkdir(parents=True)
    validate_path.write_text("# installed validator location\n")
    helper_path.write_text(METADATA_HELPER.read_text())

    helper = validate._load_metadata_consistency(validate_path)

    assert Path(helper.__file__).resolve() == helper_path.resolve()
    assert helper.are_similar_shared_by("John", "Jon")


def test_validator_missing_helper_does_not_fall_back_to_pythonpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, validate, _ = load_modules(monkeypatch, tmp_path / "vault")
    validate_path = tmp_path / "missing-profile" / "dashboard" / "validate.py"
    rogue = tmp_path / "rogue"
    validate_path.parent.mkdir(parents=True)
    rogue.mkdir()
    validate_path.write_text("# missing helper layout\n")
    (rogue / "metadata_consistency.py").write_text("ROGUE = True\n")
    monkeypatch.syspath_prepend(str(rogue))

    with pytest.raises(RuntimeError) as exc_info:
        validate._load_metadata_consistency(validate_path)

    message = str(exc_info.value)
    assert "metadata consistency helper is missing" in message
    assert "skill-obsidian/scripts/metadata_consistency.py" in message
    assert "skills/note-taking/obsidian/scripts/metadata_consistency.py" in message
    assert str(rogue) not in message


def test_cross_entry_shared_by_validation_uses_dated_notes_not_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_daily(vault, entry_block("Canonical", shared_by="Ibby"))
    write_index(
        vault,
        entry_block("Canonical", shared_by="Ibby"),
        entry_block("Index only", shared_by="Iby", url="https://example.com/index-only"),
    )
    _, validate, _ = load_modules(monkeypatch, vault)

    report = validate.validate_vault(str(vault))

    assert report["metadata_errors"] == []
    assert report["metadata_warnings"] == []
    assert report["total_entries"] == 2


def test_validator_reports_exact_and_similar_dated_name_pairs_once_deterministically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    entries = (
        entry_block("One", shared_by="Ibby", url="https://example.com/one"),
        entry_block("Two", shared_by="ibby", url="https://example.com/two"),
        entry_block("Three", shared_by="Iby", url="https://example.com/three"),
        entry_block("Four", shared_by="Ibby", url="https://example.com/four"),
    )
    write_daily(vault, *entries)
    write_index(vault, *entries)
    _, validate, _ = load_modules(monkeypatch, vault)
    before = {path.name: path.read_bytes() for path in vault.iterdir() if path.is_file()}

    first = validate.validate_vault(str(vault))
    second = validate.validate_vault(str(vault))

    assert first == second
    assert [item["names"] for item in first["metadata_errors"]] == [["Ibby", "ibby"]]
    assert [item["names"] for item in first["metadata_warnings"]] == [
        ["Ibby", "Iby"],
        ["Iby", "ibby"],
    ]
    assert {path.name: path.read_bytes() for path in vault.iterdir() if path.is_file()} == before


def test_index_remains_structurally_validated_with_dated_name_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    valid = entry_block("Dated", shared_by="Ibby")
    invalid_index = entry_block(
        "Index malformed", shared_by="Ibby", context="business"
    )
    write_daily(vault, valid)
    write_index(vault, invalid_index)
    _, validate, _ = load_modules(monkeypatch, vault)

    report = validate.validate_vault(str(vault))

    assert report["broken"]
    assert any(
        "Context" in error
        for broken in report["broken"]
        for error in broken["errors"]
    )
    assert report["metadata_errors"] == []
    assert report["metadata_warnings"] == []


def make_dashboard_client(monkeypatch: pytest.MonkeyPatch, vault: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    archive, _, main = load_modules(monkeypatch, vault)
    return archive, main, TestClient(main.app)


def filter_fixture(vault: Path) -> None:
    write_index(
        vault,
        entry_block(
            "Prompt defense",
            shared_by="Ibby",
            context="work",
            summary="A practical prompt security guide.",
            tags="#ai #ai-security #python #extra",
            url="https://example.com/work",
        ),
        entry_block(
            "Personal photos",
            shared_by="Mario",
            context="personal",
            summary="A private photo organizer.",
            tags="#photography #open-source #ai",
            url="https://example.com/photos",
            entry_type="github",
        ),
        entry_block(
            "More work",
            shared_by="ibby",
            context="work",
            summary="Python automation notes.",
            tags="#AI #python",
            url="https://example.com/more-work",
            entry_type="tool",
        ),
        entry_block(
            "Plain travel link",
            summary="No optional metadata.",
            tags="#travel #ai",
            url="https://example.com/plain",
        ),
    )


def numbered_entries(
    count: int,
    *,
    prefix: str = "Entry",
    added: str = DATE,
    tags: str = "#testing",
    summary: str = "Numbered pagination fixture.",
    shared_by: str | None = None,
    context: str | None = None,
    entry_type: str = "article",
) -> list[str]:
    return [
        entry_block(
            f"{prefix} {index:03d}",
            added=added,
            tags=tags,
            summary=summary,
            shared_by=shared_by,
            context=context,
            entry_type=entry_type,
            url=f"https://example.com/{prefix.casefold().replace(' ', '-')}-{index}",
        )
        for index in range(count)
    ]


def pagination_href(page_html: str, label: str) -> str:
    match = re.search(rf'<a href="([^"]+)" rel="(?:prev|next)">{label}</a>', page_html)
    assert match is not None
    return html.unescape(match.group(1))


@pytest.mark.parametrize(
    ("count", "first_count", "total_pages", "last_page_count"),
    [
        (0, 0, 1, 0),
        (1, 1, 1, 1),
        (49, 49, 1, 49),
        (50, 50, 1, 50),
        (51, 50, 2, 1),
        (100, 50, 2, 50),
        (127, 50, 3, 27),
    ],
)
def test_pagination_helper_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    count: int,
    first_count: int,
    total_pages: int,
    last_page_count: int,
) -> None:
    archive, _, _ = load_modules(monkeypatch, tmp_path / "vault")
    values = list(range(count))

    first = archive.paginate_entries(values, 1)
    last = archive.paginate_entries(values, total_pages)

    assert archive.PAGE_SIZE == 50
    assert len(first.entries) == first_count
    assert len(last.entries) == last_page_count
    assert first.total_results == count
    assert first.total_pages == total_pages
    assert (first.first_result, first.last_result) == ((1, first_count) if count else (0, 0))
    assert first.previous_page is None
    assert last.next_page is None


def test_pagination_helper_has_no_duplicates_or_gaps() -> None:
    sys.path.insert(0, str(DASHBOARD))
    try:
        archive = importlib.import_module("archive")
        values = list(range(137))
        pages = [archive.paginate_entries(values, page).entries for page in (1, 2, 3)]
    finally:
        sys.path.pop(0)

    flattened = [value for page in pages for value in page]
    assert flattened == values
    assert len(flattened) == len(set(flattened)) == 137
    assert all(len(page) <= 50 for page in pages)


def test_large_temporary_vault_reaches_every_entry_across_html_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(vault, *numbered_entries(121, prefix="Large archive"))
    _, _, client = make_dashboard_client(monkeypatch, vault)

    responses = [client.get(path) for path in ("/", "/?page=2", "/?page=3")]
    rendered_titles = [
        title
        for response in responses
        for title in re.findall(r">(Large archive \d{3})</a>", response.text)
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert [response.text.count('class="entry-card is-collapsed"') for response in responses] == [
        50,
        50,
        21,
    ]
    assert "Showing 1–50 of 121" in responses[0].text
    assert "Showing 51–100 of 121" in responses[1].text
    assert "Showing 101–121 of 121" in responses[2].text
    assert rendered_titles == [f"Large archive {index:03d}" for index in range(121)]
    assert len(rendered_titles) == len(set(rendered_titles))


def test_home_paginates_newest_first_and_preserves_same_day_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    entries = [
        entry_block("Old", added="2026-08-15", url="https://example.com/old"),
        entry_block("Newest first", added="2026-08-17", url="https://example.com/newest-first"),
        entry_block("Newest second", added="2026-08-17", url="https://example.com/newest-second"),
        entry_block("Middle", added="2026-08-16", url="https://example.com/middle"),
    ]
    write_index(vault, *entries)
    _, main, client = make_dashboard_client(monkeypatch, vault)

    ordered = [entry.title for entry in main.build_dashboard_context("/")["entries"]]
    response = client.get("/")

    assert ordered == ["Newest first", "Newest second", "Middle", "Old"]
    assert response.status_code == 200
    title_positions = [
        response.text.index(f">{title}</a>")
        for title in ("Newest first", "Newest second", "Middle", "Old")
    ]
    assert title_positions == sorted(title_positions)


def test_home_date_grouping_can_span_page_boundary_without_duplication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        *numbered_entries(60, prefix="Same day", added="2026-08-17"),
        *numbered_entries(5, prefix="Older day", added="2026-08-16"),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    first = client.get("/")
    second = client.get("/?page=2")

    assert first.text.count('class="entry-card is-collapsed"') == 50
    assert second.text.count('class="entry-card is-collapsed"') == 15
    assert first.text.count('class="day-block__date">17 Aug 2026</a>') == 1
    assert second.text.count('class="day-block__date">17 Aug 2026</a>') == 1
    assert second.text.count('class="day-block__date">16 Aug 2026</a>') == 1
    assert "Same day 049" in first.text and "Same day 049" not in second.text
    assert "Same day 050" not in first.text and "Same day 050" in second.text
    assert second.text.index("17 Aug 2026") < second.text.index("16 Aug 2026")


@pytest.mark.parametrize("path", ["/?page=0", "/?page=-1", "/?page=banana"])
def test_invalid_page_values_are_rejected_by_fastapi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str
) -> None:
    vault = tmp_path / "vault"
    write_index(vault, entry_block())
    _, _, client = make_dashboard_client(monkeypatch, vault)

    assert client.get(path).status_code == 422


def test_default_empty_and_out_of_range_page_behavior(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_vault = tmp_path / "empty-vault"
    write_index(empty_vault)
    _, _, empty_client = make_dashboard_client(monkeypatch, empty_vault)

    empty = empty_client.get("/")
    assert empty.status_code == 200
    assert "No links match" in empty.text
    assert "Showing 1–0" not in empty.text
    assert empty_client.get("/?page=2").status_code == 404

    full_vault = tmp_path / "full-vault"
    write_index(full_vault, *numbered_entries(51))
    _, _, full_client = make_dashboard_client(monkeypatch, full_vault)
    default = full_client.get("/")
    assert default.status_code == 200
    assert "Page 1 of 2" in default.text
    assert full_client.get("/?page=3").status_code == 404


def test_all_filters_run_before_pagination_and_navigation_preserves_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    non_matches = numbered_entries(
        255,
        prefix="Unfiltered",
        tags="#other",
        summary="Something unrelated.",
        shared_by="Bob",
        context="work",
        entry_type="tool",
    )
    matches = numbered_entries(
        72,
        prefix="Banana match",
        tags="#kids #fruit",
        summary="Banana family reference.",
        shared_by="Alice & Co",
        context="personal",
        entry_type="article",
    )
    write_index(vault, *non_matches, *matches)
    _, _, client = make_dashboard_client(monkeypatch, vault)
    params = {
        "q": "banana",
        "context": "personal",
        "shared_by": "Alice & Co",
        "tag": "kids",
        "type": "article",
    }

    first = client.get("/", params=params)
    next_href = pagination_href(first.text, "Next")
    second = client.get(next_href)

    assert first.status_code == second.status_code == 200
    assert first.text.count('class="entry-card is-collapsed"') == 50
    assert second.text.count('class="entry-card is-collapsed"') == 22
    assert "Showing 1–50 of 72" in first.text
    assert "Showing 51–72 of 72" in second.text
    assert "Banana match 000" in first.text
    assert "Unfiltered 000" not in first.text
    parsed = urlsplit(next_href)
    assert parse_qs(parsed.query) == {**{key: [value] for key, value in params.items()}, "page": ["2"]}
    assert parsed.query.count("page=") == 1
    assert 'name="page"' not in first.text


def test_search_and_tag_routes_paginate_complete_match_sets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        *numbered_entries(61, prefix="Needle", tags="#kids #testing", summary="Needle result."),
        *numbered_entries(55, prefix="Other", tags="#other", summary="Unrelated."),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    search_first = client.get("/search", params={"q": "needle"})
    search_next = pagination_href(search_first.text, "Next")
    search_second = client.get(search_next)
    tag_first = client.get("/tag/kids", params={"type": "article"})
    tag_next = pagination_href(tag_first.text, "Next")
    tag_second = client.get(tag_next)

    assert search_first.text.count('class="entry-card is-collapsed"') == 50
    assert search_second.text.count('class="entry-card is-collapsed"') == 11
    assert "Showing 51–61 of 61" in search_second.text
    assert parse_qs(urlsplit(search_next).query) == {"q": ["needle"], "page": ["2"]}
    assert tag_first.text.count('class="entry-card is-collapsed"') == 50
    assert tag_second.text.count('class="entry-card is-collapsed"') == 11
    assert urlsplit(tag_next).path == "/tag/kids"
    assert parse_qs(urlsplit(tag_next).query) == {"type": ["article"], "page": ["2"]}


def test_malicious_filter_values_are_escaped_and_safely_encoded_in_page_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    malicious = '\"><script>alert(1)</script> & value'
    write_index(
        vault,
        *numbered_entries(
            51,
            prefix="Unsafe query fixture",
            shared_by=malicious,
            summary="Unsafe query fixture.",
        ),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    response = client.get("/", params={"shared_by": malicious})
    next_href = pagination_href(response.text, "Next")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert parse_qs(urlsplit(next_href).query) == {"shared_by": [malicious], "page": ["2"]}
    assert next_href.count("page=") == 1


def test_collapsed_cards_show_sender_and_omit_missing_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        entry_block("Shared", shared_by="Ibby", context="work"),
        entry_block("Plain", url="https://example.com/plain"),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    html = client.get("/").text

    assert 'class="entry-card is-collapsed"' in html
    assert 'class="sender-initial" aria-hidden="true">I</span>' in html
    assert "Shared by Ibby" in html
    assert html.count('class="entry-sender"') == 1
    assert html.count('class="entry-context"') == 1
    assert "Shared by:" not in html


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("context=work", ["Prompt defense", "More work"]),
        ("shared_by=MARIO", ["Personal photos"]),
        ("tag=PYTHON", ["Prompt defense", "More work"]),
        ("type=GITHUB", ["Personal photos"]),
        (
            "q=PrOmPt&context=WORK&shared_by=IBBY&tag=AI-SECURITY&type=ARTICLE",
            ["Prompt defense"],
        ),
    ],
)
def test_filters_are_independent_combinable_and_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    query: str,
    expected: list[str],
) -> None:
    vault = tmp_path / "vault"
    filter_fixture(vault)
    _, _, client = make_dashboard_client(monkeypatch, vault)

    response = client.get(f"/?{query}")

    assert response.status_code == 200
    for title in expected:
        assert title in response.text
    for title in {"Prompt defense", "Personal photos", "More work", "Plain travel link"} - set(expected):
        assert title not in response.text
    assert f"{len(expected)} result" in response.text


@pytest.mark.parametrize(
    "query",
    ["context=workish", "shared_by=Nobody", "tag=missing", "type=video"],
)
def test_unknown_or_non_exact_filters_return_zero_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, query: str
) -> None:
    vault = tmp_path / "vault"
    filter_fixture(vault)
    _, _, client = make_dashboard_client(monkeypatch, vault)

    response = client.get(f"/?{query}")

    assert response.status_code == 200
    assert "0 results" in response.text
    assert "No links match" in response.text


def test_selected_filters_clear_links_counts_top_topics_and_active_chips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    filter_fixture(vault)
    archive, main, client = make_dashboard_client(monkeypatch, vault)

    options = archive.get_filter_options()
    assert options["people"] == [("Ibby", 2), ("Mario", 1)]
    assert options["tags"][:4] == [("#ai", 4), ("#python", 2), ("#ai-security", 1), ("#extra", 1)]

    context = main.build_dashboard_context("/", tag="#ai")
    assert [(item["name"], item["count"]) for item in context["top_topics"]] == [
        ("#ai", 4),
        ("#python", 2),
        ("#ai-security", 1),
    ]

    html = client.get(
        "/?q=prompt&context=work&shared_by=Ibby&tag=%23ai-security&type=article"
    ).text
    assert 'value="prompt"' in html
    assert 'id="desktop-context-work" type="radio" name="context" value="work" checked' in html
    assert 'id="desktop-shared-by" name="shared_by" value="Ibby"' in html
    assert 'id="desktop-topic" name="tag" value="#ai-security"' in html
    assert 'id="desktop-type" name="type" value="article"' in html
    assert 'href="/" class="clear-filters"' in html
    assert html.count("active-filter-chip") >= 5
    assert "Search: prompt" in html
    assert "Context: work" in html
    assert "Shared by: Ibby" in html
    assert "Topic: #ai-security" in html
    assert "Type: article" in html
    assert html.count('class="topic-chip') == 4
    assert "More topics" in html
    assert "shared_by=Ibby" in html and "tag=%23ai-security" in html


def test_filter_comboboxes_support_typeahead_and_keyboard_navigation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    filter_fixture(vault)
    _, _, client = make_dashboard_client(monkeypatch, vault)

    html = client.get("/").text

    assert html.count('aria-autocomplete="list"') == 6
    assert html.count('class="combobox-listbox" role="listbox"') == 6
    assert 'autocomplete="off"' in html
    assert "function filterOptions()" in html
    assert "option.dataset.search.toLocaleLowerCase().includes(term)" in html
    assert "event.key === 'ArrowDown'" in html
    assert "event.key === 'ArrowUp'" in html
    assert "event.key === 'Enter'" in html
    assert "event.key === 'Escape'" in html
    assert "No matching options" in html
    assert "<select" not in html


def test_summary_display_truncation_preserves_full_source_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    short = "Short   summary with\nrepeated whitespace."
    long = (
        "This is a deliberately long summary with repeated    whitespace that must be truncated at a sensible "
        "word boundary while its complete original value remains available everywhere else."
    )
    vault = tmp_path / "vault"
    write_index(
        vault,
        entry_block("Short", summary=short),
        entry_block("Long", summary=long, url="https://example.com/long"),
    )
    archive, _, client = make_dashboard_client(monkeypatch, vault)

    short_display = archive.collapsed_summary(short)
    long_display = archive.collapsed_summary(long)
    assert short_display == "Short summary with repeated whitespace."
    assert len(short_display) < 100
    assert long_display.endswith("…")
    assert len(long_display) <= 100
    assert long_display == "This is a deliberately long summary with repeated whitespace that must be truncated at a sensible…"

    source = (vault / "INDEX.md").read_text()
    assert long in source
    parsed_long = next(entry for entry in archive.get_all_entries() if entry.title == "Long")
    assert parsed_long.summary == long

    html = client.get("/").text
    assert short_display in html
    assert long_display in html
    assert long in html
    assert "entry-collapsed-summary" in html
    assert "entry-full-summary" in html

    day_entries = client.get(f"/day-json/{DATE}").json()
    assert next(entry for entry in day_entries if entry["title"] == "Long")["summary"] == long


def test_malicious_card_values_are_escaped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_index(vault, entry_block())
    archive, main, _ = make_dashboard_client(monkeypatch, vault)
    malicious = archive.ArchiveEntry(
        title='<img src=x onerror="alert(1)">',
        url="",
        entry_type="article",
        tags=['#<svg onload="alert(4)">'],
        added=DATE,
        summary='<script>alert("summary")</script>',
        shared_by='<script>alert("sender")</script>',
        context="work",
    )

    html = str(main.templates.env.get_template("_components.html").module.entry_card(malicious))

    assert "<script>" not in html
    assert "<img src=" not in html
    assert "<svg onload=" not in html
    assert "&lt;script&gt;alert" in html
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in html
    assert "&lt;svg onload=&#34;alert(4)&#34;&gt;" in html


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,hello", "file:///tmp/private", "not-a-url"],
)
def test_unsafe_manual_url_is_invalid_and_not_clickable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    vault = tmp_path / "vault"
    block = entry_block(url=url)
    write_index(vault, block)
    archive, validate, main = load_modules(monkeypatch, vault)

    result = validate.validate_entry(block)
    parsed = archive._parse_entry(block)

    assert not result.valid
    assert any("Unsafe **URL**" in error for error in result.errors)
    assert parsed is not None
    assert parsed.url == url
    assert parsed.clickable_url is None
    html = str(main.templates.env.get_template("_components.html").module.entry_card(parsed))
    assert f'href="{url}"' not in html


def test_external_dashboard_dependencies_are_forbidden_and_d3_is_local() -> None:
    templates = list((DASHBOARD / "templates").glob("*.html"))
    templates.extend((ROOT / "skill-link-curator-dashboard" / "templates").glob("*.html"))
    external_resource = re.compile(
        r'<(?:script|link)\b[^>]*(?:src|href)=["\']https?://', re.IGNORECASE
    )
    for path in templates:
        assert not external_resource.search(path.read_text()), path

    css = "\n".join(path.read_text() for path in (DASHBOARD / "static").glob("*.css"))
    assert not re.search(r"@import\s+url\([^)]*https?://|url\([^)]*https?://", css, re.I)
    assert '/static/vendor/d3.v7.min.js' in (DASHBOARD / "templates" / "graph.html").read_text()
    assert 'https://d3js.org' not in (DASHBOARD / "templates" / "graph.html").read_text()


def test_vendored_d3_version_and_checksum() -> None:
    bundle = DASHBOARD / "static" / "vendor" / "d3.v7.min.js"
    content = bundle.read_bytes()
    assert content.startswith(b"// https://d3js.org v7.9.0")
    assert hashlib.sha256(content).hexdigest() == "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539"
    assert (bundle.parent / "LICENSE.d3.txt").is_file()


def test_graph_keeps_singleton_only_entry_but_hides_its_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(vault, entry_block("Bananas", tags="#bananas"))
    _, _, client = make_dashboard_client(monkeypatch, vault)

    graph = client.get("/graph-json")

    assert graph.status_code == 200
    data = graph.json()
    assert [node["label"] for node in data["nodes"] if node["kind"] == "entry"] == ["Bananas"]
    assert [node for node in data["nodes"] if node["kind"] == "tag"] == []
    assert data["links"] == []


def test_graph_returns_all_entries_when_all_tags_are_singletons_or_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        entry_block("Bananas", tags="#bananas", url="https://example.com/bananas"),
        entry_block("Pears", tags="#pears", url="https://example.com/pears"),
        entry_block("Untagged", tags="", url="https://example.com/untagged"),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    data = client.get("/graph-json").json()

    entry_nodes = [node for node in data["nodes"] if node["kind"] == "entry"]
    assert [node["label"] for node in entry_nodes] == ["Bananas", "Pears", "Untagged"]
    assert [node for node in data["nodes"] if node["kind"] == "tag"] == []
    assert data["links"] == []


def test_graph_shared_urls_get_distinct_opaque_ids_and_occurrence_specific_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    shared_url = "https://example.com/shared"
    write_index(
        vault,
        entry_block("First", tags="#common #alpha", url=shared_url),
        entry_block("Second", tags="#common #beta", url=shared_url),
        entry_block("Third", tags="#alpha #beta", url="https://example.com/third"),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    data = client.get("/graph-json").json()
    entries = {node["label"]: node for node in data["nodes"] if node["kind"] == "entry"}
    sources_by_target = {
        entry_id: {link["source"] for link in data["links"] if link["target"] == entry_id}
        for entry_id in (node["id"] for node in entries.values())
    }

    assert entries["First"]["id"] != entries["Second"]["id"]
    assert sources_by_target[entries["First"]["id"]] == {"tag:#common", "tag:#alpha"}
    assert sources_by_target[entries["Second"]["id"]] == {"tag:#common", "tag:#beta"}
    assert sources_by_target[entries["Third"]["id"]] == {"tag:#alpha", "tag:#beta"}
    for node in entries.values():
        assert re.fullmatch(r"entry:[0-9a-f]{64}:\d+", node["id"])
        assert shared_url not in node["id"]
        assert node["id"] != f"entry:{node['url']}"


def test_graph_identical_occurrences_have_distinct_deterministic_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    identical = entry_block("Identical", tags="#shared")
    write_index(vault, identical, identical)
    _, _, client = make_dashboard_client(monkeypatch, vault)

    first = client.get("/graph-json").json()
    second = client.get("/graph-json").json()
    first_ids = [node["id"] for node in first["nodes"] if node["kind"] == "entry"]
    second_ids = [node["id"] for node in second["nodes"] if node["kind"] == "entry"]

    assert len(first_ids) == len(set(first_ids)) == 2
    assert first_ids == second_ids
    assert {link["target"] for link in first["links"]} == set(first_ids)


def test_graph_duplicate_tag_token_does_not_activate_singleton_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(vault, entry_block("Repeated token", tags="#bananas #bananas"))
    _, _, client = make_dashboard_client(monkeypatch, vault)

    data = client.get("/graph-json").json()

    assert len([node for node in data["nodes"] if node["kind"] == "entry"]) == 1
    assert [node for node in data["nodes"] if node["kind"] == "tag"] == []
    assert data["links"] == []


def test_graph_duplicate_tag_tokens_do_not_create_duplicate_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        entry_block("First", tags="#shared #shared", url="https://example.com/first"),
        entry_block("Second", tags="#shared", url="https://example.com/second"),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    data = client.get("/graph-json").json()
    tag_nodes = [node for node in data["nodes"] if node["kind"] == "tag"]

    assert tag_nodes == [{"id": "tag:#shared", "label": "#shared", "kind": "tag", "count": 2}]
    assert len(data["links"]) == 2
    assert len({(link["source"], link["target"]) for link in data["links"]}) == 2


def test_graph_mixed_entries_have_expected_counts_links_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        entry_block(
            "Connected one",
            tags="#repeat #one-off",
            shared_by="Ibby",
            context="work",
            url="https://example.com/connected-one",
        ),
        entry_block("Connected two", tags="#repeat #another", url="https://example.com/connected-two"),
        entry_block("Standalone", tags="#unique", url="https://example.com/standalone"),
        entry_block("Untagged", tags="", url="https://example.com/untagged"),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    data = client.get("/graph-json").json()
    entry_nodes = [node for node in data["nodes"] if node["kind"] == "entry"]
    tag_nodes = [node for node in data["nodes"] if node["kind"] == "tag"]

    assert len(data["nodes"]) == 5
    assert len(entry_nodes) == 4
    assert tag_nodes == [{"id": "tag:#repeat", "label": "#repeat", "kind": "tag", "count": 2}]
    assert len(data["links"]) == 2
    assert {link["source"] for link in data["links"]} == {"tag:#repeat"}
    connected = next(node for node in entry_nodes if node["label"] == "Connected one")
    assert connected["type"] == "article"
    assert connected["url"] == "https://example.com/connected-one"
    assert connected["shared_by"] == "Ibby"
    assert connected["context"] == "work"
    assert connected["count"] == 1


def test_graph_empty_archive_returns_empty_dataset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(vault)
    _, _, client = make_dashboard_client(monkeypatch, vault)

    response = client.get("/graph-json")

    assert response.status_code == 200
    assert response.json() == {"nodes": [], "links": []}


def test_graph_templates_guard_standalone_forces_drag_and_double_click_behavior() -> None:
    paths = [
        DASHBOARD / "templates" / "graph.html",
        ROOT / "skill-link-curator-dashboard" / "templates" / "force-graph.html",
    ]

    for path in paths:
        template = path.read_text()
        assert "new Set(data.links.map(d => d.target))" in template
        assert "d3.forceX(width / 2)" in template
        assert "d3.forceY(height / 2)" in template
        assert "d.kind === 'entry' && !linkedEntryIds.has(d.id)" in template
        assert ".force('standalone-x', standaloneX)" in template
        assert ".force('standalone-y', standaloneY)" in template
        assert "window.addEventListener('resize', resizeGraph)" in template
        assert "centerForce.x(width / 2).y(height / 2)" in template
        assert "standaloneX.x(width / 2)" in template
        assert "standaloneY.y(height / 2)" in template
        assert "node.call(drag)" in template
        assert "if (d.kind === 'tag') { d.fx = null; d.fy = null; }" in template
        assert "node.filter(d => d.kind === 'entry').on('dblclick'" in template
        assert "window.open(d.url, '_blank', 'noopener')" in template


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_values_are_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host: str
) -> None:
    _, _, main = load_modules(monkeypatch, tmp_path / "vault")
    assert main.validate_bind_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_remote_bind_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host: str
) -> None:
    _, _, main = load_modules(monkeypatch, tmp_path / "vault")
    with pytest.raises(RuntimeError, match="ARCHIVE_ALLOW_REMOTE_BIND=1"):
        main.validate_bind_host(host)
    assert main.validate_bind_host(host, "1") == host


def test_default_bind_is_loopback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ARCHIVE_HOST", raising=False)
    monkeypatch.delenv("ARCHIVE_ALLOW_REMOTE_BIND", raising=False)
    _, _, main = load_modules(monkeypatch, tmp_path / "vault")
    assert main.HOST == "127.0.0.1"


def test_all_card_views_and_existing_json_endpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    write_index(
        vault,
        entry_block("Work link", shared_by="Ibby", context="work", tags="#testing #shared-topic"),
        entry_block(
            "Personal link",
            shared_by="Mario",
            context="personal",
            url="https://example.com/personal",
            tags="#testing #personal-topic",
            entry_type="github",
        ),
        entry_block("Plain link", url="https://example.com/plain", tags="#testing #plain"),
    )
    _, _, client = make_dashboard_client(monkeypatch, vault)

    for path in ("/", "/search?q=link", "/tag/testing", f"/day/{DATE}"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Shared by Ibby" in response.text
        assert "entry-collapsed-summary" in response.text

    calendar = client.get("/calendar")
    assert calendar.status_code == 200
    assert "if (entry.context)" in calendar.text
    assert "if (entry.shared_by)" in calendar.text
    assert "senderLabel.textContent = 'Shared by ' + entry.shared_by" in calendar.text
    assert "collapsed.textContent = truncateSummary(entry.summary)" in calendar.text
    assert "innerHTML" not in calendar.text

    day_json = client.get(f"/day-json/{DATE}")
    assert day_json.status_code == 200
    entries = day_json.json()
    assert entries[0]["shared_by"] == "Ibby"
    assert entries[0]["context"] == "work"
    assert entries[2]["shared_by"] is None
    assert entries[2]["context"] is None

    graph = client.get("/graph-json")
    assert graph.status_code == 200
    nodes = graph.json()["nodes"]
    entry_nodes = [node for node in nodes if node["kind"] == "entry"]
    tag_nodes = [node for node in nodes if node["kind"] == "tag"]
    assert entry_nodes
    assert all("shared_by" in node and "context" in node for node in entry_nodes)
    assert all("shared_by" not in node and "context" not in node for node in tag_nodes)

    stats = client.get("/stats")
    assert stats.status_code == 200
    assert stats.json()["by_context"] == {"work": 1, "personal": 1}

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "healthy",
        "vault_path": str(vault),
        "total_entries": 3,
        "total_days": 1,
    }

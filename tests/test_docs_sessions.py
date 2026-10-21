# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The Sessions documentation says every verb the CLI offers (MH-45).

Each verb `agent-teams sessions --help` lists has a section — a heading
naming `agent-teams sessions <verb>` — in one of the pages under
`docs/docs/sessions/`, so a verb added to the group without its page fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("datalayer_core", reason="the sessions verbs need agent-teams[datalayer]")

from agent_teams.cli import app

DOCS = Path(__file__).resolve().parents[1] / "docs" / "docs" / "sessions"


def _verbs() -> list[str]:
    result = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}).invoke(
        app, ["sessions", "--help"]
    )
    assert result.exit_code == 0, result.output
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    commands = text.split("Commands")[-1]
    return sorted(set(re.findall(r"^\W*\b([a-z]+)\b", commands, flags=re.M)) - {"help"})


def test_every_sessions_verb_has_a_page_section() -> None:
    verbs = _verbs()
    assert verbs == ["clone", "end", "export", "import", "list", "resume", "show", "swap"], verbs
    pages = {page.name: page.read_text(encoding="utf-8") for page in DOCS.glob("*.mdx")}
    assert pages, "the Sessions section has pages"
    for verb in verbs:
        heading = f"## `agent-teams sessions {verb}`"
        assert any(heading in text for text in pages.values()), (
            f"no page section for `agent-teams sessions {verb}`"
        )


def test_the_cli_reference_names_the_group_and_nothing_private_is_named() -> None:
    reference = (DOCS.parent / "cli" / "index.mdx").read_text(encoding="utf-8")
    assert "`sessions list" in reference and "[Sessions](../sessions)" in reference
    for page in [*DOCS.glob("*.mdx"), DOCS.parent / "cli" / "index.mdx"]:
        text = page.read_text(encoding="utf-8")
        assert "plans/" not in text and "datalayer/ui" not in text, page


REPO = DOCS.parents[2]


def test_nothing_public_names_a_private_plan_or_the_private_web_client() -> None:
    """Every page, the README, the changelog and the package's own docstrings say the
    seam with the platform in public words: never a private plan (`plans/`) or the
    private web client's repository (`datalayer/ui`)."""
    files = [
        REPO / "README.md",
        REPO / "CHANGELOG.md",
        *(REPO / "docs" / "docs").rglob("*.mdx"),
        *(REPO / "agent_teams").rglob("*.py"),
    ]
    named = []
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for word in ("plans/", "datalayer/ui"):
            if word in text:
                named.append(f"{path.relative_to(REPO)}: {word!r}")
    assert not named, "named where nothing private may be: " + ", ".join(named)


def test_the_readme_says_the_platform_extras_and_the_seam() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for words in (
        "agent-teams[datalayer]",
        "agent-teams[shield]",
        "DATALAYER_AI_AGENTS_URL",
        "DATALAYER_API_KEY",
        "--local",
    ):
        assert words in readme, words

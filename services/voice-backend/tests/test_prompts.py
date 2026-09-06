import pytest

from voice_agent.prompts import load_system_prompt


def test_prompt_loader_reads_markdown_and_honors_inline_override(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("# Agent\n\nDefault instructions.\n", encoding="utf-8")
    assert load_system_prompt(str(prompt)).startswith("# Agent")
    assert load_system_prompt(str(prompt), " Inline override ") == "Inline override"


def test_prompt_loader_rejects_missing_and_empty_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_system_prompt(str(tmp_path / "missing.md"))
    empty = tmp_path / "empty.md"
    empty.write_text(" \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_system_prompt(str(empty))

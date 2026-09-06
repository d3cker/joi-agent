import asyncio
import json

import pytest

from voice_agent.skills import (
    SkillCatalog,
    SkillCatalogManager,
    SkillTools,
    compose_system_prompt,
)


def _write_skill(root, slug="example", *, description="Handle example tasks."):
    directory = root / slug
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"""---
name: Example skill
description: {description}
version: 2
---
# Example

Follow the example workflow.
""",
        encoding="utf-8",
    )


def _write_manifest(root, enabled, revision=1):
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "revision": revision,
        "enabled": enabled,
    }), encoding="utf-8")


def test_catalog_loads_markdown_metadata_and_prompt_index(tmp_path):
    _write_skill(tmp_path)
    catalog = SkillCatalog.load(str(tmp_path))
    skill = catalog.get("example")
    assert skill is not None
    assert skill.name == "Example skill"
    assert skill.version == "2"
    assert skill.content.startswith("# Example")
    prompt = compose_system_prompt("# Base", catalog)
    assert "`example` — Handle example tasks." in prompt
    assert prompt.startswith("# Base")


def test_catalog_enabled_filter_is_strict(tmp_path):
    _write_skill(tmp_path, "first")
    _write_skill(tmp_path, "second")
    catalog = SkillCatalog.load(str(tmp_path), "second")
    assert [item.slug for item in catalog.list()] == ["second"]
    with pytest.raises(ValueError, match="not found"):
        SkillCatalog.load(str(tmp_path), "missing")
    with pytest.raises(ValueError, match="Invalid skill"):
        SkillCatalog.load(str(tmp_path), "../escape")


def test_catalog_rejects_invalid_or_oversized_skill(tmp_path):
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("# no front matter", encoding="utf-8")
    with pytest.raises(ValueError, match="front matter"):
        SkillCatalog.load(str(tmp_path))

    (invalid / "SKILL.md").write_text("x" * (256 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="larger"):
        SkillCatalog.load(str(tmp_path))


def test_skill_tools_list_and_lazy_read_without_execution(tmp_path):
    _write_skill(tmp_path)
    tools = SkillTools(SkillCatalog.load(str(tmp_path)))
    assert {item.name for item in tools.definitions()} == {"list_skills", "read_skill"}

    listed = asyncio.run(tools.execute("list_skills", {}))
    assert json.loads(listed.content)["skills"][0]["slug"] == "example"
    loaded = asyncio.run(tools.execute("read_skill", {"name": "example"}))
    assert loaded.content.startswith("# Example")
    missing = asyncio.run(tools.execute("read_skill", {"name": "missing"}))
    assert missing.is_error
    traversal = asyncio.run(tools.execute("read_skill", {"name": "../secret"}))
    assert traversal.is_error


def test_bundled_catalog_contains_stage3_baseline():
    catalog = SkillCatalog.load("skills")
    assert {skill.slug for skill in catalog.list()} == {
        "open-terminal-work", "project-engineering", "skill-authoring", "web-research"
    }
    assert "SearXNG" in catalog.get("web-research").content
    assert "only execution sandbox" in catalog.get("open-terminal-work").content


def test_manifest_manager_reloads_atomically_and_dynamic_tools_follow_it(tmp_path):
    _write_skill(tmp_path, "first")
    _write_skill(tmp_path, "second")
    _write_manifest(tmp_path, ["first"])
    manager = SkillCatalogManager(str(tmp_path))
    tools = SkillTools(manager)

    assert [item.slug for item in manager.snapshot().list()] == ["first"]
    read_definition = next(
        item for item in tools.definitions() if item.name == "read_skill"
    )
    assert read_definition.parameters["properties"]["name"]["enum"] == ["first"]

    _write_manifest(tmp_path, ["first", "second"], revision=2)
    manager.reload()
    assert manager.revision == 2
    listed = json.loads(asyncio.run(tools.execute("list_skills", {})).content)
    assert listed["revision"] == 2
    assert listed["skills"][1]["slug"] == "second"

    _write_manifest(tmp_path, ["missing"], revision=3)
    with pytest.raises(ValueError, match="not found"):
        manager.reload()
    assert manager.revision == 2
    assert [item.slug for item in manager.snapshot().list()] == ["first", "second"]


def test_manager_create_update_and_manifest_revision_require_explicit_reload(tmp_path):
    _write_skill(tmp_path, "first")
    _write_manifest(tmp_path, ["first"], revision=4)
    manager = SkillCatalogManager(str(tmp_path))
    raw = """---
name: Added skill
description: Handle newly added work.
version: 1
---
# Added

Follow the new workflow.
"""

    manager.install("added", raw, overwrite=False)
    assert manager.revision == 4
    assert manager.snapshot().get("added") is None
    assert manager.pending_manifest().revision == 5
    assert manager.pending_manifest().enabled == ("first", "added")

    manager.reload()
    assert manager.revision == 5
    assert manager.snapshot().get("added").name == "Added skill"
    with pytest.raises(FileExistsError):
        manager.install("added", raw, overwrite=False)

    updated = raw.replace("version: 1", "version: 2").replace(
        "Follow the new workflow.", "Follow the revised workflow."
    )
    manager.install("added", updated, overwrite=True)
    assert manager.snapshot().get("added").version == "1"
    manager.reload()
    assert manager.revision == 6
    assert manager.snapshot().get("added").version == "2"


def test_manager_rejects_credentials_and_invalid_manifest_without_changing_active(tmp_path):
    _write_skill(tmp_path, "first")
    _write_manifest(tmp_path, ["first"])
    manager = SkillCatalogManager(str(tmp_path))
    secret = """---
name: Unsafe
description: Unsafe test.
version: 1
---
# Unsafe

api_key = abcdefghijklmnop
"""
    with pytest.raises(ValueError, match="credential"):
        manager.install("unsafe", secret, overwrite=False)
    assert not (tmp_path / "unsafe").exists()
    assert manager.revision == 1

    (tmp_path / "manifest.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        manager.reload()
    assert manager.revision == 1


def test_reload_rejects_directly_added_secret_and_preserves_active_catalog(tmp_path):
    _write_skill(tmp_path, "first")
    _write_manifest(tmp_path, ["first"])
    manager = SkillCatalogManager(str(tmp_path))
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "SKILL.md").write_text("""---
name: Unsafe
description: Unsafe direct edit.
version: 1
---
# Unsafe

password: abcdefghijklmnop
""", encoding="utf-8")
    _write_manifest(tmp_path, ["first", "unsafe"], revision=2)

    with pytest.raises(ValueError, match="credential"):
        manager.reload()
    assert manager.revision == 1
    assert manager.snapshot().get("unsafe") is None

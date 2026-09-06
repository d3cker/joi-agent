---
name: Skill authoring
description: Draft, validate, install, update, and hot-reload a focused SKILL.md extension for this voice agent.
version: 2
---
# Skill authoring

Use this skill when the user asks to create or revise an agent skill. A draft
lives in Open Terminal; an active copy lives in the backend's persistent skill
store. These are separate machines and filesystems.

- One skill lives at `<slug>/SKILL.md`; the slug uses lowercase letters,
  numbers, and hyphens.
- Start with flat front matter containing `name`, `description`, and `version`.
- Make the description precise enough for the model to decide when to load it.
- Keep the body procedural, bounded, and independent of hidden context.
- Reference existing tools by their registered names. A skill is instruction,
  not executable code and not a new permission boundary.
- Never put passwords, API keys, model weights, or machine-specific secrets in
  a skill.
- Draft a new skill with Open Terminal `write` at exactly
  `.voice-agent/skill-drafts/<slug>/SKILL.md`.
- Call `validate_skill` with the slug and exact draft path. Fix validation
  errors through Open Terminal before continuing.
- For a new skill call `create_skill`; for a revision call `update_skill`.
  These tools import a validated copy into the backend and update its manifest.
- Call `reload_skills` last. Do not claim the skill is active unless this returns
  the new manifest revision and `list_skills` includes the slug.
- A failed reload preserves the previous active catalog. Never work around the
  importer by writing directly to the backend release directory.

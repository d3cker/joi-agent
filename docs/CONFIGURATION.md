# Configuration and skills

## Locations

`JOI_CONFIG_HOME` overrides `~/.config/joi`; otherwise XDG_CONFIG_HOME is
honored. `JOI_DATA_HOME` overrides `~/.local/share/joi`.
The installer uses these standard directories unless local-mode path flags are
provided. The Mac and Ubuntu do not share a folder over the network.

```text
~/.config/joi/
  backend/
    settings.json             # Versioned server/STT/VAD/TTS/tools/runtime settings
    models.json               # Active LLM profile and reasoning/context settings
    settings.previous.json    # Previous document for rollback
    models.previous.json
    secrets/                  # mode 0600, never returned by config reads
      client-api-key
      config-api-key
      llm-api-key              # Optional
      tts-api-key              # Optional
      open-terminal-api-key    # Optional
  security/
    ca.crt / ca.key
    server.crt / server.key
  prompts/default-system.md
  skills/manifest.json
  skills/<slug>/SKILL.md
  client-connection.json       # Private installer handoff; not server private keys
  client/settings.json         # On the Mac
  imports/                    # On the Mac, private installer handoffs
```

Configuration schema version and document revision are separate. Settings
updates require the current revision; a stale write returns a conflict.
Writes use atomic replacement and a previous-document backup. Values from
environment variables seed a fresh configuration; existing JSON is authoritative.
Bootstrap never overwrites customized skill bodies or the main prompt.

## The Options window

- Client settings save locally on the Mac. Endpoint changes affect where the
  client connects, not where Ubuntu binds.
- Backend settings are sent over **HTTPS**, not as arbitrary WebSocket commands.
  The administrative `X-JOI-Config-Key` is required.
- `host=0.0.0.0` is the Ubuntu listener binding, not a destination address.
- Save persists the remote file. Reload/restart is a separate operation and the
  API identifies which fields require which action.
- The backend schema supplies available fields. Additive settings must be
  reflected in Settings, the section map, validation, schema and tests.
- GPU selection is read-only in the API. Installer/service configuration must
  agree on the physical GPU. Do not use UI configuration to affect other GPUs.
- The language catalog drives language selection, not a hardcoded two-way switch.
  Active conversation language governs STT, LLM instructions and TTS normalization.

## Skills

Bundled defaults live in `services/voice-backend/skills`. Installed, editable
skills live on the backend under `~/.config/joi/skills`.

Each skill has `<slug>/SKILL.md` with flat front matter: `name`,
`description`, `version`. The catalog is `manifest.json`, not an environment
variable. The prompt contains a compact enabled-skill index; the agent calls
`read_skill` to load a relevant body.

The authoring workflow is:

1. Draft `.voice-agent/skill-drafts/<slug>/SKILL.md` using OpenTerminal write.
2. Call validate_skill, then create_skill or update_skill.
3. The bounded importer retrieves and validates that draft and atomically
   publishes it into the backend's persistent skill store.
4. Call reload_skills; check the new manifest revision and list_skills.

The OpenTerminal draft and installed backend copy are different files. A failed
reload preserves the prior active catalog. Skill text does not grant execution
permissions. Repository AGENTS.md governs development; it is not the assistant's
runtime system prompt or a skill loaded into every conversation.

## Speech profiles and migration

Higgs is the only production TTS provider; mock is a test mode. Legacy
Chatterbox code and pronunciation dictionaries have been removed. A config
still selecting Chatterbox fails explicitly; select openai_http before upgrading.
Unused Chatterbox fields are removed by the additive schema migration.

Optional language-specific voice profiles follow the example in
`services/voice-backend/config/tts-voice-profiles.example.json`. Use your own
recordings and exact transcripts; the file must exist on the speech host.
General language normalization is in language packs, never a brand dictionary.

# macOS client

Native SwiftUI application, macOS 13 or later. The client captures audio,
renders conversation Markdown and plays queued WAV segments; inference runs
on the configured backend.

From the repository root run `make build` or `make test-macos`.
The bundle is `apps/macos-client/dist/VoiceAgent.app`. Use `make package` for
a ZIP preserving its structure and executable permissions.

The release version comes from root `VERSION`. Builds are ad-hoc signed unless
`JOI_SIGN_IDENTITY` names an installed Developer ID identity. See
[release documentation](../../docs/RELEASES.md) for CI and notarization.

A private installer connection profile can be imported using:

```sh
python3 install.py client --profile /path/to/server.connection.json \
  --app /Applications/VoiceAgent.app
```

Import runs in the app's own signed executable, puts access keys in Keychain,
requests user trust for the private CA and updates the endpoint in
`~/.config/joi/client/settings.json`. Existing language/session preferences are
preserved. Restart an already running client after import.

See [architecture](../../docs/ARCHITECTURE.md),
[development](../../docs/DEVELOPMENT.md) and
[speech diagnostics](../../docs/SPEECH_PIPELINE.md).

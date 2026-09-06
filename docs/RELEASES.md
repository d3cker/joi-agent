# Builds and releases

## Identity and artifacts

Root VERSION is the shared semantic product release (for example 1.2.0).
The backend pyproject version can evolve independently; the app bundle gets
the product version at build time. release.json records component versions,
source revision, immutable model revisions and artifact SHA-256 values.

```sh
make setup
make test
make package
```

Output under outputs/release:

- Joi-VERSION-backend.tar.gz: allowlisted gateway source, installer and docs.
- Joi-VERSION-macOS-arm64.zip: native Apple Silicon app when built on arm64.
- Joi-VERSION-macOS-x86_64.zip: native Intel app when built on x86_64.
- release.json and SHA256SUMS.

Local make package builds the host architecture only. GitHub builds both.
Backend archives contain no private configuration, secrets, voices, model
weights, virtual environments or historical outputs. ZIP creation uses ditto
to preserve the .app layout and executable permissions. A source hash is used
when a checkout has no Git metadata; do not label such a build as a Git commit.

## GitHub Actions

.github/workflows/checks.yml runs Linux backend/installer tests and strict
macOS XCTest plus build on Apple Silicon and Intel. release.yml is triggered
by a v* tag and verifies it matches VERSION. It runs tests, builds both client
architectures and the backend archive, gathers checksums and publishes a GitHub
Release only after all jobs pass. The final publish job alone has contents:write.

Before first publication:

1. Create/push the Git repository to the intended GitHub repository.
2. Confirm distribution permission for application code, bundled assets and
   model licenses. This task does not choose a license on the owner's behalf.
3. Enable Actions and require the Tests workflow in branch protection.
4. Optionally configure Apple signing/notarization secrets below.
5. Update VERSION, review RELEASE_NOTES.md, commit and push a matching vVERSION
   tag. The workflow publishes a release; do not tag an unreviewed worktree.

Runner labels follow GitHub's [hosted-runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Actions require available runner capacity/minutes. Actual remote CI success
must be checked on GitHub; local packaging does not prove it.

## Signing and notarization

Without Apple credentials the app is **ad-hoc signed**, not Developer ID
notarized. Users do not need to compile it, but Gatekeeper can require explicit
approval through Privacy & Security. Do not tell users to disable Gatekeeper.

Optional repository secrets:

| Secret | Meaning |
| --- | --- |
| JOI_CERTIFICATE_P12 | Base64 Developer ID Application certificate/private key export |
| JOI_CERTIFICATE_PASSWORD | Password for the P12 export |
| JOI_SIGN_IDENTITY | Exact installed Developer ID Application signing identity |
| JOI_APPLE_ID | Apple account for notarization |
| JOI_APPLE_PASSWORD | App-specific notarization password |
| JOI_APPLE_TEAM | Apple developer team ID |

The CI helper creates a temporary keychain on the ephemeral runner, imports the
identity, builds with hardened runtime, submits to notarytool, verifies Accepted,
staples the app and packages only then. The keychain is deleted in an always
step. Partial notarization credentials or rejected notarization block release.
Never put credentials in workflow YAML, source or command-line examples.

## Hygiene

Use one scripts/release.py, not package_backend_1_2_0.sh or copied changelog
scripts. Keep meaningful docs by topic. Generated artifacts and historical test
recordings stay ignored/outside source. Additive wire telemetry must remain
compatible across client/backend component versions.

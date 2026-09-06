# Security boundaries

## Transport and identity

The backend serves TLS directly through Uvicorn: no reverse proxy, Bonjour or
local DNS. Install with a reachable server IP. The installer generates a private
CA and an RSA server certificate with that IP in subjectAltName. TLS 1.2 or
newer is required. No plaintext LAN listener or automatic downgrade exists;
plain HTTP/WS is reserved for loopback development and private TTS transport.

The client uses normal URLSession certificate and hostname validation. Importing
the installer profile adds the CA to the user's login Keychain SSL trust.
macOS may request confirmation. Do not add an accept-all certificate delegate.
A different IP requires an explicitly reissued certificate; this installer
refuses to silently change or rotate an existing identity.

The generated CA is private to this installation. Keep ca.key and server.key
on Ubuntu, mode 0600. The client receives only ca.crt and its fingerprint.
A trusted private CA can authenticate other certificates it signs: protect its
key and import connection profiles only from your authenticated SSH session.

## Authentication

- Client requests use Authorization: Bearer with the client-api-key.
- Administrative configuration routes require a separate X-JOI-Config-Key.
- Keys are never accepted from query strings.
- The public health endpoint is deliberately unauthenticated and must not leak
  secret values. Other session/tool/skill endpoints are authenticated.
- Backend keys live in private files; Mac keys live in Keychain.
- A connection profile contains access/admin keys. Transfer it only over SSH,
  do not commit or email it, and remove it securely when no longer needed.
- TLS does not provide per-user tenancy. This is a trusted-user/private-network
  agent, not an Internet-facing multi-tenant SaaS.

## Installer and host

The installer manages only joi-backend.service and joi-tts.service in the
installing user's systemd directory. It refuses unmanaged unit names or occupied
ports. It never stops Qwen, vLLM, ComfyUI or arbitrary GPU processes. It neither
installs nor updates NVIDIA drivers. Package installation is isolated in venvs;
optional sudo is limited to explicitly requested build prerequisites and linger.

SSH uses OpenSSH host verification, keys/agent or interactive password input.
No passwords are stored in arguments, environment files or connection profiles.
Establish the host key normally before using installer SSH mode.

## Tool execution and external services

SearXNG and OpenTerminal are outside this installation/security scope. Existing
LLM/tool endpoints may use their own network policy. Joi does not rewrite them
or claim their transport is encrypted. Restrict their network access separately.

With tools enabled, the user authorizes broad operations inside OpenTerminal.
Prompt injection remains possible; tool and fetched content are untrusted.
Skills are instructions, not a security boundary. Do not add backend-local
execute/read/write fallbacks. Never expose administrative keys to model context.

## Verification

Test TLS chain and IP mismatch, rejected anonymous HTTP and WS requests,
separate management authorization, no secrets in snapshots, malformed config,
and a failed installer rollback. A mock test does not prove model quality or
physical microphone capture. See [development](DEVELOPMENT.md).

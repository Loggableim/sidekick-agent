# Sidekick Known Issues

**Status:** development branch for v0.8.81.

## Session model convergence

`shared.sessions.Session` is the lightweight cross-surface session model.
`web.api.models.Session` adds WebUI runtime state. Both use the same storage
root and preserve unknown WebUI metadata during round-trips, but their object
shapes are not yet unified.

**Impact:** low for normal session use; changes to session persistence must
exercise both the CLI and WebUI contracts.

## Web route modernization

FastAPI is the only HTTP server. A portion of the WebUI API still uses a
handler-style route module through an in-process adapter while endpoints are
converted to native FastAPI handlers.

**Impact:** WebUI endpoint work must be tested against the FastAPI server and
the in-process route bridge.

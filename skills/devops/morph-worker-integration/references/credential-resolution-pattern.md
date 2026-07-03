# Credential Resolution Pattern — Morph Tools

Concrete worked example of the `check_fn` + fallback resolver pattern applied to Morph's two tools (`morph_apply`, `morph_codebase_search`).

## Problem

Morph tools were gated by `requires_env=["MORPH_API_KEY"]`, a **static gate** checked at tool-definition time (module import). If the key was only in `auth.json` (e.g. configured via WebUI Settings → Providers → Morph), the tools stayed invisible until Sidekick restarted. The `.env` file is only loaded at process start, so `os.environ` didn't have the key either.

## Solution

### 1. Replace `requires_env` with `check_fn`

```python
# Before:
registry.register(
    name="morph_apply",
    requires_env=["MORPH_API_KEY"],  # static — checked once at import
    ...
)

# After:
def _morph_available() -> bool:
    return _get_morph_api_key() is not None

registry.register(
    name="morph_apply",
    check_fn=_morph_available,  # dynamic — re-evaluates every ~30s (TTL cache)
    ...
)
```

### 2. Add a multi-source key resolver

```python
def _get_morph_api_key() -> str | None:
    """Resolve MORPH_API_KEY: env var first, then auth.json credential pool."""
    key = os.environ.get("MORPH_API_KEY")
    if key:
        return key
    try:
        from cli.config import get_sidekick_home
        auth = json.loads((get_sidekick_home() / "auth.json").read_text())
        pool = auth.get("credential_pool", {}).get("morph", [])
        if pool:
            return pool[0].get("access_token") or None
    except Exception:
        pass
    return None
```

### 3. Use the same resolver in the handler

```python
def _morph_apply_handler(args: dict, **kw) -> str:
    api_key = _get_morph_api_key()  # not os.environ.get("MORPH_API_KEY")
    if not api_key:
        return json.dumps({"error": "MORPH_API_KEY not set"})
    ...
```

## auth.json credential pool structure

```json
{
  "credential_pool": {
    "morph": [
      {
        "id": "morph-1",
        "label": "MORPH_API_KEY",
        "auth_type": "api_key",
        "priority": 0,
        "source": "auth.json",
        "access_token": "sk-...",
        "base_url": "https://api.morphllm.com/v1",
        "request_count": 0
      }
    ]
  }
}
```

## Files changed

| File | Change |
|------|--------|
| `tools/morph_apply.py` | Added `_get_morph_api_key()`, `_morph_available()`, replaced `requires_env` with `check_fn` |
| `tools/morph_warpgrep.py` | Same changes as above |
| `home/.env` | Added `MORPH_API_KEY=sk-...` |
| `home/auth.json` | Updated `credential_pool.morph[0].access_token` with real key |

## Pitfalls encountered

- **`read_file` with line numbers + `write_file` = corrupted JSON**: The `read_file` tool returns content with line-number prefixes (e.g. `   267|      {`). Piping this through `write_file` writes the line numbers into the file, breaking JSON parsing. Always strip line numbers before writing back, or use `patch` instead.
- **`.env` is only loaded at process start**: Setting `MORPH_API_KEY` in `.env` doesn't affect the running process. For the current session, you must also set `os.environ["MORPH_API_KEY"]` or rely on the `auth.json` fallback.
- **`auth.json` may have truncated placeholder tokens**: The credential pool entry may contain `"sk-fGt...Gir1"` (a 13-char placeholder) instead of the real 51-char key. This happens when the key was entered via a UI that truncated it. Verify the actual key length.

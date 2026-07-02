# MiniMax Token Plan MCP — Sidekick Integration

> Adds the [MiniMax Token Plan MCP server](https://platform.minimax.io/docs/guides/token-plan-mcp-guide)
> to Sidekick so `web_search` and `understand_image` become first-class tools in
> every Space (Nova, Aquaropa, etc.) and on every platform (CLI, Telegram,
> Discord, WebUI …).

## What you get

| MCP tool           | What it does                                            |
| ------------------ | ------------------------------------------------------- |
| `web_search`       | Real-time web search with suggestions                  |
| `understand_image` | Image analysis (URL or local path, JPEG/PNG/GIF/WebP, max 20 MB) |

Both tools are registered into Sidekick's tool registry under toolset `minimax`
and become available wherever the `mcp` toolset (or any preset that includes
it) is enabled.

## Prerequisites

1. **`uvx` on `PATH`** — install once:
   - **Windows** (PowerShell):
     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```
   - **macOS / Linux**:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
   Verify: `uvx --version` (Sidekick installs on this machine already pass).

2. **MiniMax Subscription Key** — get one from
   <https://platform.minimax.io/user-center/billing/token-plan>.
   It needs an active Token Plan seat (or purchased credits) to call the
   paid `web_search` and `understand_image` endpoints.

## One-command install

```bash
sidekick mcp add minimax --preset minimax-token-plan
```

This writes the following block to `~/.sidekick/config.yaml`:

```yaml
mcp_servers:
  minimax:
    command: uvx
    args:
      - -y
      - minimax-coding-plan-mcp
```

…and then prompts you for the required env vars:

| Variable                    | Required | Default                  | Purpose                                                                 |
| --------------------------- | -------- | ------------------------ | ----------------------------------------------------------------------- |
| `MINIMAX_API_KEY`           | ✅       | —                        | Your MiniMax Subscription Key                                           |
| `MINIMAX_API_HOST`          | ❌       | `https://api.minimax.io` | API endpoint; change only for self-hosted/private deployments           |
| `MINIMAX_API_RESOURCE_MODE` | ❌       | `url`                    | `url` (return image URLs) or `local` (write outputs to disk)            |
| `MINIMAX_MCP_BASE_PATH`     | only if `local` | —              | Directory for cached image outputs (must exist, must be writable)       |

The prompt uses Sidekick's standard `cli/env_loader.py` so values land in
`~/.sidekick/.env` with file permissions `0600` on Unix and ACL-locked on
Windows — the key never touches the YAML config in plaintext.

## Verify

```bash
sidekick mcp test minimax
```

Expected output (truncated):

```
✓ Connected to MCP server 'minimax'
✓ Discovered 2 tools:
   - web_search        Performs web searches based on search queries …
   - understand_image  Performs image understanding and analysis …
```

Then enable the toolset on the platform you use:

```bash
sidekick tools enable minimax --platform sidekick-cli
sidekick tools enable minimax --platform sidekick-telegram
```

(Use `sidekick tools list` to see which platforms currently have it on.)

## Manual configuration

If you prefer to edit `config.yaml` by hand:

```yaml
mcp_servers:
  minimax:
    command: uvx
    args: ["-y", "minimax-coding-plan-mcp"]
    env:
      MINIMAX_API_KEY: "your-key-here"   # or use ${MINIMAX_API_KEY} indirection
      # MINIMAX_API_HOST: https://api.minimax.io
      # MINIMAX_API_RESOURCE_MODE: url
      # MINIMAX_MCP_BASE_PATH: /tmp/minimax-mcp-cache
    timeout: 120         # per-tool-call timeout in seconds
    connect_timeout: 60  # initial connection timeout
```

…and add `minimax` to the `toolsets` list of any platform you want to expose
the tools on:

```yaml
sidekick:
  toolsets:
    sidekick-cli:
      - minimax
      - web
      - terminal
      # …
```

## How it works under the hood

Sidekick already has a first-class MCP client in `tools/mcp_tool.py` that
supports stdio transport, reconnection, error sanitization, sampling, and
per-server timeouts. The Token Plan MCP server publishes itself as a
[FastMCP](https://github.com/jlowin/fastmcp) stdio server, which is a perfect
match — no HTTP/SSE plumbing needed.

This PR adds:

1. A preset entry (`minimax-token-plan`) to `_MCP_PRESETS` in
   `cli/mcp_config.py` so `sidekick mcp add --preset` knows the package
   layout and can prompt for the right env vars.
2. A static toolset entry (`minimax`) in `toolsets.py` so `resolve_toolset("minimax")`
   returns a stable handle and the tools appear in `sidekick tools list`
   even before the MCP server is connected (tools populate dynamically once
   the MCP client registers them).

No changes to `tools/mcp_tool.py` are required — the existing client discovers
and exposes the tools automatically.

## Troubleshooting

| Symptom                                            | Fix                                                                                                          |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `spawn uvx ENOENT`                                 | `uvx` is not on PATH; reinstall with the Astral installer above and reopen the shell.                       |
| `MINIMAX_API_KEY environment variable is required`  | Key not set; rerun `sidekick mcp configure minimax` and paste the Subscription Key.                          |
| `HTTP 401 Unauthorized`                            | Key invalid or expired; regenerate at <https://platform.minimax.io/user-center/billing/token-plan>.         |
| `HTTP 429 Too Many Requests`                       | Rate-limited by MiniMax; raise `timeout` and retry, or upgrade the Token Plan seat.                          |
| Tool `web_search` / `understand_image` not in /mcp  | MCP server didn't register; check `sidekick mcp test minimax` and the stderr log in `~/.sidekick/logs/`.    |
| `understand_image` returns `local` mode error      | Set `MINIMAX_MCP_BASE_PATH` to an existing writable directory, or switch `MINIMAX_API_RESOURCE_MODE=url`.    |

## Cost note

`web_search` and `understand_image` both consume Token Plan credits. Each call
is metered by MiniMax; see the Token Plan page for current per-call pricing.
Add `minimax` only to the platforms that actually need web/image access
(e.g. enable for `sidekick-cli` and `sidekick-telegram` but leave
`sidekick-acp` and `sidekick-api-server` off if they don't).
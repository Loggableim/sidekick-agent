---
name: morph-worker-integration
description: >
  Nutze Morph als Worker-Netzwerk für Sidekick — Fast Apply (10,500 tok/s)
  ersetzt patch, WarpGrep ersetzt search_files, Compact komprimiert Context,
  Router routet Prompts. Alle über einen OpenAI-kompatiblen Endpoint.
trigger:
  - "morph anwenden"
  - "morph worker"
  - "fast apply"
  - "warpgrep"
  - "codebase search"
  - "context compression"
  - "morph router"
  - "edit file with morph"
---

# Morph Worker Integration

## Architektur

Morph ist **kein Provider** im klassischen Sinne — es ist ein Worker-Netzwerk
aus spezialisierten Sub-Agents und Fast-Inference-Modellen, die über einen
einzigen OpenAI-kompatiblen Endpoint (`https://api.morphllm.com/v1`) erreichbar
sind. Alle Produkte teilen sich denselben API-Key (`MORPH_API_KEY`).

```
┌─────────────────────────────────────────────────────────┐
│                    Sidekick Agent Loop                    │
│                                                           │
│  1. Router klassifiziert Prompt (easy/medium/hard)        │
│  2. WarpGrep sucht Codebase (~6s)                         │
│  3. Fast Apply editiert Dateien (10,500 tok/s)            │
│  4. Compact komprimiert Context (33,000 tok/s)             │
│  5. Fast Models laufen den Haupt-Loop                     │
└─────────────────────────────────────────────────────────┘
```

## Setup

### 1. API-Key setzen

```bash
# In .env oder config.yaml
MORPH_API_KEY=sk-...
```

Der Key wird automatisch in `auth.json` registriert (Settings → Providers → Morph).

**Key-Resolution (seit Juli 2026):** Die Tools checken zuerst `os.environ["MORPH_API_KEY"]`,
dann fallback auf `auth.json` → `credential_pool.morph[0].access_token`. Du musst den
Key also nicht zwingend in der `.env` haben — wenn er in `auth.json` steht (z.B. via
WebUI Settings → Providers → Morph eingegeben), funktionieren die Tools trotzdem.
`requires_env` wurde durch `check_fn=_morph_available` ersetzt, sodass die Tools auch
ohne Neustart sichtbar werden, sobald ein Key verfügbar ist.

Die Morph-Tools (`morph_apply`, `morph_codebase_search`) sind per `requires_env=["MORPH_API_KEY"]` auf `os.environ["MORPH_API_KEY"]` **gated**. Wenn der Key nur in `auth.json` unter `credential_pool.morph` liegt, aber nicht als Umgebungsvariable gesetzt ist, erscheinen die Tools **gar nicht erst** in der Tool-Liste des Agents.

**Richtiges Vorgehen:**
1. Key in `.env` setzen: `MORPH_API_KEY=sk-...`
2. `.env` wird automatisch von `cli/env_loader.py` geladen → `os.environ` wird gesetzt
3. Tools erscheinen im nächsten Session-Start automatisch
4. Der Key wird zusätzlich in `auth.json` registriert (Settings → Providers → Morph) — das ist ein netter Nebeneffekt, aber **nicht ausreichend** für die Tool-Gating.

**Key aus auth.json in .env übertragen (wenn er dort schon liegt):**
```bash
# Token aus auth.json auslesen
python -c "import json; d=json.load(open('C:/sidekick/home/auth.json')); print(d['credential_pool']['morph'][0]['access_token'])"
# → Ausgabe in .env schreiben: MORPH_API_KEY=sk-...
```

Die Morph-Tools (`morph_apply`, `morph_codebase_search`) sind per `check_fn` auf `os.environ["MORPH_API_KEY"]` **gated**. Wenn der Key nur in `auth.json` unter `credential_pool.morph` liegt, aber nicht als Umgebungsvariable gesetzt ist, erscheinen die Tools **gar nicht erst** in der Tool-Liste des Agents.

**Richtiges Vorgehen:**
1. Key in `.env` setzen: `MORPH_API_KEY=sk-...`
2. `.env` wird automatisch von `cli/env_loader.py` geladen → `os.environ` wird gesetzt
3. Tools erscheinen im nächsten Session-Start automatisch
4. Der Key wird zusätzlich in `auth.json` registriert (Settings → Providers → Morph) — das ist ein netter Nebeneffekt, aber **nicht ausreichend** für die Tool-Gating.

### 2. Verfügbare Tools

| Tool | Beschreibung | Ersetzt |
|------|-------------|---------|
| `morph_apply` | Fast Apply — Edit-Snippets mergen | `patch` (bei komplexen Edits) |
| `morph_codebase_search` | WarpGrep — semantische Code-Suche | `search_files` (bei semantischen Queries) |

### 3. Verfügbare Modelle

| Modell-ID | Speed | Context | Use Case |
|-----------|-------|---------|----------|
| `morph-qwen35-397b` | ~180 tok/s | 262k | Haupt-Loop, Reasoning |
| `morph-glm52-744b` | ~80 tok/s | 1M | Große Context-Fenster |
| `morph-minimax3-428b` | ~90 tok/s | 256k | Coding-Aufgaben |
| `morph-dsv4flash` | ~150 tok/s | 1M | Schnelle Antworten, günstig |
| `morph-qwen36-27b` | ~100 tok/s | 131k | Leichte Aufgaben |
| `morph-v3-fast` | 10,500+ tok/s | — | Fast Apply (nicht für Chat) |
| `morph-v3-large` | 5,000+ tok/s | — | Fast Apply hohe Genauigkeit |
| `morph-compactor` | 33,000 tok/s | — | Context-Kompression |
| `morph-warp-grep-v2.1` | ~6s/Query | — | Codebase Search |

## Workflow: Morph als Worker nutzen

### Pattern 1: Codebase Search + Edit (der häufigste Fall)

```python
# 1. Suche mit WarpGrep
result = morph_codebase_search(query="Find auth middleware implementation")
# → liefert Dateipfade + Line-Ranges + Content

# 2. Lese die relevante Datei
content = read_file(path="src/auth/middleware.py")

# 3. Editiere mit Fast Apply (lazy edit snippet)
morph_apply(
    target_file="src/auth/middleware.py",
    instructions="Add rate limiting to the auth middleware",
    code_edit="""
# ... existing code ...
from ratelimit import rate_limit
# ... existing code ...
@rate_limit(max_requests=100, window_seconds=60)
async def authenticate(request):
    # ... existing code ...
"""
)
```

**Wichtig:** Fast Apply arbeitet mit **lazy edit snippets** — du zeigst NUR
die geänderten Zeilen, mit `// ... existing code ...` (oder `# ... existing code ...`
für Python) als Platzhalter für unveränderte Abschnitte. Morph merged das
automatisch. Das ist schneller und genauer als `patch` oder Full-File-Rewrites.

### Pattern 2: Context Compression (vor langen LLM-Calls)

```yaml
# config.yaml
auxiliary:
  compression:
    provider: morph
    model: morph-compactor
```

Oder direkt via API:
```python
from openai import OpenAI
client = OpenAI(api_key="MORPH_API_KEY", base_url="https://api.morphllm.com/v1")
response = client.chat.completions.create(
    model="morph-compactor",
    messages=[{"role": "user", "content": chat_history}],
)
compressed = response.choices[0].message.content
```

Compact arbeitet mit `keepContext`-Tags — alles in `<keepContext>` überlebt
die Kompression unverändert:
```
<keepContext>
// CRITICAL: Auth middleware
function authenticate(req, res, next) { ... }
</keepContext>
```

### Pattern 3: Router (Prompt-Klassifikation vor Modell-Wahl)

```bash
curl -s -X POST "https://api.morphllm.com/v1/router/classify" \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": "Add error handling to this function", "classes": ["difficulty", "ambiguity", "domain"]}'
```

Returns:
```json
{
  "classifications": {
    "difficulty": { "label": "easy", "confidence": 0.93 },
    "ambiguity": { "label": "low", "confidence": 0.88 },
    "domain": { "label": "coding", "confidence": 0.91 }
  }
}
```

Nutze das um zu entscheiden: einfache Prompts → günstiges Modell (morph-dsv4flash),
komplexe → starkes Modell (morph-qwen35-397b).

### Pattern 4: Fast Models als Haupt-Provider

```yaml
# config.yaml
model:
  provider: morph
  model: morph-qwen35-397b
```

Oder für spezifische Tasks:
```yaml
auxiliary:
  compression:
    provider: morph
    model: morph-compactor
  web_extract:
    provider: morph
    model: morph-dsv4flash
```

## Wann Morph statt Sidekick-Builtins?

| Task | Sidekick Builtin | Morph Worker | Wann Morph? |
|------|-----------------|--------------|-------------|
| Datei editieren | `patch` | `morph_apply` | Komplexe Edits, viele Änderungen, 10x schneller |
| Code suchen | `search_files` | `morph_codebase_search` | Semantische Queries ("wo ist auth implementiert?") |
| Context komprimieren | Eingebauter Compressor | `morph-compactor` | Sehr große Contexts (>50K tokens) |
| Prompt routen | — | Router API | Vor Modell-Wahl, um Kosten zu sparen |

## Wann NICHT Morph?

- **Einfache Edits** (1-2 Zeilen ändern) → `patch` ist schneller (kein API-Call)
- **Regex-Suche** → `search_files` (ripgrep lokal, kein API-Call nötig)
- **Kleine Contexts** (<10K tokens) → eingebauter Compressor reicht

## Pitfalls

1. **Fast Apply braucht `// ... existing code ...` Marker.** Ohne die Marker
   behandelt Morph fehlende Abschnitte als Löschung. Immer im Prompt erwähnen.
2. **WarpGrep braucht `ripgrep` lokal.** Ohne `rg` auf PATH schlägt die Suche
   fehl. `winget install BurntSushi.ripgrep.MSVC` auf Windows.
3. **Compact ohne `query`-Parameter** macht generische Kompression. Immer
   einen Query mitgeben: `"Was ist für den nächsten LLM-Call relevant?"`
4. **Router ist ein Klassifikator, kein LLM.** ~50ms, $0.005/Request. Nicht
   für Chat geeignet — nur für Pre-Call-Routing.
5. **Tools sind gated auf `MORPH_API_KEY`.** Ohne gesetzten Key erscheinen
   `morph_apply` und `morph_codebase_search` nicht in der Tool-Liste.
   **Seit Juli 2026:** Die Tools nutzen `check_fn` statt `requires_env`,
   sodass der Key aus `os.environ` **oder** `auth.json` → `credential_pool.morph`
   aufgelöst wird. Die Tools werden dynamisch sichtbar (~30s TTL), kein
   Sidekick-Neustart nötig.
6. **`auth.json` kann abgeschnittene Keys enthalten.** Wenn der Key über die
   WebUI Settings → Providers eingegeben wurde, kann `auth.json` den Key
   maskiert speichern (`sk-fGt...Gir1` statt dem vollen 51-Zeichen-Key).
   In dem Fall den Key direkt in die `.env` schreiben oder via `sidekick config set`
   setzen. Prüfen: `python -c "import json; d=json.load(open('C:/sidekick/home/auth.json')); print(len(d['credential_pool']['morph'][0]['access_token']))"` — sollte 51 sein, nicht 13.

## Related

- `provider-integration-audit` — class-level skill for auditing/fixing provider integrations
- `provider-integration-audit/references/morph-integration-worked-example.md` — full worked example of this integration with per-file checklist and design decisions
- `sidekick-tool-development` — pattern for adding new tools to Sidekick (registry, toolset, env gating)
- `references/credential-resolution-pattern.md` — concrete worked example of the `check_fn` + `auth.json` fallback resolver pattern applied to Morph tools

## Verification

```bash
# Test: Morph API erreichbar?
curl -s "https://api.morphllm.com/v1/models" \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  | jq '.data[].id' | head -10

# Test: Fast Apply
# Siehe tools/morph_apply.py

# Test: WarpGrep
# Siehe tools/morph_warpgrep.py
```

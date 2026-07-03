# Morph Worker Integration

> Nutze Morph als Worker-Netzwerk für Sidekick — Fast Apply (10,500 tok/s) für
> Datei-Edits, WarpGrep für semantische Code-Suche, Compactor für Context-Kompression.

## Architektur

Morph ist **kein Provider** im klassischen Sinne — es ist ein Worker-Netzwerk
aus spezialisierten Sub-Agents und Fast-Inference-Modellen, die über einen
einzigen OpenAI-kompatiblen Endpoint (`https://api.morphllm.com/v1`) erreichbar
sind. Alle Produkte teilen sich denselben API-Key (`MORPH_API_KEY`).

```
┌─────────────────────────────────────────────────────────┐
│                    Sidekick Agent Loop                    │
│                                                           │
│  1. WarpGrep sucht Codebase (~6s)                         │
│  2. Fast Apply editiert Dateien (10,500 tok/s)            │
│  3. Compactor komprimiert Context (33,000 tok/s)           │
│  4. Router klassifiziert Prompts (~50ms)                  │
└─────────────────────────────────────────────────────────┘
```

## Setup

### 1. API-Key setzen

```bash
# In .env
MORPH_API_KEY=sk-...
```

Der Key wird automatisch in `auth.json` registriert (Settings → Providers → Morph).
Die Tools nutzen `check_fn` — sie erscheinen dynamisch in der Tool-Liste, sobald
ein Key verfügbar ist. Kein Sidekick-Neustart nötig.

### 2. Verfügbare Tools

| Tool | Beschreibung | Ersetzt |
|------|-------------|---------|
| `morph_apply` | Fast Apply — Edit-Snippets mergen (Backup + Syntax-Validierung + Diff) | `patch` (bei komplexen/semantischen Edits) |
| `morph_codebase_search` | WarpGrep — semantische Code-Suche (cross-platform) | `search_files` (bei semantischen Queries) |
| `morph_compress` | Compactor — Context-Kompression mit `keepContext`-Tags | Manuelles Zusammenfassen |

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
automatisch.

**Sicherheit:** `morph_apply` erstellt automatisch ein `.bak`-Backup vor dem Edit.
Bei Syntax-Fehler (Python, JSON, YAML, TOML) wird automatisch zurückgerollt.
Bei Erfolg wird das Backup gelöscht. Der Output enthält ein Unified Diff.

### Pattern 2: Context Compression

```python
# Direkt via morph_compress Tool
result = morph_compress(
    text=long_conversation,
    query="What is relevant for debugging the auth middleware?"
)
# → compressed text, original_size, compressed_size, ratio
```

Compact arbeitet mit `keepContext`-Tags — alles in `<keepContext>` überlebt
die Kompression unverändert:

```
<keepContext>
// CRITICAL: Auth middleware
function authenticate(req, res, next) { ... }
</keepContext>
```

Texte unter 2000 Zeichen werden automatisch übersprungen (der API-Call lohnt
sich erst bei größeren Contexts).

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

## Benchmark-Ergebnisse

### Chat (Prompt ~2000 Tokens)

| Metrik | Ollama Cloud (deepseek-v4-flash) | Morph (morph-dsv4flash) |
|--------|----------------------------------|------------------------|
| TTFT | **2.506s** | 7.408s |
| Total | **2.507s** | 7.410s |

→ **Ollama Cloud ist 66% schneller** für Chat. Morphs Chat-Modelle sind nicht
der Fokus — die Stärke liegt in den spezialisierten Workern.

### Fast Apply (5 Edits in 432-Zeilen-Datei)

| Metrik | `patch` (5 Aufrufe) | `morph_apply` (1 Aufruf) |
|--------|---------------------|--------------------------|
| Zeit | **0.059s** | 1.885s |
| API-Calls | 5 | 1 |
| Erfolg | 5/5 | 5/5 |

→ **`patch` ist 97% schneller** für einfache Textersetzungen. Der API-Call-Overhead
(~1.8s) frisst den Speed-Vorteil.

### Wann Morph Fast Apply trotzdem gewinnt

| Szenario | `patch` | `morph_apply` |
|----------|---------|---------------|
| 5 einfache Textersetzungen | **0.06s** ✓ | 1.9s |
| 1 Edit in 5000-Zeilen-Datei | ~0.05s ✓ | 1.9s |
| **Semantischer Edit** ("ändere alle Error-Handling-Blöcke") | ❌ Mehrere Patches + manuelle Suche | **1 API-Call** ✓ |
| **Edit über 10 Dateien** | 10x patch = 0.5s | 1 Aufruf = 1.9s ✓ |
| **Fuzzy Edit** (weißt nicht genau wo, nur was) | ❌ Musst erst suchen | **1 API-Call** ✓ |
| **Lazy Edit** ("füg Rate-Limiting in fetch ein") | ❌ Musst exakte Zeilen kennen | **1 API-Call** ✓ |

## Wann Morph statt Sidekick-Builtins?

| Task | Sidekick Builtin | Morph Worker | Wann Morph? |
|------|-----------------|--------------|-------------|
| Datei editieren | `patch` | `morph_apply` | Semantische/Fuzzy-Edits, Edits über mehrere Dateien |
| Code suchen | `search_files` | `morph_codebase_search` | Semantische Queries ("wo ist auth implementiert?") |
| Context komprimieren | Eingebauter Compressor | `morph_compress` | Sehr große Contexts (>50K tokens) |
| Prompt routen | — | Router API | Vor Modell-Wahl, um Kosten zu sparen |

## Wann NICHT Morph?

- **Einfache Edits** (1-2 Zeilen ändern) → `patch` ist schneller (kein API-Call)
- **Regex-Suche** → `search_files` (ripgrep lokal, kein API-Call nötig)
- **Kleine Contexts** (<2000 chars) → `morph_compress` überspringt automatisch
- **Chat** → Morphs Chat-Modelle sind 66% langsamer als Ollama Cloud

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
   die Morph-Tools nicht in der Tool-Liste. Die Tools nutzen `check_fn` —
   Key aus `os.environ` **oder** `auth.json` → `credential_pool.morph`.
6. **`auth.json` kann abgeschnittene Keys enthalten.** Wenn der Key über die
   WebUI Settings → Providers eingegeben wurde, kann `auth.json` den Key
   maskiert speichern. Prüfen: `len(access_token)` sollte 51 sein, nicht 13.
7. **`morph_apply` erstellt `.bak` Backup vor Edit.** Bei Syntax-Fehler wird
   automatisch zurückgerollt. Bei Erfolg wird das Backup gelöscht.
8. **`morph_compress` überspringt Texte < 2000 Zeichen.** Der API-Call lohnt
   sich erst bei größeren Contexts.
9. **`morph_apply` ist für lokale Textersetzungen langsamer als `patch`.**
   Der API-Call-Overhead (~1.8s) macht ihn nur bei semantischen Edits,
   Fuzzy-Edits oder Edits über mehrere Dateien lohnenswert.

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

# Test: Compactor
# Siehe tools/morph_compress.py
```

## Implementierte Tools

| Datei | Beschreibung |
|-------|-------------|
| `tools/morph_apply.py` | Fast Apply mit Backup, Syntax-Validierung, Diff-Output |
| `tools/morph_warpgrep.py` | WarpGrep mit cross-platform `os.walk` (statt `find`) |
| `tools/morph_compress.py` | Compactor mit `keepContext`-Tags, Auto-Skip bei kleinen Texten |
| `toolsets.py` | Alle 3 Tools im Core-Toolset registriert |

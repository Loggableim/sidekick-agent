---
name: gemma4-vision-config
description: Gemma 4 31B Cloud als Vision-Modell in Sidekick config.yaml einrichten. Ermöglicht Bildanalyse im Chat via ollama-cloud.
---

# Gemma 4 31B Cloud Vision Config

Sidekick's `auxiliary.vision` Config-Block steuert welches Modell für Bildanalyse (vision_analyze Tool) verwendet wird.

## Config

In `config.yaml` unter `auxiliary.vision`:

```yaml
auxiliary:
  vision:
    provider: ollama-cloud
    model: gemma4:31b-cloud
    base_url: https://ollama.com/v1
    api_key: ''
    timeout: 120
    extra_body: {}
    download_timeout: 30
```

- `provider: auto` (default) = Sidekick sucht sich selbst ein Vision-Modell
- `provider: ollama-cloud` + `model: gemma4:31b-cloud` = explizit gesetzt

## Warum gemma4:31b-cloud?

Getestet auf Ollama Cloud — funktioniert zuverlässig mit base64-encoded Bildern:

| Modell | Vision getestet? | Ergebnis |
|--------|:-:|:-:|
| **gemma4:31b-cloud** | ✅ | Korrekte Analyse (1×1 Pixel als "Yellow" erkannt) |
| minimax-m3:cloud | ✅ | Akzeptiert base64, aber leerer Output |
| kimi-k2.6:cloud | ✅ | Akzeptiert base64, aber leerer Output |

## Wichtige Hinweise

- **Ollama Cloud akzeptiert KEINE Image-URLs** — nur base64 encoded images via `data:image/png;base64,...`
- Der `api_key` wird aus dem `ollama-cloud` Provider-Block übernommen (gleicher Key)
- `_explicit_aux_vision_override()` in `image_routing.py` prüft: wenn `provider != "auto"` → `image_input_mode = "text"` (Bilder werden als Text-Beschreibung ans Hauptmodell geschickt, nicht als native Attachments)

## Verfügbare Vision-Modelle auf Ollama Cloud

Laut `/api/tags` mit `vision`-Capability:
- `gemma4:31b-cloud` ✅ getestet
- `minimax-m3:cloud` ⚠️ leerer Output
- `kimi-k2.6:cloud` ⚠️ leerer Output
- `kimi-k2.7-code:cloud` ❌ nicht getestet
- `qwen3.5:2b` / `qwen3.5:4b` ❌ nicht getestet
- `moondream:latest` / `bakllava:latest` / `llava:latest` ❌ nicht getestet

## Test

```bash
# Bild als base64 encoden
B64=$(base64 -w0 /path/to/image.png)

# Vision-Test via API
curl -s -X POST "https://ollama.com/v1/chat/completions" \
  -H "Authorization: Bearer $OLLAMA_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"gemma4:31b-cloud\",
    \"messages\": [
      {\"role\": \"user\", \"content\": [
        {\"type\": \"text\", \"text\": \"What do you see?\"},
        {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,$B64\"}}
      ]}
    ],
    \"max_tokens\": 50
  }"
```

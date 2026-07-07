# Changelog

## [Unreleased]

### Fixed
- **Reasoning-effort copy in the WebUI now shows the intended brain glyph and separators** instead of `??`/`?`, so the reasoning picker toast and tooltip both read correctly.
- **Static WebUI placeholders in the composer, goal banner, sandbox toggle, agent wizard, and mail panels now use real icons and labels** instead of `?`/`??`, so hidden UI surfaces no longer fall back to question marks when opened.
- **Slash-command help, toasts, and list separators in `web/static/commands.js` now use the intended glyphs** instead of mojibake, so `/help`, `/skills`, `/personality`, `/exec`, and `/image` read correctly again.
- **The WebUI language selector now exposes proper menu ARIA state** and keeps `aria-expanded` in sync while opening, switching, and closing the dropdown. This removes the remaining accessibility regression in the new titlebar language menu.
- **Language selector options in the WebUI now render actual flags and labels** instead of `????`. The titlebar dropdown had placeholder text in `web/static/index.html`, so the menu was unreadable until this patch. The active locale indicator still updates through `i18n.js`, and the dropdown now has accessible `aria-label`s for each language.
- **Attachment-Dateilinks im WebUI-Chat zeigen jetzt den vollständigen Pfad** statt nur den Dateinamen. Uploads liefern `{path, name}`, aber `ui.js` verwendete `f.name` für die `/api/file/raw?path=…`-URL. Der Server (`safe_resolve`) benötigt den absoluten Pfad, um die Datei im Workspace zu finden. Jetzt wird `f.path` für die URL und `f.name` nur für das Anzeige-Label verwendet. ([#4226210](https://github.com/Loggableim/sidekick-agent/commit/4226210))

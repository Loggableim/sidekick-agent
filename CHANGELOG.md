# Changelog

## [Unreleased]

### Fixed
- **Attachment-Dateilinks im WebUI-Chat zeigen jetzt den vollständigen Pfad** statt nur den Dateinamen. Uploads liefern `{path, name}`, aber `ui.js` verwendete `f.name` für die `/api/file/raw?path=…`-URL. Der Server (`safe_resolve`) benötigt den absoluten Pfad, um die Datei im Workspace zu finden. Jetzt wird `f.path` für die URL und `f.name` nur für das Anzeige-Label verwendet. ([#4226210](https://github.com/Loggableim/sidekick-agent/commit/4226210))

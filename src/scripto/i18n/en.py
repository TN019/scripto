"""English catalog (the fallback: every key must exist here)."""

CATALOG: dict[str, str] = {
    "app.name": "Scripto",
    "app.tagline": "Local batch transcription and subtitle translation",
    "app.gui_placeholder": "The Scripto GUI arrives in milestone M5. Use `scripto-cli` in the meantime.",
    "cli.description": "Scripto command-line interface",
    "cli.info.help": "Show data locations and current settings",
    "cli.info.header": "Scripto {version}",
    "cli.info.config": "Config file: {path}",
    "cli.info.data_dir": "Data dir:    {path}",
    "cli.info.log_dir": "Logs dir:    {path}",
    "cli.info.language": "UI language: {value}",
    "cli.language.not_set": "(not set, defaulting to English)",
    "errors.ffmpeg_missing": "ffmpeg not found. Install it with: {hint}",
    "errors.ffmpeg_failed": "ffmpeg could not read {name}: {reason}",
    "errors.extract_timeout": "Audio extraction timed out after {seconds}s on {name}.",
    "errors.unknown_model": "Unknown whisper model: {model}",
    "errors.unknown_engine": "Unknown transcription engine: {engine}",
    "errors.no_engine": "No transcription engine is available. Run `uv sync` to install dependencies.",
}

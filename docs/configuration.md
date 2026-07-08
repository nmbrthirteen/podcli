# Configuration

## Environment variables

Copy `.env.example` to `.env`, or export these in your shell. `setup.sh` copies the file for you.

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `PODCLI_HOME` | managed folder (installed) or `./.podcli` (source) | Config home: knowledge, presets, assets, history, settings |
| `PODCLI_DATA` | managed folder (installed) or `./data` (source) | Runtime data: cache, working files, logs |
| `PODCLI_OUTPUT` | `./podcli-clips` (installed) or `$PODCLI_DATA/output` (source) | Where rendered clips are written |
| `PODCLI_QUALITY` | `high` | Render quality profile |
| `PODCLI_NO_UPDATE` | unset | Set to `1` to skip the update check. Same as `podcli config set update.auto off` |
| `PODCLI_LOG_LEVEL` | `info` | Logging verbosity |
| `PORT` | `3847` | Web studio port |
| `HF_TOKEN` | unset | Hugging Face token, required for speaker diarization |
| `PODCLI_ENV_FILE` | `./.env` | Path to an alternate `.env` |
| `PODCLI_BACKEND` | resolved | Override the Python backend directory |
| `PODCLI_PYTHON` | resolved | Override the Python interpreter |
| `FFMPEG_PATH` / `FFPROBE_PATH` | `ffmpeg` / `ffprobe` | Override the FFmpeg binaries |

Installed builds provision their own Python, Node, FFmpeg, and whisper.cpp, so the
override variables are only needed when running from source or debugging.

The Whisper model is chosen per run with `--model` (or `podcli setup --model`), not
through an environment variable.

## Config profiles

A portable bundle zips your config home. Cache and rendered clips are excluded.

```bash
podcli config export ~/backups/myshow.zip
podcli config import ~/backups/myshow.zip --home ~/.podcli-myshow --activate
podcli config status
```

To point at a config root without importing:

```bash
podcli config use ~/.podcli-myshow    # writes .podcli-home in the project
```

Settings live in the config home (`PODCLI_HOME`, tracked by the `.podcli-home`
marker). Heavy runtime files live under `PODCLI_DATA`. The marker file records
which config home is active; it does not replace either root.

MCP equivalent: `manage_config`.

## Upgrading from older layouts

Older releases kept the transcription cache under `project/.podcli/cache/` (now
`data/cache/`) and presets under `project/presets/` (now `.podcli/presets/`).
Migration runs automatically when legacy files are still present, across the CLI,
web UI, and MCP server. To preview or run it by hand:

```bash
podcli config migrate --dry-run   # preview only
podcli config migrate             # apply
```

## Transcript format

podcli auto-transcribes with Whisper, and it accepts an existing transcript with
`--transcript`. Drag-drop `.txt`, `.srt`, and `.vtt` work in the studio.

```
Speaker Name (00:00)
What they said goes here as plain text.

Another Speaker (00:45)
Their response text here.
```

The time offset field (default `-1s`) shifts every timestamp to sync with the audio.

## Knowledge base

`.md` files in `$PODCLI_HOME/knowledge/` teach the AI your show. The clip suggestion
engine reads them for voice rules and title formulas, and checks the episode database
so it does not resuggest a moment you already published.

PodStack ships 13 starter templates covering brand identity, voice and tone, the
episode database, shorts criteria, title formulas, description templates, thumbnail
guides, topics, workflow, and quick reference. Fill them in with your show's details.

Edit them in the studio under Knowledge, or through the `knowledge_base` MCP tool.

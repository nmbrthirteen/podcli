# podcli

AI-powered podcast clip generator. Takes a long-form podcast video, identifies viral moments, and exports upload-ready TikTok / YouTube Shorts (1080×1920, 9:16, burned captions, normalized audio).

## Features

- **Auto clip suggestion** — text heuristics + audio energy analysis for smarter scoring
- **Hardware-accelerated encoding** — auto-detects VideoToolbox (Mac), NVENC (NVIDIA), VAAPI, or falls back to CPU
- **Burned-in captions** — word-level timestamps with branded, hormozi, karaoke, or subtle styles
- **Smooth gradient overlay** — alpha-blended PNG overlay (no banding)
- **Smart cropping** — center crop or face detection
- **Transcript import** — paste `Speaker (MM:SS)` format, JSON, or drag-and-drop `.txt` files
- **Whisper transcription** — auto-transcribe with OpenAI Whisper (tiny → large)
- **CLI mode** — one-command processing: `./podcli process video.mp4 --top 5`
- **Preset system** — save and load named configurations per show
- **MCP server** — use as a Claude Desktop / Claude Code tool
- **Web UI** — single-page flow: input → suggest → review → export

---

## Prerequisites

| Tool | Install |
|------|---------|
| **Node.js** ≥ 18 | [nodejs.org](https://nodejs.org) |
| **Python** ≥ 3.10 | [python.org](https://python.org) |
| **FFmpeg** | `brew install ffmpeg` / `sudo apt install ffmpeg` |

---

## Quick Start

```bash
cd podcli
chmod +x setup.sh podcli
./setup.sh
```

This will:

1. Check system dependencies (Node, Python, FFmpeg)
2. Create a Python virtual environment and install packages
3. Install Node packages and build TypeScript
4. Launch the web UI at **http://localhost:3847**

### Setup options

```bash
./setup.sh              # full install + launch UI
./setup.sh --install    # install only
./setup.sh --ui         # launch UI only (skip install)
./setup.sh --mcp        # print MCP config
```

---

## Usage (CLI)

Process a video in one command — no UI needed:

```bash
# Basic: auto-transcribe + suggest top 5 clips + export
./podcli process video.mp4

# With existing transcript
./podcli process video.mp4 --transcript transcript.txt --top 5

# With a saved preset
./podcli process video.mp4 -t transcript.txt --preset myshow

# Full options
./podcli process video.mp4 \
  --transcript transcript.txt \
  --top 8 \
  --output ./clips \
  --caption-style branded \
  --crop center \
  --logo ~/logo.png \
  --time-adjust -1
```

### Presets

Save your settings so you don't reconfigure each time:

```bash
# Save a preset
./podcli presets save myshow --caption-style branded --logo ~/logo.png --top 5

# List presets
./podcli presets list

# Show preset details
./podcli presets show myshow

# Use a preset
./podcli process video.mp4 --preset myshow

# Delete a preset
./podcli presets delete myshow
```

Presets are stored in `~/.podcli/presets/`.

### System info

```bash
./podcli info
```

Shows detected encoder (VideoToolbox, NVENC, CPU), platform, and FFmpeg flags.

---

## Usage (Web UI)

1. **Set video** — drag-and-drop a video file or enter a local path
2. **Add transcript** — drag a `.txt` file, paste `Speaker (MM:SS)` text, or use Whisper auto-transcription
3. **Click "Generate Clips"** — podcli analyzes audio energy, parses transcript, and suggests clips
4. **Review** — toggle clips on/off, adjust settings (caption style, crop, logo)
5. **Export** — batch-renders all selected clips with hardware acceleration
6. **Preview / Retry / Download** — watch results inline, re-export individual clips

The encoder badge in the header shows which encoder is active (e.g., VIDEOTOOLBOX, NVENC, or CPU).

### Transcript format

```
Speaker Name (00:00)
What they said goes here as plain text.

Another Speaker (00:45)
Their response text here.
```

The time offset field (default: -1s) shifts all timestamps to sync with audio.

---

## Usage (MCP Server)

podcli is also a [Model Context Protocol](https://modelcontextprotocol.io) server — Claude can use it as a tool to create clips programmatically.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "podcli": {
      "command": "node",
      "args": ["/absolute/path/to/podcli/dist/index.js"],
      "env": {
        "PYTHON_PATH": "/absolute/path/to/podcli/venv/bin/python3"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add podcli -- node /absolute/path/to/podcli/dist/index.js
```

Or run `./setup.sh --mcp` to get the exact config with your paths filled in.

### Available MCP tools

| Tool | Description |
|------|-------------|
| `transcribe_podcast` | Transcribe audio with Whisper |
| `suggest_clips` | Get AI-suggested clip timestamps |
| `create_clip` | Render a single short clip |
| `batch_clips` | Render multiple clips in one go |

---

## Project Structure

```
podcli/
├── podcli                    # CLI entry point (shell wrapper)
├── setup.sh                  # one-command install & launch
├── package.json
├── tsconfig.json
├── .env.example
│
├── src/                      # TypeScript source
│   ├── index.ts              # MCP server entry (stdio)
│   ├── server.ts             # MCP tool definitions
│   ├── config/paths.ts       # path resolution
│   ├── models/index.ts       # shared types
│   ├── handlers/             # MCP tool handlers
│   │   ├── transcribe.handler.ts
│   │   ├── suggest-clips.handler.ts
│   │   ├── create-clip.handler.ts
│   │   └── batch-clips.handler.ts
│   ├── services/
│   │   ├── python-executor.ts   # spawns Python backend
│   │   └── file-manager.ts
│   └── ui/
│       ├── web-server.ts        # Express server + SSE + REST API
│       └── public/index.html    # React single-page UI
│
├── backend/                  # Python backend
│   ├── main.py               # stdin/stdout JSON dispatcher
│   ├── cli.py                # CLI entry point (argparse)
│   ├── presets.py             # preset save/load system
│   ├── requirements.txt
│   ├── services/
│   │   ├── transcription.py     # Whisper wrapper
│   │   ├── video_processor.py   # FFmpeg clip rendering (hw-accel)
│   │   ├── audio_analyzer.py    # RMS energy analysis for scoring
│   │   ├── encoder.py           # hardware encoder detection
│   │   ├── clip_generator.py    # full pipeline orchestration
│   │   ├── caption_renderer.py  # ASS subtitle generation
│   │   └── transcript_parser.py # Speaker (MM:SS) parser
│   └── config/
│       └── caption_styles.py    # ASS subtitle style presets
│
└── data/                     # runtime data (gitignored)
    ├── cache/transcripts/
    ├── working/uploads/
    ├── output/
    └── logs/
```

---

## Configuration

Copy `.env.example` to `.env` (setup.sh does this automatically):

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `base` | Whisper model size |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `PYTHON_PATH` | (venv) | Path to Python binary |
| `PODCLI_HOME` | `~/.podcli` | Data directory |
| `FFMPEG_PATH` | `ffmpeg` | Custom FFmpeg path |
| `LOG_LEVEL` | `info` | Logging verbosity |

---

## Caption Styles

| Style | Look |
|-------|------|
| **branded** | Gradient overlay + boxed word highlight + optional logo |
| **hormozi** | Bold pop-on text (Alex Hormozi style) |
| **karaoke** | Word-by-word highlight |
| **subtle** | Clean, minimal white text |

---

## License

MIT

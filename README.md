# podcli

AI-powered podcast clip generator. Takes a long-form podcast video, identifies viral moments, and exports upload-ready TikTok / YouTube Shorts (1080x1920, 9:16, burned captions, normalized audio).

## Features

- **Auto clip suggestion** — text heuristics + audio energy analysis
- **Burned-in captions** — 4 styles: branded (dark box highlight), hormozi (bold pop-on), karaoke (progressive highlight), subtle (clean bottom text)
- **Hardware-accelerated encoding** — auto-detects VideoToolbox (Mac), NVENC (NVIDIA), VAAPI, or CPU fallback
- **Smart cropping** — center crop or face detection (OpenCV)
- **Knowledge base** — drop `.md` files to give the AI context about your podcast, hosts, and style
- **Asset management** — register logos and videos by name for quick reuse
- **Clip history** — tracks all generated clips to avoid duplicates
- **Transcript import** — paste `Speaker (MM:SS)` format, JSON, or drag-and-drop `.txt` files
- **Whisper transcription** — auto-transcribe with OpenAI Whisper (tiny → large)
- **Preset system** — save and load named configurations per show
- **MCP server** — use as a Claude Desktop / Claude Code tool (7 tools)
- **Web UI** — single-page flow: input → suggest → review → export
- **CLI mode** — one-command processing: `./podcli process video.mp4 --top 5`

## Prerequisites

| Tool | Install |
|------|---------|
| **Node.js** >= 18 | [nodejs.org](https://nodejs.org) |
| **Python** >= 3.10 | [python.org](https://python.org) |
| **FFmpeg** | `brew install ffmpeg` / `sudo apt install ffmpeg` |

## Quick Start

```bash
git clone <repo-url> podcli
cd podcli
chmod +x setup.sh podcli
./setup.sh
```

This will:

1. Check system dependencies (Node, Python, FFmpeg)
2. Create a Python virtual environment and install packages
3. Install Node packages and build TypeScript
4. Create the local `.podcli/` data directory
5. Launch the web UI at **http://localhost:3847**

### Setup options

```bash
./setup.sh              # full install + launch UI
./setup.sh --install    # install only
./setup.sh --ui         # launch UI only (skip install)
./setup.sh --mcp        # print MCP config for Claude
```

## Usage

### Web UI

```bash
./setup.sh --ui
# → http://localhost:3847
```

1. **Set video** — drag-and-drop or enter a local path
2. **Add transcript** — drag a `.txt` file, paste `Speaker (MM:SS)` text, or auto-transcribe with Whisper
3. **Generate Clips** — analyzes audio energy + transcript to suggest viral moments
4. **Review** — toggle clips on/off, pick caption style, crop mode, logo
5. **Export** — batch-renders selected clips with hardware acceleration
6. **Preview / Download** — watch results inline, download individual clips

### CLI

```bash
# Auto-transcribe + suggest top 5 clips + export
./podcli process video.mp4

# With existing transcript
./podcli process video.mp4 --transcript transcript.txt --top 5

# Full options
./podcli process video.mp4 \
  --transcript transcript.txt \
  --top 8 \
  --caption-style branded \
  --crop center \
  --logo logo.png
```

### Presets

```bash
./podcli presets save myshow --caption-style branded --logo logo.png --top 5
./podcli presets list
./podcli process video.mp4 --preset myshow
```

### MCP Server (Claude integration)

podcli is a [Model Context Protocol](https://modelcontextprotocol.io) server — Claude can use it as a tool to create clips through conversation.

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "podcli": {
      "command": "node",
      "args": ["/path/to/podcli/dist/index.js"],
      "env": {
        "PYTHON_PATH": "/path/to/podcli/venv/bin/python3"
      }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add podcli -- node /path/to/podcli/dist/index.js
```

Run `./setup.sh --mcp` to get the exact config with your paths filled in.

#### MCP Tools

| Tool | Description |
|------|-------------|
| `transcribe_podcast` | Transcribe audio/video with Whisper |
| `suggest_clips` | Submit clip suggestions (includes duplicate check) |
| `create_clip` | Render a single short-form clip |
| `batch_create_clips` | Render multiple clips in one batch |
| `knowledge_base` | Read/write podcast context files |
| `manage_assets` | Register/list reusable assets (logos, videos) |
| `clip_history` | View previously created clips, check for duplicates |

## Knowledge Base

Drop `.md` files into `.podcli/knowledge/` to give the AI context about your podcast. The MCP server reads these before every request.

Suggested files:
- `podcast.md` — show name, format, episode structure
- `hosts.md` — host names, speaking styles
- `style.md` — preferred caption style, logo, colors
- `audience.md` — target audience, platform preferences
- `avoid.md` — topics or segments to skip

Manage via the web UI at `/knowledge.html` (drag & drop, inline editor) or through the `knowledge_base` MCP tool.

## Caption Styles

| Style | Look |
|-------|------|
| **branded** | Large bold text, dark box highlight on active word, gradient overlay, optional logo |
| **hormozi** | Bold uppercase pop-on text, yellow active word (Alex Hormozi style) |
| **karaoke** | Full sentence visible, words highlight progressively |
| **subtle** | Clean minimal white text at bottom |

## Project Structure

```
podcli/
├── podcli                    # CLI entry point
├── setup.sh                  # one-command install & launch
├── package.json
├── .env.example
│
├── src/                      # TypeScript
│   ├── index.ts              # MCP server entry (stdio)
│   ├── server.ts             # MCP tool definitions
│   ├── config/paths.ts
│   ├── models/index.ts
│   ├── handlers/             # MCP tool handlers
│   ├── services/
│   │   ├── python-executor.ts
│   │   ├── file-manager.ts
│   │   ├── asset-manager.ts
│   │   ├── clips-history.ts
│   │   ├── knowledge-base.ts
│   │   └── transcript-cache.ts
│   └── ui/
│       ├── web-server.ts     # Express server + API
│       └── public/           # Frontend (React SPA)
│
├── backend/                  # Python
│   ├── main.py               # stdin/stdout JSON dispatcher
│   ├── cli.py                # CLI entry point
│   ├── presets.py
│   ├── requirements.txt
│   ├── services/             # Whisper, FFmpeg, captions, etc.
│   └── config/
│       └── caption_styles.py
│
└── .podcli/                  # local data (gitignored)
    ├── assets/               # registered logos, videos
    ├── cache/transcripts/    # cached transcriptions
    ├── history/              # generated clip history
    ├── knowledge/            # .md context files for AI
    ├── output/               # rendered clips
    ├── presets/              # saved configurations
    └── working/              # temp files
```

## Configuration

Copy `.env.example` to `.env` (setup.sh does this automatically):

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `base` | Whisper model size (tiny, base, small, medium, large) |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `PYTHON_PATH` | (venv) | Path to Python binary |
| `PODCLI_HOME` | `.podcli/` | Data directory (relative to project root) |
| `FFMPEG_PATH` | `ffmpeg` | Custom FFmpeg path |
| `LOG_LEVEL` | `info` | Logging verbosity |

## Transcript Format

```
Speaker Name (00:00)
What they said goes here as plain text.

Another Speaker (00:45)
Their response text here.
```

The time offset field (default: -1s) shifts all timestamps to sync with audio.

## License

MIT

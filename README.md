<p align="center">
  <img src="public/podcli-icon.png" width="80" alt="podcli icon" />
</p>
<p align="center">
  <img src="public/podcli-logo-transparent.png" height="36" alt="podcli" />
</p>
<p align="center">
  AI-powered podcast content studio. Transcribe episodes, find viral moments, render upload-ready Shorts with burned captions — then generate titles, descriptions, thumbnails, and a full publish-ready content package. All from your terminal.
</p>

---

## What It Does

**podcli** takes a long-form podcast and turns it into a complete content operation:

```
Record episode
    ↓
Transcribe (Whisper, speaker detection)
    ↓
Find viral moments (audio energy + text heuristics)
    ↓
Render clips (9:16, captions, smart crop, normalized audio)
    ↓
Generate content package (titles, descriptions, thumbnails, SEO)    ← PodStack
    ↓
Publish with optimization checklist                                  ← PodStack
    ↓
Review performance                                                   ← PodStack
```

The first half is **video processing** — podcli's core engine. The second half is **content workflow** — powered by [PodStack](https://github.com/nmbrthirteen/podstack), a set of Claude Code slash commands that ship with podcli.

---

## How It Works (From a User's Perspective)

### 1. Drop in your episode

```bash
./setup.sh --ui
# → http://localhost:3847
```

Drag your video into the Web UI, or use the CLI:

```bash
./podcli process episode.mp4 --transcript transcript.txt --top 8
```

### 2. Get clips automatically

podcli analyzes your transcript + audio energy to find the best moments. It scores each one, suggests clips, and lets you toggle them on/off before rendering.

Clips come out as **upload-ready Shorts**: 1080x1920, 9:16 vertical, with burned-in captions, normalized audio, and your logo.

### 3. Generate the full content package

Open the project in **Claude Code** and run:

```
/prep-episode
```

This runs the [PodStack](https://github.com/nmbrthirteen/podstack) pipeline — a gstack-style workflow that gives you:

- **8-15 scored moments** with timestamps, categories, and reasoning
- **8 title options per clip** following your show's title spec (verified against 6 quality gates)
- **Ready-to-paste descriptions** with hooks, guest attribution, hashtags, SEO keywords
- **Thumbnail briefs** for both podcast (16:9) and shorts (9:16) formats
- **Brand review** that catches banned words, voice violations, and weak hooks
- **Publish checklist** covering pre-upload, at-publish, first-24-hours, and day 3-4 optimization

### 4. Publish and track

Run `/publish-checklist` when uploading. A week later, run `/retro-episode` with your YouTube Studio stats to see what worked and what to improve.

---

## The Two Halves

| | Video Engine (podcli core) | Content Workflow (PodStack) |
|---|---|---|
| **What** | Transcription, clip detection, rendering | Titles, descriptions, thumbnails, publishing |
| **How** | Python + FFmpeg + Whisper + OpenCV | Claude Code slash commands |
| **Interface** | Web UI, CLI, MCP tools | `/slash-commands` in Claude Code |
| **Output** | `.mp4` files ready to upload | Content packages ready to paste into YouTube |

Both halves share the same **knowledge base** (`.podcli/knowledge/`) — your show's brand, voice, title formulas, episode database, and style guide. Set it up once, everything stays on-brand.

---

## Features

### Video Processing
- **Auto clip suggestion** — text heuristics + audio energy analysis
- **Burned-in captions** — 4 styles: branded, hormozi, karaoke, subtle
- **Hardware-accelerated encoding** — VideoToolbox (Mac), NVENC (NVIDIA), VAAPI, CPU fallback
- **Smart cropping** — center crop or face detection (OpenCV)
- **Whisper transcription** — auto-transcribe with speaker detection (tiny → large)
- **Transcript import** — paste `Speaker (MM:SS)`, JSON, drag-drop `.txt` / `.srt` / `.vtt`

### Content Workflow (PodStack)
- **`/process-transcript`** — extract and score best moments from any transcript
- **`/generate-titles`** — 8 titles per clip with 6-point verification checklist
- **`/generate-descriptions`** — descriptions + hashtags + SEO keywords
- **`/plan-thumbnails`** — thumbnail text + designer briefs for both formats
- **`/review-content`** — paranoid brand check (banned words, voice, title rules)
- **`/prep-episode`** — full pipeline: transcript → publish-ready package
- **`/publish-checklist`** — pre/post-publish optimization
- **`/retro-episode`** — performance analysis after publishing

### Infrastructure
- **Knowledge base** — `.md` files that teach the AI your brand, voice, and style
- **Asset management** — register logos and videos for quick reuse
- **Clip history** — tracks everything to avoid duplicates
- **Preset system** — save named configurations per show
- **MCP server** — 7 tools for Claude Desktop / Claude Code integration
- **Web UI** — single-page flow at `localhost:3847`
- **CLI** — one-command processing: `./podcli process video.mp4 --top 5`

---

## Prerequisites

| Tool | Install |
|------|---------|
| **Node.js** >= 18 | [nodejs.org](https://nodejs.org) |
| **Python** >= 3.10 | [python.org](https://python.org) |
| **FFmpeg** | `brew install ffmpeg` / `sudo apt install ffmpeg` |
| **Claude Code** (optional) | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code) — needed for PodStack slash commands |

## Quick Start

```bash
git clone https://github.com/nmbrthirteen/podcli.git
cd podcli
chmod +x setup.sh podcli
./setup.sh
```

This will:

1. Check system dependencies (Node, Python, FFmpeg)
2. Create a Python virtual environment and install packages
3. Install Node packages and build TypeScript
4. Set up PodStack slash commands and knowledge base templates
5. Create the local `.podcli/` data directory
6. Launch the web UI at **http://localhost:3847**

### Setup options

```bash
./setup.sh              # full install + launch UI
./setup.sh --install    # install only
./setup.sh --ui         # launch UI only (skip install)
./setup.sh --mcp        # print MCP config for Claude
```

---

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

### Content Workflow (PodStack)

Open the project in Claude Code, then use slash commands:

```bash
# Full pipeline — transcript to publish-ready package
/prep-episode

# Individual steps
/process-transcript        # extract moments from a transcript
/generate-titles           # get 8 title options for a clip
/generate-descriptions     # get descriptions + hashtags
/plan-thumbnails           # get thumbnail briefs for your designer
/review-content            # brand and quality review
/publish-checklist         # pre/post-publish ops
/retro-episode             # performance analysis
```

Or just paste a transcript — Claude auto-detects the input and runs the right command.

---

## Knowledge Base

The knowledge base is what makes podcli understand *your* show. Drop `.md` files into `.podcli/knowledge/` and both the video engine and content workflow use them.

PodStack ships with **13 starter templates** that you fill in with your show's details:

| File | What It Teaches The AI |
|------|----------------------|
| `00-master-instructions.md` | Auto-detection rules, decision tree, quality gates |
| `01-brand-identity.md` | Show name, positioning, tagline, hosts, format |
| `02-voice-and-tone.md` | Voice fingerprint, banned words, the Coffee Test |
| `03-episodes-database.md` | Episode tracking, existing shorts (for dedup) |
| `04-shorts-creation-guide.md` | Moment types, selection criteria, extraction process |
| `05-title-formulas.md` | Title shapes, rules, templates by content type |
| `06-descriptions-template.md` | Description formulas, hashtag library, SEO keywords |
| `07-thumbnail-guide.md` | Layouts, brand colors, typography, visual specs |
| `08-topics-themes.md` | Core topics, cross-cutting themes, audience map |
| `09-content-workflow.md` | End-to-end workflow phases, handoff specs |
| `10-internal-processing.md` | Auto-execution rules, internal quality gates |
| `11-inspiration-channels.md` | Reference channels, viral hooks, hybrid formulas |
| `12-quick-reference.md` | Copy-paste hooks, hashtags, CTAs, checklists |

Manage via the web UI at `/knowledge.html` (drag & drop, inline editor) or through the `knowledge_base` MCP tool.

---

## MCP Server (Claude Integration)

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

### MCP Tools

| Tool | Description |
|------|-------------|
| `transcribe_podcast` | Transcribe audio/video with Whisper + speaker detection |
| `suggest_clips` | Submit clip suggestions (includes duplicate check) |
| `create_clip` | Render a single short-form clip |
| `batch_create_clips` | Render multiple clips in one batch |
| `knowledge_base` | Read/write podcast context files |
| `manage_assets` | Register/list reusable assets (logos, videos) |
| `clip_history` | View previously created clips, check for duplicates |

---

## Caption Styles

| Style | Look |
|-------|------|
| **branded** | Large bold text, dark box highlight on active word, gradient overlay, optional logo |
| **hormozi** | Bold uppercase pop-on text, yellow active word (Alex Hormozi style) |
| **karaoke** | Full sentence visible, words highlight progressively |
| **subtle** | Clean minimal white text at bottom |

---

## Project Structure

```
podcli/
├── podcli                    # CLI entry point
├── setup.sh                  # one-command install & launch
├── package.json
├── CLAUDE.md                 # PodStack master config
│
├── .claude/commands/         # PodStack slash commands
│   ├── process-transcript.md
│   ├── generate-titles.md
│   ├── generate-descriptions.md
│   ├── plan-thumbnails.md
│   ├── review-content.md
│   ├── prep-episode.md
│   ├── publish-checklist.md
│   └── retro-episode.md
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
    ├── knowledge/            # .md context files for AI (13 templates)
    ├── assets/               # registered logos, videos
    ├── cache/transcripts/    # cached transcriptions
    ├── history/              # generated clip history
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

---

## Credits

Content workflow powered by [PodStack](https://github.com/nmbrthirteen/podstack) — inspired by [gstack](https://github.com/garrytan/gstack) by Garry Tan.

## License

MIT

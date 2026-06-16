<p align="center">
  <img src="public/podcli-logo-transparent.png" height="36" alt="podcli" />
</p>

<p align="center">
  <strong>Open-source AI podcast clipper.</strong><br/>
  Generate vertical clips with face tracking and burned-in captions. CLI, MCP server, and web app.
</p>

<p align="center">
  <a href="https://podcli.com"><strong>podcli.com</strong></a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#mcp-server-claude-integration">MCP</a> ·
  <a href="#features">Features</a>
</p>

<p align="center">
  <a href="https://github.com/nmbrthirteen/podcli/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="license: AGPL-3.0" /></a>
  <a href="https://github.com/nmbrthirteen/podcli/stargazers"><img src="https://img.shields.io/github/stars/nmbrthirteen/podcli?style=social&cacheSeconds=86400" alt="stars" /></a>
</p>

<p align="center">
  <a href="https://x.com/nikasiradze_/status/2056061654664708570">
    <img src="public/promo.gif" alt="Podcli demo" width="720" />
  </a>
</p>
<p align="center"><sub>▶ <a href="https://x.com/nikasiradze_/status/2056061654664708570">Watch with sound on X</a></sub></p>

```bash
./podcli process episode.mp4
```

One command transcribes, picks the best moments, crops to the face, and burns captions in. Nothing leaves your machine.

---

## What It Does

**podcli** takes a long-form podcast and turns it into a complete content operation:

```
Record episode
    ↓
Transcribe (Whisper, speaker detection)
    ↓
Find viral moments (Claude AI + audio energy + knowledge base)
    ↓
Render clips (9:16, captions, smart crop, normalized audio)
    ↓
Generate content package (titles, descriptions, thumbnails, SEO)    ← PodStack
    ↓
Publish with optimization checklist                                  ← PodStack
    ↓
Review performance                                                   ← PodStack
```

The first half is **video processing** — podcli's core engine. The second half is **content workflow** — powered by [PodStack](https://github.com/nmbrthirteen/podstack), a set of Claude Code slash commands that ship with podcli. Both halves are deeply integrated: the clip suggestion engine reads from your PodStack knowledge base, uses your title formulas and voice rules, checks the episode database for duplicates, and outputs MCP-aligned fields that flow through to export.

---

## How It Works (From a User's Perspective)

### 1. Drop in your episode

```bash
./setup.sh --ui
# → http://localhost:3847
```

Drag your video into the Web UI, or use the CLI:

```bash
./podcli process episode.mp4
```

### 2. Get clips automatically

podcli uses Claude to analyze your transcript against your show's knowledge base, finding the most viral moments. It scores each one on 4 dimensions, suggests clips with multi-cut segments (cutting out filler), and lets you toggle them on/off before rendering.

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

|               | Video Engine (podcli core)                        | Content Workflow (PodStack)                  |
| ------------- | ------------------------------------------------- | -------------------------------------------- |
| **What**      | Transcription, clip detection, rendering          | Titles, descriptions, thumbnails, publishing |
| **How**       | Python + FFmpeg + Whisper + OpenCV + Claude/Codex | Claude Code slash commands                   |
| **Interface** | Web UI, CLI, MCP tools                            | `/slash-commands` in Claude Code             |
| **Output**    | `.mp4` files ready to upload                      | Content packages ready to paste into YouTube |

Both halves share the same **knowledge base** (`.podcli/knowledge/`) — your show's brand, voice, title formulas, episode database, and style guide. Set it up once, everything stays on-brand.

---

## Features

### Video Processing

- **AI clip suggestion** — Claude/Codex-powered moment detection with knowledge base context, multi-cut segments, 4-dimension scoring
- **Face tracking** — YuNet face detection, exponential-smoothing camera, split-screen support, speaker-aware tracking with snap cooldown
- **Burned-in captions** — 4 styles: branded, hormozi, karaoke, subtle
- **Hardware-accelerated encoding** — VideoToolbox (Mac), NVENC (NVIDIA), VAAPI, CPU fallback
- **Smart cropping** — center crop or face tracking (handles split-screen, Riverside-style mixed layouts)
- **Multi-segment clips** — automatically cuts out filler, long pauses, and tangents
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
- **MCP server** — 17 tools for Claude Desktop / Claude Code integration
- **Web UI** — single-page flow at `localhost:3847`
- **CLI** — one-command processing: `./podcli process episode.mp4`

---

## Install

No prerequisites — the install fetches a self-contained binary, and the first run
provisions everything it needs (Python, Node, FFmpeg, whisper.cpp, models) into a
managed directory. You don't need Go, Node, Python, or FFmpeg installed.

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.ps1 | iex
```

**With npm** (if you already have Node):

```bash
npm install -g podcli
```

Then just run it — the first launch sets itself up:

```bash
podcli                       # interactive menu (and Web UI)
podcli process episode.mp4   # transcribe + export clips
```

**Optional**, for AI clip suggestion and the PodStack slash commands: install
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) or
[Codex](https://openai.com/index/introducing-codex/) (auto-detected).

> Building from source needs Go 1.23+ (and Node for the studio bundle); see
> [`plans/native-cli.md`](plans/native-cli.md).

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
# One command. Auto-transcribes, picks moments, renders clips.
./podcli process episode.mp4
```

With more control:

```bash
# Use an existing transcript instead of transcribing
./podcli process episode.mp4 --transcript transcript.txt --top 5

# Full options
./podcli process episode.mp4 \
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

The knowledge base is what makes podcli understand _your_ show. Drop `.md` files into `.podcli/knowledge/` and both the video engine and content workflow use them. The clip suggestion engine reads 8 of these files (prioritized by relevance), checks the episode database for duplicate avoidance, and applies your voice rules and title formulas when generating suggestions.

PodStack ships with **13 starter templates** that you fill in with your show's details:

| File                          | What It Teaches The AI                               |
| ----------------------------- | ---------------------------------------------------- |
| `00-master-instructions.md`   | Auto-detection rules, decision tree, quality gates   |
| `01-brand-identity.md`        | Show name, positioning, tagline, hosts, format       |
| `02-voice-and-tone.md`        | Voice fingerprint, banned words, the Coffee Test     |
| `03-episodes-database.md`     | Episode tracking, existing shorts (for dedup)        |
| `04-shorts-creation-guide.md` | Moment types, selection criteria, extraction process |
| `05-title-formulas.md`        | Title shapes, rules, templates by content type       |
| `06-descriptions-template.md` | Description formulas, hashtag library, SEO keywords  |
| `07-thumbnail-guide.md`       | Layouts, brand colors, typography, visual specs      |
| `08-topics-themes.md`         | Core topics, cross-cutting themes, audience map      |
| `09-content-workflow.md`      | End-to-end workflow phases, handoff specs            |
| `10-internal-processing.md`   | Auto-execution rules, internal quality gates         |
| `11-inspiration-channels.md`  | Reference channels, viral hooks, hybrid formulas     |
| `12-quick-reference.md`       | Copy-paste hooks, hashtags, CTAs, checklists         |

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

| Tool                 | Description                                                              |
| -------------------- | ------------------------------------------------------------------------ |
| `transcribe_podcast` | Transcribe audio/video with Whisper + speaker detection                  |
| `suggest_clips`      | Submit clip suggestions (includes duplicate check)                       |
| `create_clip`        | Render a single short-form clip as a vertical short                      |
| `batch_create_clips` | Render multiple clips in one batch                                       |
| `knowledge_base`     | Read/manage podcast context files (hosts, style, audience, etc.)         |
| `manage_assets`      | Register/list reusable assets (logos, videos)                            |
| `clip_history`       | View previously created clips, check for duplicates                      |
| `get_ui_state`       | Read current session state and get workflow next-step guidance           |
| `modify_clip`        | Adjust a suggested clip's timing, title, or caption style (or delete it) |
| `toggle_clip`        | Select or deselect a suggested clip for export                           |
| `update_settings`    | Update rendering settings (caption style, crop strategy, logo, outro)    |
| `list_outputs`       | List all rendered clip files in the output directory                     |
| `manage_presets`     | Save, load, list, or delete rendering presets                            |
| `analyze_energy`     | Analyze audio energy levels to find high-energy moments                  |
| `set_video`          | Set the working video file without transcribing                          |
| `import_transcript`  | Import an external transcript with word-level timestamps (skips Whisper) |
| `parse_transcript`   | Parse raw speaker-labeled plain text into word-level timestamps          |

---

## Caption Styles

| Style       | Look                                                                                |
| ----------- | ----------------------------------------------------------------------------------- |
| **branded** | Large bold text, dark box highlight on active word, gradient overlay, optional logo |
| **hormozi** | Bold uppercase pop-on text, yellow active word (Alex Hormozi style)                 |
| **karaoke** | Full sentence visible, words highlight progressively                                |
| **subtle**  | Clean minimal white text at bottom                                                  |

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
│   ├── models/               # ML model files
│   │   └── face_detection_yunet_2023mar.onnx
│   ├── services/             # Whisper, FFmpeg, captions, face tracking, etc.
│   │   ├── face_detector.py  # shared YuNet face detector
│   │   └── ...
│   └── config/
│       └── caption_styles.py
│
├── .podcli/                  # config home (gitignored) — knowledge, presets, assets
│   ├── knowledge/
│   ├── assets/
│   ├── presets/
│   └── history/
└── data/                     # runtime data (gitignored) — cache, output, working
    ├── cache/                # CLI transcription cache + remotion bundle
    │   └── transcripts/      # MCP/UI transcript cache
    ├── output/               # rendered clips
    └── working/              # temp uploads and task dirs
```

## Configuration

Copy `.env.example` to `.env` (setup.sh does this automatically):

| Variable         | Default    | Description                                           |
| ---------------- | ---------- | ----------------------------------------------------- |
| `WHISPER_MODEL`  | `base`     | Whisper model size (tiny, base, small, medium, large) |
| `WHISPER_DEVICE` | `auto`     | `cpu`, `cuda`, or `auto`                              |
| `PYTHON_PATH`    | (venv)     | Path to Python binary                                 |
| `PODCLI_HOME`    | `.podcli/` | Config home (knowledge, presets, assets, settings)    |
| `PODCLI_DATA`    | `data/`    | Runtime data (cache, output, working, logs)           |
| `FFMPEG_PATH`    | `ffmpeg`   | Custom FFmpeg path                                    |
| `LOG_LEVEL`      | `info`     | Logging verbosity                                     |

### Config profiles (multi-show / multi-machine)

Portable bundles zip your config home (not cache or rendered clips):

```bash
podcli config export ~/backups/myshow.zip
podcli config import ~/backups/myshow.zip --home ~/.podcli-myshow --activate
podcli config status
```

Activate a config root without importing: `podcli config use ~/.podcli-myshow` (writes `.podcli-home` in the project).

### Upgrading from older layouts

Older releases stored transcription cache under `project/.podcli/cache/` (now `data/cache/`) and presets under `project/presets/` (now `.podcli/presets/`). After upgrading, migration runs automatically when legacy files are still present (CLI, Web UI, MCP). To preview or run manually:

```bash
podcli config migrate --dry-run   # preview only
podcli config migrate             # apply (same as auto when legacy cache exists)
```

**One source of truth:** settings live in **config home** (`PODCLI_HOME` or `.podcli/`, tracked by `.podcli-home`); heavy/runtime files live under **data** (`PODCLI_DATA` or `data/`). The marker file only points at which config home is active — it does not replace either root.

MCP: `manage_config(action=migrate)`.

Web UI: [Config profiles](http://localhost:3847/config.html) (when `npm run ui` is running).

See [CONTRIBUTING.md](CONTRIBUTING.md) for development conventions.

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

AGPL-3.0. See [LICENSE](LICENSE).

**Need to use Podcli without AGPL terms?** A commercial license is available — email [siradze@nikusha.me](mailto:siradze@nikusha.me) with a one-line description of your use case.

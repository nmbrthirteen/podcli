# podcli + PodStack: AI podcast content studio

> Transcribe, clip, and publish from one place.

This file is the primary instruction document. `CLAUDE.podstack.md` (persona and protocols), `AGENTS.podstack.md` (cross-tool usage), and `ETHOS.podstack.md` (content philosophy) build on it; the tables below are the single source of truth.

You have two systems working together:

1. **podcli**: video processing engine (transcription, clip detection, rendering)
2. **PodStack**: content workflow (titles, descriptions, thumbnails, publishing)

Both share the same knowledge base at `.podcli/knowledge/`.

---

## Slash commands (PodStack)

| Command | Role | What it does |
|---------|------|-------------|
| `/plan-episode` | Episode Architect | Pre-recording: designs questions, story arc, and target moments backwards from ideal output |
| `/process-transcript` | Content Analyst | Ingests transcript → extracts 8-15 moments → scores → categorizes |
| `/generate-titles` | Title Writer | Generates 8 title options with full verification checklist |
| `/generate-descriptions` | Copywriter | Creates descriptions + hashtags + SEO keywords |
| `/plan-thumbnails` | Art Director | Plans thumbnail text + layout briefs for both formats |
| `/review-content` | Brand Guardian | Reviews output against brand voice, quality gates, banned words |
| `/produce-shorts` | Producer | Full pipeline: transcript → publish-ready package |
| `/publish-checklist` | Launch Manager | Pre/post-publish optimization checklist |
| `/retro-episode` | Analyst | Episode performance review + appends learnings to `.podcli/knowledge/13-learnings.md` |

---

## MCP tools (podcli engine)

All 26 tools registered by the MCP server.

**Transcription and input**

| Tool | What it does |
|------|-------------|
| `transcribe_podcast` | Transcribe audio/video with Whisper word timestamps + speaker detection |
| `transcribe_start` | Start transcription as a background job, returns a job_id immediately |
| `job_status` | Poll any background job (transcription, render, batch export) with long-polling |
| `set_video` | Set the working video without transcribing |
| `import_transcript` | Import an external transcript with word-level timestamps, skips Whisper |
| `parse_transcript` | Parse a speaker-labeled plain text transcript into word-level timestamps |

**Clip workflow**

| Tool | What it does |
|------|-------------|
| `get_ui_state` | Read session state (video, transcript, suggestions, settings) and next steps |
| `suggest_clips` | Submit clip suggestions, assigns clip numbers, pushes them to the web UI |
| `modify_clip` | Adjust a suggested clip: timing, title, caption style, or delete it |
| `toggle_clip` | Select or deselect a suggested clip for export |
| `create_clip` | Render a single clip with burned-in captions and normalized audio |
| `batch_create_clips` | Render multiple clips in one batch |
| `manage_reel` | Build a highlights reel: detect once, edit moments, rebuild without re-detecting |
| `analyze_energy` | Analyze audio energy levels to find high-energy moments |

**Content and configuration**

| Tool | What it does |
|------|-------------|
| `knowledge_base` | Read or manage the `.podcli/knowledge/` context files |
| `manage_assets` | Register and manage reusable assets: logos, intros, outros, music |
| `clip_history` | View previously created clips to avoid duplicates |
| `list_outputs` | List rendered clip files with sizes and dates |
| `update_settings` | Update rendering settings: caption style, crop strategy, logo, outro |
| `manage_presets` | Save, load, list, or delete rendering presets |
| `manage_thumbnail_config` | Show, export, import, or reset the thumbnail template |

**Integrations and environment**

| Tool | What it does |
|------|-------------|
| `manage_integrations` | List, enable, or disable podcli integrations |
| `export_to_davinci_resolve` | Export shorts as a DaVinci Resolve FCPXML project |
| `manage_config` | Manage portable config profiles and legacy path migration |
| `manage_env` | List, set, or unset global podcli settings stored in `.env` |
| `ai_cli_status` | Show whether Claude Code / Codex CLIs are available for AI features |

---

## The full pipeline

```
/plan-episode  →  Record  →  /produce-shorts  →  Published content
     ↑                              ↓
  Guest info              /process-transcript → /generate-titles → /generate-descriptions
                          → /plan-thumbnails → /review-content → /publish-checklist
```

Or run everything at once: `/produce-shorts`

After publishing: `/retro-episode`

---

## Knowledge base

All slash commands read from `.podcli/knowledge/`. This is where your show's brand, voice, and style live. 14 files:

| File | What it contains |
|------|-----------------|
| `00-master-instructions.md` | AI operating system, auto-detection rules |
| `01-brand-identity.md` | Show name, positioning, hosts, format |
| `02-voice-and-tone.md` | Voice fingerprint, banned words |
| `03-episodes-database.md` | Episode tracking, existing shorts |
| `04-shorts-creation-guide.md` | Moment selection criteria |
| `05-title-formulas.md` | Title shapes, rules, templates |
| `06-descriptions-template.md` | Description formulas, hashtags |
| `07-thumbnail-guide.md` | Thumbnail layouts, brand colors |
| `08-topics-themes.md` | Core topics, audience mapping |
| `09-content-workflow.md` | End-to-end workflow phases |
| `10-internal-processing.md` | Auto-execution rules |
| `11-inspiration-channels.md` | Reference channels, viral hooks |
| `12-quick-reference.md` | Copy-paste resources |
| `13-learnings.md` | Cross-episode learnings, appended by `/retro-episode` |

---

## Quality gate (always active)

Before outputting ANY content:

1. **Would I click this?** If no, rewrite
2. **Does it earn attention in 5 seconds?** If no, find better hook
3. **Does it deliver on the promise?** If no, it's clickbait, fix it
4. **Is it standalone?** If context needed, unusable for shorts
5. **Zero banned words**: check `02-voice-and-tone.md`
6. **The Coffee Test**: sounds like a person, not a press release

---

## Auto-detection

When input is provided without a specific command:

- **Transcript text or file** → Run `/process-transcript`
- **Asks for titles** → Run `/generate-titles`
- **Asks for thumbnails** → Run `/plan-thumbnails`
- **Asks for descriptions** → Run `/generate-descriptions`
- **Says "process episode"** → Run `/produce-shorts`
- **Asks to review content** → Run `/review-content`

---

## Project layout

```
├── CLAUDE.md                     ← primary instructions (this file)
├── .claude/commands/             ← PodStack slash commands
├── cli/                          ← Go launcher (install, update, provisioning)
├── src/                          ← TypeScript (MCP server, web studio, services)
├── backend/                      ← Python (Whisper, FFmpeg, captions)
├── .podcli/
│   ├── knowledge/                ← your show's brand brain (14 .md files)
│   ├── history/                  ← clip tracking
│   ├── assets/                   ← logos, intros, outros
│   └── presets/                  ← saved configs
├── data/                         ← runtime data: cache, working files (gitignored)
├── podcli-clips/                 ← rendered clips (gitignored; PODCLI_OUTPUT overrides)
└── episodes/                     ← content packages from PodStack (gitignored output)
```

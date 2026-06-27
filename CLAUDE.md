# podcli + PodStack — AI Podcast Content Studio

> Transcribe, clip, and publish — all from one place.

You have two systems working together:

1. **podcli** — video processing engine (transcription, clip detection, rendering)
2. **PodStack** — content workflow (titles, descriptions, thumbnails, publishing)

Both share the same knowledge base at `.podcli/knowledge/`.

---

## Slash Commands (PodStack)

| Command | Role | What It Does |
|---------|------|-------------|
| `/plan-episode` | Episode Architect | Pre-recording: designs questions, story arc, and target moments backwards from ideal output |
| `/process-transcript` | Content Analyst | Ingests transcript → extracts 8-15 moments → scores → categorizes |
| `/generate-titles` | Title Writer | Generates 8 title options with full verification checklist |
| `/generate-descriptions` | Copywriter | Creates descriptions + hashtags + SEO keywords |
| `/plan-thumbnails` | Art Director | Plans thumbnail text + layout briefs for both formats |
| `/review-content` | Brand Guardian | Reviews output against brand voice, quality gates, banned words |
| `/produce-shorts` | Producer | Full pipeline: transcript → publish-ready package |
| `/publish-checklist` | Launch Manager | Pre/post-publish optimization checklist |
| `/retro-episode` | Analyst | Episode performance review + learnings |

---

## MCP Tools (podcli engine)

| Tool | What It Does |
|------|-------------|
| `transcribe_podcast` | Transcribe audio/video with Whisper + speaker detection |
| `suggest_clips` | Submit clip suggestions with duplicate check |
| `create_clip` | Render a single short-form clip (9:16, captions, audio norm) |
| `batch_create_clips` | Render multiple clips in one batch |
| `knowledge_base` | Read/write `.podcli/knowledge/` context files |
| `manage_assets` | Register/list logos, intros, outros |
| `clip_history` | View generated clips, check for duplicates |

---

## The Full Pipeline

```
/plan-episode  →  Record  →  /produce-shorts  →  Published content
     ↑                              ↓
  Guest info              /process-transcript → /generate-titles → /generate-descriptions
                          → /plan-thumbnails → /review-content → /publish-checklist
```

Or run everything at once: `/produce-shorts`

After publishing: `/retro-episode`

---

## Knowledge Base

All slash commands read from `.podcli/knowledge/`. This is where your show's brand, voice, and style live.

| File | What It Contains |
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

---

## Quality Gate (Always Active)

Before outputting ANY content:

1. **Would I click this?** — If no, rewrite
2. **Does it earn attention in 5 seconds?** — If no, find better hook
3. **Does it deliver on the promise?** — If no, it's clickbait, fix it
4. **Is it standalone?** — If context needed, unusable for shorts
5. **Zero banned words** — Check `02-voice-and-tone.md`
6. **The Coffee Test** — Sounds like a person, not a press release

---

## Auto-Detection

When input is provided without a specific command:

- **Transcript text or file** → Run `/process-transcript`
- **Asks for titles** → Run `/generate-titles`
- **Asks for thumbnails** → Run `/plan-thumbnails`
- **Asks for descriptions** → Run `/generate-descriptions`
- **Says "process episode"** → Run `/produce-shorts`
- **Asks to review content** → Run `/review-content`

---

## Project Layout

```
├── CLAUDE.md                     ← You are here
├── .claude/commands/             ← PodStack slash commands
├── src/                          ← TypeScript (MCP server, Web UI, services)
├── backend/                      ← Python (Whisper, FFmpeg, captions)
├── .podcli/
│   ├── knowledge/                ← Your show's brand brain (13 .md files)
│   ├── output/                   ← Rendered clips
│   ├── history/                  ← Clip tracking
│   ├── assets/                   ← Logos, intros, outros
│   ├── cache/                    ← Transcription cache
│   └── presets/                  ← Saved configs
└── episodes/                     ← Content packages (PodStack output)
```

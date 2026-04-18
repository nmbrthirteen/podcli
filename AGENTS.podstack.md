# AGENTS.md — PodStack

> Cross-tool entry point for PodStack skills.
>
> This file is the **primary instruction document** for AI coding tools that read `AGENTS.md` by convention (OpenAI Codex, opencode, Aider, Cursor Agent, and others). Claude Code uses `CLAUDE.md` as its primary but also reads `AGENTS.md` as a secondary index.

PodStack turns your AI tool into a podcast content team: Episode Architect, Content Analyst, Title Writer, Copywriter, Art Director, Brand Guardian, Producer, Launch Manager, and Performance Analyst.

---

## How To Use

Each skill below is a self-contained instruction file in `commands/` (or `.claude/commands/`, `.codex/prompts/`, `.cursor/rules/`, `.opencode/commands/` — depending on which host installed it).

**To run a skill:** ask your agent to "run the [skill-name] skill" or invoke its slash command (`/[skill-name]`) where supported. The agent opens the corresponding file and follows it step by step.

**Natural-language triggers:** each skill's `triggers:` list describes phrases that should auto-route to it. If the user's message matches one, run that skill without asking.

**Completion Protocol:** every skill ends its output with one of:
- `DONE` — all quality gates passed
- `DONE_WITH_CONCERNS` — shipped, but flag non-blocking issues with evidence
- `BLOCKED` — cannot complete; state blocker + what's needed to unblock
- `NEEDS_INPUT` — one specific missing input; ask the question and wait

**Three-Strike Rule:** orchestrators (`/produce-shorts`) stop after 3 consecutive phase failures and return `BLOCKED` rather than looping.

---

## Sprint Workflow

PodStack is a process, not a collection of tools. Skills run in the order a content sprint runs:

```
Plan → Process → Write → Review → Publish → Retro
```

```
/plan-episode  →  [record]  →  /process-transcript  →  /generate-titles  →  /generate-descriptions
                                                                                    ↓
             /retro-episode  ←  [published]  ←  /publish-checklist  ←  /review-content  ←  /plan-thumbnails
```

Or run the full post-record pipeline at once: `/produce-shorts`.

---

## Skills

### /plan-episode

- **role:** Episode Architect
- **description:** Design questions, story arc, and moment map BEFORE recording
- **allowed-tools:** Read, Write
- **triggers:** plan episode, upcoming recording, guest prep, prepare for interview
- **outputs:** episode plan written to `episodes/ep[XX]-[guest]-plan.md`
- **next:** record → `/process-transcript`

### /process-transcript

- **role:** Content Analyst
- **description:** Extract, score, classify best moments from a raw transcript
- **allowed-tools:** Read, Write
- **triggers:** transcript, process transcript, extract moments, podcast transcript
- **outputs:** moment brief with timestamps, scores, titles, thumbnails, descriptions
- **next:** `/generate-titles` or `/produce-shorts`

### /generate-titles

- **role:** Title Writer
- **description:** Generate 8 verified title options for a clip or moment
- **allowed-tools:** Read
- **triggers:** titles for, title options, write titles, generate titles
- **outputs:** 8 titles + 2 top picks with rationale

### /generate-descriptions

- **role:** Copywriter
- **description:** Write shorts + long-form descriptions with hashtags and SEO keywords
- **allowed-tools:** Read
- **triggers:** description, descriptions for, write description, hashtags for
- **outputs:** ready-to-paste descriptions + keyword lists

### /plan-thumbnails

- **role:** Art Director
- **description:** Plan two-line thumbnail text + layout briefs for designers
- **allowed-tools:** Read
- **triggers:** thumbnail, thumbnails for, thumbnail text, thumbnail brief
- **outputs:** podcast (16:9) + shorts (9:16) thumbnail briefs

### /review-content

- **role:** Brand Guardian (Fix-First + specialist dispatch)
- **description:** Parallel specialist review, auto-fixes mechanical issues, batches human-decision items
- **allowed-tools:** Read, Edit, Task
- **triggers:** review content, check this, brand review, quality check, verify
- **outputs:** fix log (what was auto-fixed) + ask queue (what needs human call)

### /produce-shorts

- **role:** Producer (master orchestrator)
- **description:** Full pipeline from transcript to publish-ready content package
- **allowed-tools:** Read, Write, Edit, Task
- **triggers:** process episode, produce shorts, full pipeline, prep episode, make content package
- **outputs:** complete content package in `episodes/ep[XX]-[guest]-content-package.md`
- **orchestrates:** process-transcript → generate-titles → generate-descriptions → plan-thumbnails → review-content
- **stop-rule:** Three-Strike Rule (BLOCKED after 3 consecutive phase failures)

### /publish-checklist

- **role:** Launch Manager
- **description:** Pre/at/post-publish + day 3-4 optimization checklist
- **allowed-tools:** Read
- **triggers:** publish checklist, ready to publish, launch checklist, pre-publish
- **outputs:** checklist with completion status + next-step recommendations

### /retro-episode

- **role:** Performance Analyst
- **description:** Analyze published performance; append patterns to learnings file
- **allowed-tools:** Read, Write, Edit
- **triggers:** retro, episode review, analytics, what worked, post-mortem
- **outputs:** retro report written to `episodes/ep[XX]-[guest]-retro.md` + patterns appended to `knowledge/13-learnings.md`

---

## Knowledge Base

Skill files reference these knowledge files (default location: `knowledge/`, or `.podcli/knowledge/` with podcli):

| File | Contents |
|------|----------|
| `00-master-instructions.md` | AI operating system — routing, decision tree |
| `01-brand-identity.md` | Show positioning, hosts, format |
| `02-voice-and-tone.md` | Voice fingerprint, banned words |
| `03-episodes-database.md` | Episode tracking, existing shorts log |
| `04-shorts-creation-guide.md` | Moment selection criteria |
| `05-title-formulas.md` | Title spec — shapes, rules |
| `06-descriptions-template.md` | Description formulas, hashtags |
| `07-thumbnail-guide.md` | Layouts, colors, typography |
| `08-topics-themes.md` | Core topics, audience mapping |
| `09-content-workflow.md` | End-to-end workflow phases |
| `10-internal-processing.md` | Internal quality gates |
| `11-inspiration-channels.md` | Reference channels, viral hooks |
| `12-quick-reference.md` | Copy-paste hooks, CTAs, checklists |
| `13-learnings.md` | Cross-episode learnings (auto-updated by `/retro-episode`) |

---

## Quality Gate (Always Active)

Before outputting ANY content, verify:

1. **Would I click this?** — If no, rewrite
2. **Does it earn attention in 5 seconds?** — If no, find a better hook
3. **Does it deliver on the promise?** — If no, it's clickbait
4. **Is it standalone?** — If context needed, unusable for shorts
5. **Zero banned words** — Check `02-voice-and-tone.md`
6. **The Coffee Test** — Sounds like a person, not a press release

Full philosophy in `ETHOS.md`.

---

## Host Compatibility

PodStack ships one source-of-truth (`commands/`) and installs to the right location for each tool:

| Host | Install location | Primary doc |
|------|-----------------|-------------|
| Claude Code | `.claude/commands/*.md` | `CLAUDE.md` |
| OpenAI Codex | `.codex/prompts/*.md` | `AGENTS.md` (this file) |
| Cursor | `.cursor/rules/*.mdc` | `AGENTS.md` |
| opencode | `.opencode/commands/*.md` | `AGENTS.md` |
| Generic | `commands/*.md` | `AGENTS.md` |

Install: `./setup.sh --host <name>`. See `README.md` for per-host usage examples.

---

## One Rule

> **Earn attention in 5 seconds. Deliver value that matches the promise.**

Everything else flows from this.

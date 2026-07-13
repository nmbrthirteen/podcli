# AGENTS.podstack.md: cross-tool entry point for PodStack skills

> This file is the instruction document for AI coding tools that read `AGENTS.md` by convention (OpenAI Codex, opencode, Aider, Cursor Agent, and others). Claude Code uses `CLAUDE.md` as its primary; the command index, pipeline, MCP tool table, knowledge base table (14 files at `.podcli/knowledge/`), and quality gate live there and are not repeated here.

PodStack turns your AI tool into a podcast content team: Episode Architect, Content Analyst, Title Writer, Copywriter, Art Director, Brand Guardian, Producer, Launch Manager, and Performance Analyst.

---

## How to use

Each skill below is a self-contained instruction file in `commands/` (or `.claude/commands/`, `.codex/prompts/`, `.cursor/rules/`, `.opencode/commands/`, depending on which host installed it).

**To run a skill:** ask your agent to "run the [skill-name] skill" or invoke its slash command (`/[skill-name]`) where supported. The agent opens the corresponding file and follows it step by step.

**Natural-language triggers:** each skill's `triggers:` list describes phrases that should auto-route to it. If the user's message matches one, run that skill without asking.

**Completion protocol:** every skill ends its output with one of:
- `DONE`: all quality gates passed
- `DONE_WITH_CONCERNS`: shipped, but flag non-blocking issues with evidence
- `BLOCKED`: cannot complete; state blocker + what's needed to unblock
- `NEEDS_INPUT`: one specific missing input; ask the question and wait

**Three-strike rule:** orchestrators (`/produce-shorts`) stop after 3 consecutive phase failures and return `BLOCKED` rather than looping.

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
- **stop-rule:** three-strike rule (BLOCKED after 3 consecutive phase failures)

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
- **outputs:** retro report written to `episodes/ep[XX]-[guest]-retro.md` + patterns appended to `.podcli/knowledge/13-learnings.md`

---

## Knowledge base and quality gate

Skill files read the 14 knowledge files at `.podcli/knowledge/`; the full file table and the always-active quality gate are in `CLAUDE.md`. Full content philosophy in `ETHOS.podstack.md`.

---

## Host compatibility

PodStack ships one source-of-truth (`commands/`) and installs to the right location for each tool:

| Host | Install location | Primary doc |
|------|-----------------|-------------|
| Claude Code | `.claude/commands/*.md` | `CLAUDE.md` |
| OpenAI Codex | `.codex/prompts/*.md` | `AGENTS.podstack.md` (this file) |
| Cursor | `.cursor/rules/*.mdc` | `AGENTS.podstack.md` |
| opencode | `.opencode/commands/*.md` | `AGENTS.podstack.md` |
| Generic | `commands/*.md` | `AGENTS.podstack.md` |

These command files ship with podcli; place the set for your tool (left column) in
its command dir. See `README.md` for per-host usage examples.

---

## One rule

> **Earn attention in 5 seconds. Deliver value that matches the promise.**

Everything else flows from this.

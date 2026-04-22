---
description: One-verb pipeline — drop a video, confirm strategy, render clips
allowed-tools: Read, Bash, mcp__podcli__transcribe_podcast, mcp__podcli__transcribe_start, mcp__podcli__transcribe_status, mcp__podcli__get_ui_state, mcp__podcli__set_video, mcp__podcli__suggest_clips, mcp__podcli__batch_create_clips, mcp__podcli__knowledge_base, mcp__podcli__clip_history
argument-hint: [video-path-or-episode-slug] [optional: count e.g. "5 clips"]
triggers:
  - auto
  - make shorts from this
  - just edit this
  - one-shot this
---

# /auto — One-Verb Pipeline

> Drop raw footage, confirm a strategy, get rendered clips back. Inspired by video-use: "LLM reads, doesn't watch."

This command orchestrates the existing MCP tools on top of the compact packed transcript. No menus. No preset selection. Strategy gate before any render.

---

## Rules

1. **Read, don't watch.** Reason about clips from the packed markdown view — not raw segments, not frame dumps.
2. **Strategy first, render after.** Propose the cut list and WAIT for user confirmation before calling `batch_create_clips`.
3. **Knowledge base is context, not template.** If `.podcli/knowledge/` exists, read it for brand voice and format preferences. If not, infer from the content itself.
4. **Never silently render.** Every clip that ships must appear in the proposal the user approved.

---

## Inputs

| Field | Required | Source |
|-------|----------|--------|
| Video path | Yes | First argument, or set via `set_video` |
| Clip count | Optional | Second argument, e.g. `"5 clips"`. Default: propose what the content supports (3–8). |
| Brief | Optional | Anything after the count, e.g. `"focus on the investor pitch moments"`. |

---

## Flow

### Phase 1 — Inventory

1. If a video path was given, call `set_video(file_path)`. If no path, read `get_ui_state` and use the current video.
2. **Transcribe with progress narration.** Transcription takes 15–25 min on a 60-min episode — do NOT use the silent `transcribe_podcast` for long files. Instead:
   - Call `transcribe_start(file_path)` → returns `{job_id, cached, estimate}` immediately.
   - If `cached: true`, skip to step 3.
   - Otherwise emit a short status to the user: _"Transcription started — estimated {estimate}. I'll check progress every 30s."_
   - Loop: call `transcribe_status(job_id, wait_seconds: 30)`. Between calls, emit ONE terse line to the user like `"Progress: 47% — pyannote diarization"`. Keep it to one line per poll — no repeat prose. Exit the loop when `done: true`.
   - If `status: "error"`, stop and report the error.
3. Read the packed transcript: `get_ui_state(include_transcript: true)`. This returns a compact phrase-grouped view with speakers, silence gaps, and energy peaks.
4. If `.podcli/knowledge/` exists, read `01-brand-identity.md`, `02-voice-and-tone.md`, and `04-shorts-creation-guide.md` for show context. Skip silently if missing — `/auto` works on any content.
5. Call `clip_history` to see what's already been shipped for this episode. Avoid duplicates in the proposal.

**Fallback**: if `transcribe_start` returns an error about the Web UI not running, tell the user and offer either (a) run `npm run ui` in another terminal then retry, or (b) fall back to the synchronous `transcribe_podcast` (no live progress, works silently).

### Phase 2 — Strategy Proposal (GATE)

Emit a numbered strategy table. Do NOT render yet.

```
Proposed strategy for <video-label> (<duration>, <N> speakers):

Inferred format: <talking-head | interview | montage | tutorial | travel | other>
Inferred tone:   <from voice fingerprint or knowledge/02>
Target count:    <N> clips (from arg, or inferred from content density)

#1  [00:04:22-00:05:01]  39s  S0 "<hook>"
    Why: <one line — the angle, the stakes, or the moment>
    Title: <suggested, ≤60 chars>
    Style: <hormozi | karaoke | subtle | branded>

#2  ...

Unused peaks / skipped moments:
- [00:18:45] — high energy but no standalone hook
- [00:34:10] — great quote, but needs 40s of setup

Confirm? (yes / redirect / change specific clips)
```

**Stop here. Wait for the user's response.** Do not call `batch_create_clips` on implicit approval — require an explicit "yes", "go", "ship it", or similar.

### Phase 3 — Render (with live progress)

Once the user confirms:

1. Call `batch_create_clips(clip_numbers=[1,2,...], async_mode: true)` → returns `{job_id, clip_count}` immediately.
2. Emit to the user: _"Rendering {clip_count} clips — I'll report progress per clip."_
3. Loop: `job_status(job_id, wait_seconds: 30)` → emit ONE terse line per poll, e.g. `"Rendering 3/7 — clip #3 (speaker crop)"`. Exit when `done: true`.
4. When done, print the output paths and a one-line-per-clip summary (from the result field).

**Fallback**: if `async_mode` fails (Web UI down), fall back to sync `batch_create_clips(clip_numbers=[...])` — renders silently but still works.

### Phase 4 — Persist

Write a compact session log to `.podcli/sessions/<episode-slug>.md` (create the dir if missing):

```markdown
# <episode-label> — <ISO date>

## Proposed
<the strategy table>

## Rendered
- #1 → .podcli/output/<file>.mp4
- #2 → ...

## Skipped / redirected
- <any user redirects>
```

This gives next week's session something to pick up from. Not required — skip silently on I/O failure.

---

## Error Handling

- **Three-strike rule**: if any phase fails 3 times in a row, STOP and report. Do not continue with partial state.
- **No transcription yet**: call `transcribe_podcast`. It runs Whisper + diarization + auto-packs — takes minutes on a 60-min episode.
- **No packed view available**: `get_ui_state` falls back to raw segments automatically. Proceed, but flag lower reasoning quality in the proposal.
- **User says "redirect"**: discard the current proposal, ask what to change, re-propose. Don't argue.

---

## What `/auto` Is NOT

- Not a content-package generator — use `/produce-shorts` for titles/descriptions/thumbnails across the whole pipeline.
- Not a self-eval loop yet — `timeline_view` (the visual composite that would verify cut boundaries on rendered output) is not built. For now, trust the cut planner and inspect output manually.
- Not autonomous — the strategy gate is mandatory. No clips ship without explicit user confirmation.

---

## Completion

Return one of:

- **DONE** — Strategy approved, all N clips rendered, session log written.
- **PARTIAL** — Some clips rendered, some failed. Report which succeeded and the failure reason for the rest.
- **CANCELLED** — User redirected or rejected the strategy. No clips rendered.
- **BLOCKED** — Upstream failure (transcription, no audio, corrupt source). Report the specific ask needed to unblock.

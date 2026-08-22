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
3. **Knowledge base is context, not template.** If `` exists, read it for brand voice and format preferences. If not, infer from the content itself.
4. **Never silently render.** Every clip that ships must appear in the proposal the user approved.
5. **Every clip carries its own context.** A stranger who never heard the episode has to follow it from the first second. If the moment is an answer, the question comes with it.

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
   - **If the header says speakers: 0**, stop and tell the user before going further. Without speaker labels you cannot tell a question from an answer, so the whole question-with-the-answer rule below is inert and the picks will be worse. Offer to re-transcribe with `transcribe_start(file_path, enable_diarization: true)`. Only continue without it if the user says to.
4. If `` exists, read `01-brand-identity.md`, `02-voice-and-tone.md`, and `04-shorts-creation-guide.md` for show context. Skip silently if missing — `/auto` works on any content.
5. Call `clip_history` to see what's already been shipped for this episode. Avoid duplicates in the proposal.

**Fallback**: if `transcribe_start` returns an error about the Web UI not running, tell the user and offer either (a) run `npm run ui` in another terminal then retry, or (b) fall back to the synchronous `transcribe_podcast` (no live progress, works silently).

### Phase 2 — Topic Map (silent)

Before picking a single clip, map the whole episode. Clips get cut out of topics, never out of loose lines.

1. Sweep the packed transcript end to end and mark every topic change.
2. Emit 2-5 topics per 30 minutes of runtime, 6-12 for a full hour. Each topic covers 3-12 minutes.
3. Cover the whole runtime. The first 10 minutes and the last 5 get skipped most often, and they hold the setup and the summary. Anything that fits nowhere goes into a "loose ends" topic rather than being dropped.
4. Merge adjacent topics that both run under 2 minutes and cover the same ground.

Per topic, hold:

| Field | Value |
|-------|-------|
| `span` | `[hh:mm:ss-hh:mm:ss]` |
| `title` | what the topic is about, not a headline |
| `beats` | 2-5 sub-points actually said |
| `speakers` | who drives it |

Keep the map internal. It feeds Phase 3 and is only shown if the user asks for it.

### Phase 3 — Cut Planning (silent)

Work inside one topic at a time. Set boundaries by meaning, not by the clock.

**start_second**

- Land on the first sentence of the core statement. Drop the throat-clearing in front of it.
- If the moment is an answer, move the start back to include the question. Otherwise the clip opens on a reply to nothing and the viewer bounces.
- The question has to be inside the clip. `context_line` is a note for the editor, not a fix: nothing burns it into the video yet, so a clip that relies on it still ships with no setup.
- If the question rambles past roughly 8 seconds, use `segments` to keep the asked part and cut the rambling, or drop the moment.
- Never open on a word pointing back before the cut: "that", "it", "they", "yeah", "so", "exactly", "right", "which is why". Widen the start until the reference is inside the clip.

**end_second**

- Land on the end of the last sentence of the argument, never on the end of the topic block.
- Cut before trailing summaries, transitions, and "anyway, so" tails.
- If the thought is unfinished at the boundary, extend until it closes or drop the moment. Never ship half an argument.

**Never cut** mid-sentence, mid-list, or between a claim and the evidence for it.

**Write the payoff before the title.** One sentence, second person, on what the viewer walks away with. The title is then derived from the payoff, not from transcript wording. A moment with no payoff you can state in one sentence is not a clip. Drop it.

**Check the moment against its own type**, not against one generic bar:

| Type | Carries the clip | Fails when |
|------|------------------|-----------|
| Guest story | A turn: what they expected, what happened instead | It summarizes the story instead of telling it |
| Technical insight | One mechanism explained in plain words | It needs a diagram, or terms the viewer does not have |
| Market / landscape | A specific read on where things are going, with a reason | It lists players without taking a position |
| Business / strategy | A number, a trade-off, or a decision with a cost | The advice would fit any company in any year |
| Hot take | A claim a reasonable person would argue with, plus the reason | It is only a strong tone with no claim under it |

**Run the standalone check.** Name what the viewer must already know. If that is anything other than "nothing", the start moves back until the clip covers it. If it cannot, drop the moment.

### Phase 4 — Strategy Proposal (GATE)

Emit a numbered strategy table. Do NOT render yet.

```
Proposed strategy for <video-label> (<duration>, <N> speakers):

Inferred format: <talking-head | interview | montage | tutorial | travel | other>
Inferred tone:   <from voice fingerprint or 02-voice-and-tone.md>
Target count:    <N> clips (from arg, or inferred from content density)

#1  [00:04:22-00:05:01]  39s  S0 "<hook>"
    Payoff: <what the viewer walks away with, one sentence, second person>
    Needs:  <nothing | what the viewer must know, and how this clip covers it>
    Why:    <the angle, the stakes, or the moment>
    Title:  <derived from the payoff, ≤60 chars>
    Style:  <hormozi | karaoke | subtle | branded>

#2  ...

Unused peaks / skipped moments:
- [00:18:45] high energy, no payoff you can state in one sentence
- [00:34:10] great quote, needs 40s of setup that will not fit
- [00:41:02] answer with no question in reach, and the setup is too long to card

Confirm? (yes / redirect / change specific clips)
```

**Stop here. Wait for the user's response.** Do not call `batch_create_clips` on implicit approval — require an explicit "yes", "go", "ship it", or similar.

### Phase 5 — Render (with live progress)

Once the user confirms:

1. Call `suggest_clips` with the approved list. Every entry carries `payoff`, `standalone`, `context_line`, and `preview_text` (the real opening line, verbatim) alongside the timings. The tool rejects a clip whose context is missing and tells you which one; fix it and resubmit the full list rather than dropping it.
2. Call `batch_create_clips(clip_numbers=[1,2,...], async_mode: true)` → returns `{job_id, clip_count}` immediately.
3. Emit to the user: _"Rendering {clip_count} clips — I'll report progress per clip."_
4. Loop: `job_status(job_id, wait_seconds: 30)` → emit ONE terse line per poll, e.g. `"Rendering 3/7 — clip #3 (speaker crop)"`. Exit when `done: true`.
5. When done, print the output paths and a one-line-per-clip summary (from the result field).

**Fallback**: if `async_mode` fails (Web UI down), fall back to sync `batch_create_clips(clip_numbers=[...])` — renders silently but still works.

### Phase 6 — Persist

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

---
name: podcli
description: Transcribe a podcast episode, shortlist clips, render shorts, and hand off titles and descriptions for publishing. Use when the user wants to process an episode, make clips, or drive podcli from an agent.
---

# podcli

Turn a long episode into short clips with face tracking and burned-in captions. Drive it from the CLI, the studio, or MCP.

## Setup

```bash
curl -fsSL https://podcli.com/install.sh | sh
podcli setup                  # once; mcp does not auto-provision
podcli mcp install            # Claude Code / Codex, whichever is on PATH
podcli doctor --json          # confirm backend, mcp, ffmpeg, models
```

`--help` and `--version` never download. `podcli mcp` needs setup first because stdout is JSON-RPC.

Clip scoring uses Claude Code or Codex login, or an Anthropic API key. Without those it falls back to heuristics. Speaker diarization needs `HF_TOKEN`.

## Process an episode (CLI)

```bash
podcli process episode.mp4 --json --yes
```

JSON on stdout: clip paths, durations, scores, titles, descriptions, tags, caption files. Logs on stderr. `-y/--yes` skips prompts. `NO_COLOR=1` turns off colour and spinners.

Exit codes: 0 success, 1 runtime error, 2 usage, 130 interrupted.

## Process an episode (MCP)

Prefer the short path so you do not load the full transcript:

1. `transcribe_podcast` (or `transcribe_start` + `job_status` on a long file)
2. `rank_clip_candidates(video_path)` — about 20 sentence-aligned candidates
3. Pick a subset. `suggest_clips` to store them, or `create_clip` / `batch_create_clips` to render
4. `list_outputs` if you only need paths on disk

One-shot when the user wants finished files, not a review pass:

1. `process_episode(video_path)`
2. `job_status(job_id, wait_seconds: 30)` until `done`
3. Read clip paths, durations, scores, titles, captions from `result`

The original tools stay as they were. These two are extras.

## After clips exist

Titles, descriptions, thumbnails, brand review: PodStack commands (`/produce-shorts`, `/generate-titles`, `/generate-descriptions`, `/plan-thumbnails`, `/review-content`). Knowledge base is `.podcli/knowledge/`.

Publishing is outside podcli unless YouTube is linked (`youtube` CLI / integrations). Hand the JSON (paths + titles + descriptions + tags) to the user's publisher.

Do not call `get_ui_state(include_transcript: true)` unless you need to rewrite a range; use `rank_clip_candidates`. Do not run `podcli mcp` as the first command on a fresh install; run `podcli setup`.

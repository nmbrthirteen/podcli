# Clip history schema (`.podcli/history/clips.json`)

`clips.json` is a flat JSON array of clip records, shared on disk by two writers:

- **TypeScript** — `src/services/clips-history.ts` (`ClipsHistory`), writes one entry per render
  from the web server and MCP server.
- **Python** — `backend/services/clips_history.py`, the CLI writer (`clips edit`/`reopen`) and the
  Phase 2 analytics writer.

The TS type is `ClipHistoryEntry` in `src/models/index.ts`. Keep both languages in sync with this table.

| Field | Type | Written by | Notes |
|-------|------|-----------|-------|
| `id` | string (uuid) | TS render | Stable identifier. CLI also accepts an unambiguous 8-char prefix. |
| `source_video` | string | TS render | Absolute path; episodes are grouped by its **basename**. |
| `start_second` | number | TS render | |
| `end_second` | number | TS render | |
| `caption_style` | string | TS render / edit | `hormozi` \| `karaoke` \| `subtle` \| `branded` |
| `crop_strategy` | string | TS render / edit | `center` \| `face` \| `speaker` |
| `logo_path` | string? | TS render | |
| `title` | string | TS render / edit | |
| `output_path` | string | TS render | Rendered mp4. |
| `file_size_mb` | number | TS render | |
| `duration` | number | TS render | |
| `created_at` | string (ISO) | TS render | |
| `content_type` | string? | TS render | Carried from the suggestion (`guest_story`, `hot_take`, …). Undefined when no suggestion was used. |
| `transcript_slice` | string? | TS render | Plain text the clip says. The session transcript is overwritten, so this is the **only durable** copy — never reconstruct it later. |
| `youtube_video_id` | string? | Phase 2 | Set when a published video is linked. |
| `metrics` | object? | Phase 2 | `{ views?, retention?, ctr?, impressions?, fetched_at? }`. retention/ctr are 0–100. |

## Writer contract

- **Additive, optional fields only.** Old entries without the newer fields stay valid; no migration.
- **Read-modify-write, preserve unknown keys.** A writer in one language must never drop fields it
  doesn't know about (e.g. the CLI editing a title must not clobber a Phase 2 `metrics` block).
  `update_clip` applies only non-`None` fields onto the loaded entry.
- **No file locking.** Single-user localhost tool; TS writes on render, Python writes on edit — not
  concurrent in practice. Do not add locking.

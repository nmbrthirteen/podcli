# podcli Studio — Phase 1 Implementation Plan

> Major release groundwork. Full design (13 decisions) lives in the session memory file
> `project_studio_dashboard_design.md`. This plan covers **Phase 1 only**: the studio
> shell, library + per-clip edit pages, and CLI parity. **No YouTube/analytics (Phase 2),
> no semantic learning (Phase 3).**

**Guiding constraints:** open source; clean, correct, **minimal extra code**; `.podcli/` is the
shared on-disk contract; Python is the engine + history writer, web/MCP are thin shells.

**Stack decision:** Vite + React Router replaces the single-file CDN-React `src/ui/public/index.html`.

---

## Phase 0 — Discovery (DONE — facts to build on)

**UI / build**
- No bundler today. `src/ui/public/index.html` (~1880 lines) loads React 18.2 + ReactDOM + babel-standalone via CDN, all app code in one `<script type="text/babel">`. CSS: `public/css/styles.css`. Other pages: `config.html`, `integrations.html`, `knowledge.html`.
- Express serves statics: `app.use(express.static(join(__dirname, "public")))` — `src/ui/web-server.ts:197`. Port **3847** (`web-server.ts:52`). **No SPA fallback route today.**
- Build: `"build": "tsc && cp -r src/ui/public dist/ui/"` (`package.json`). `"type":"module"`, target ES2022, module ESNext, `rootDir ./src`, `outDir ./dist`, **no `jsx` configured** (public/ is not part of tsc build).
- Stable API surface the new UI consumes: `/api/history`, `/api/outputs`, `/api/ui-state` (GET/POST — MCP↔UI bridge), `/api/events` (SSE), `/api/preview/:f`, `/api/stream-source`, plus the full upload→transcribe→suggest→create/batch flow.

**Data layer**
- `ClipHistoryEntry` (`src/models/index.ts:200-213`): `id, source_video, start_second, end_second, caption_style, crop_strategy, logo_path?, title, output_path, file_size_mb, duration, created_at`. **No** `content_type`, `score`, transcript, or youtube fields.
- `src/services/clips-history.ts`: `load / save / record / findDuplicate / list / getBySource`. `record(entry: Omit<ClipHistoryEntry,"id"|"created_at">)`. Writes `paths.clipsHistory` = `${home}/history/clips.json`.
- **5 `record()` call sites**: `web-server.ts:600` (create-clip), `:729` (batch-clips), `:1804` (davinci export); `server.ts:666` (MCP create_clip), `:908` (MCP batch_create_clips).
- `content_type` originates in `suggest-clips.handler.ts:123`; the suggestion (with `content_type`) is resolved at `create-clip.handler.ts:128-137`; transcript words available at `create-clip.handler.ts:152` — but neither is threaded into `record()`.
- **Python does NOT read/write clips.json today** — `backend/config/paths.py:59` knows the path; nothing uses it. No Python history module exists.
- Paths agree across `src/config/paths.ts` and `backend/config/paths.py`.

**CLI / Python**
- `backend/cli.py`: argparse with `if/elif args.command` dispatch (`main()` ~line 2854+). Existing subcommands: `process, presets, assets, thumbnails, swap-thumbnail, corrections, knowledge, config`.
- Copy templates: **read** = `cmd_assets` (~2026-2078), **write** = `cmd_knowledge` (~2361-2451). Both print ANSI text, import service modules locally, `sys.exit(1)` on error. `swap-thumbnail` (~2177-2318) finds a clip by **file path**, not clips.json.
- Config loaded via `from config.paths import paths` (cli.py:43). Service modules live in `backend/services/` (pattern to mirror: `corrections.py`).
- **CLI = human ANSI text, no `--json`.** The JSON task-runner is separate (`backend/main.py` `emit_result` + `TASK_HANDLERS`), used by `PythonExecutor`. The `clips` CLI commands use the ANSI/human contract.
- `PythonExecutor.execute(taskType, params)` (`src/services/python-executor.ts`) spawns `backend/main.py`, JSON over stdin/stdout. (Relevant to Phase 2; Phase 1 CLI runs cli.py directly.)

**Concurrency note (accepted constraint):** clips.json has no file locking. Single-user localhost tool; TS writes on render, Python writes on `clips edit` — not concurrent in practice. Do **not** add locking (over-engineering). Both writers must preserve unknown fields on rewrite (read-modify-write, never reconstruct).

---

## Phase 1A — Data foundation (schema + Python history writer)

Lowest risk, no UI. Establishes the shared contract everything else builds on.

### What to implement
1. **Extend `ClipHistoryEntry`** in `src/models/index.ts:200-213` with **optional** fields (additive, no migration):
   ```ts
   content_type?: string;        // carried from SuggestedClip at record time
   transcript_slice?: string;    // packed/plain text of the clip's words, captured at render time
   youtube_video_id?: string;    // Phase 2 — declared now so the schema is stable
   metrics?: {                   // Phase 2 — declared now, never written in Phase 1
     views?: number;
     retention?: number;         // averageViewPercentage 0-100
     ctr?: number;               // impressionsClickThroughRate 0-100
     impressions?: number;
     fetched_at?: string;        // ISO
   };
   ```
2. **Thread `content_type` + `transcript_slice` into the TS record calls** where they are in scope:
   - In `create-clip.handler.ts`, the resolved `suggestion` (has `content_type`) and `transcriptWords` (line 152) are both available. Build a `transcript_slice` string (join the words whose timestamps fall in `[start_second, end_second]`) and surface both on the handler's return so the web/MCP record sites can pass them.
   - Update the **5 record() sites** to pass `content_type` and `transcript_slice` when available (omit otherwise — they're optional). Batch/davinci sites: thread per-clip `content_type` if the batch spec carries it; otherwise leave undefined. **Do not invent data** — only pass what is genuinely in scope.
3. **Create `backend/services/clips_history.py`** mirroring `corrections.py` structure. Functions:
   ```python
   load_clips_history() -> list[dict]      # read paths["clipsHistory"], [] if missing/bad
   save_clips_history(entries) -> str      # mkdir -p, json.dump indent=2 ensure_ascii=False
   list_clips(limit=50) -> list[dict]      # entries[-limit:][::-1]
   get_clips_by_source(video_path) -> list[dict]   # basename match, reversed
   find_clip(clip_id) -> dict | None       # exact id, also accept 8-char prefix match
   update_clip(clip_id, **fields) -> dict  # read-modify-write, preserve unknown keys, return updated
   ```
   Use `from config.paths import paths` and `paths["clipsHistory"]`. **Preserve unknown fields** (read-modify-write) so a TS-written `metrics` block is never clobbered by a Python edit.
4. **Document the schema** as the cross-language contract: add `docs/clips-schema.md` (or a header comment block in `clips_history.py`) listing every field, type, who writes it (TS-render / Python-edit / Phase-2), and the "preserve unknown fields" rule.

### Documentation references
- Extend interface: `src/models/index.ts:200-213`.
- Record sites to update: `web-server.ts:600,729,1804`; `server.ts:666,908`.
- Where content_type/transcript are in scope: `create-clip.handler.ts:128-137,152`.
- Python service pattern to copy: `backend/services/corrections.py`; path: `backend/config/paths.py:59`.

### Verification checklist
- [ ] `npm run build` (tsc) passes with the extended interface.
- [ ] Render a clip via the web UI → the new `clips.json` entry includes `content_type` and `transcript_slice` (when a suggestion was used).
- [ ] `python -c "from backend.services.clips_history import list_clips; print(list_clips(3))"` reads the same file TS wrote.
- [ ] Python `update_clip` then TS `load()` → unknown fields survive both round-trips (write a `metrics` stub by hand, edit title in Python, confirm `metrics` still present).

### Anti-pattern guards
- ❌ No new entities/tables, no migration script — fields are optional, old entries stay valid.
- ❌ Do not rewrite clips.json by reconstructing objects field-by-field (drops unknown keys). Read-modify-write only.
- ❌ Do not add file locking.
- ❌ Do not fabricate `content_type`/`transcript_slice` where the source data isn't actually in scope — leave undefined.

---

## Phase 1B — CLI `clips` subcommands

Delivers CLI parity for history + re-iteration entry point. Depends on 1A's Python service.

### What to implement
1. **Register the `clips` subparser** in `cli.py main()` (beside `knowledge`), following the existing `add_parser`/`add_subparsers` pattern:
   - `clips list [-n/--limit N]`
   - `clips edit <clip_id> [--title T] [--caption-style S] [--notes ...]` (metadata-only edits per design: edit → save → latest)
   - `clips reopen <clip_id>` — hydrate `.podcli/ui-state.json` from the history entry so the web Workspace opens with that clip loaded (see below).
   - Add `elif args.command == "clips": cmd_clips(args)` to dispatch.
2. **Implement `cmd_clips(args)`** using the `cmd_assets`/`cmd_knowledge` ANSI template:
   - `list`: print id (8-char), title, source basename, created_at, duration; show `content_type` if present.
   - `edit`: `find_clip` → apply provided fields → `update_clip`. Error + `sys.exit(1)` if not found.
   - `reopen`: read the entry; write a single-suggestion `ui-state.json` — `videoPath = source_video`, a one-item `suggestions` array reconstructed from the entry (`title, start_second, end_second, duration, content_type, preview_text = transcript_slice`), `phase = "reviewing"`. This is the CLI→UI bridge over the shared `.podcli/` contract. Print confirmation + the URL `http://localhost:3847/episode?...` (or instruct to open the studio).
3. **`reopen` semantics are metadata-only in Phase 1** — it loads the clip back into the editing surface; it does not auto-re-render. (Re-render is a save action in the UI / a future flag.)

### Documentation references
- Subparser + dispatch: `cli.py main()` (~2862-3044).
- Read handler template: `cmd_assets` (~2026-2078). Write handler template: `cmd_knowledge` (~2361-2451).
- ui-state shape to write: `UIState` in `src/models/index.ts:106-121` (videoPath, suggestions, phase).

### Verification checklist
- [ ] `python backend/cli.py clips list` prints recent clips from the same clips.json the UI shows.
- [ ] `clips edit <id> --title "X"` → `clips list` reflects it AND the web UI `/api/history` shows the new title.
- [ ] `clips reopen <id>` writes ui-state.json; opening the studio loads that clip into the Workspace.
- [ ] Editing a clip in Python preserves its `metrics`/unknown fields (re-run the 1A round-trip check).

### Anti-pattern guards
- ❌ Don't emit JSON from the CLI (human ANSI contract) — match existing subcommands.
- ❌ Don't reach into TS to record; write clips.json via the Python service from 1A.
- ❌ `reopen` must not trigger a render in Phase 1.

---

## Phase 1C — Vite + React Router scaffold (parity with current UI)

Stand up the build app and **port the existing editor** into a route, preserving all working functionality before adding new pages.

### What to implement
1. **New client source dir** `src/ui/client/` (Vite root). Files: `index.html` (Vite entry, no CDN), `main.tsx` (mounts `<RouterProvider>`), `App.tsx` (layout shell + nav), `routes/` and `components/`.
2. **Add deps** (devDeps): `vite`, `@vitejs/plugin-react`, `@types/react`, `@types/react-dom`. Deps: `react`, `react-dom`, `react-router-dom`. (React leaves the CDN.)
3. **`src/ui/client/vite.config.ts`**: `root: src/ui/client`, `plugins:[react()]`, `build.outDir: ../../../dist/ui/public` + `emptyOutDir: true`, `server.proxy` mapping `/api` → `http://localhost:3847`.
4. **Client tsconfig** `src/ui/client/tsconfig.json` with `jsx: "react-jsx"`, DOM libs. **Exclude `src/ui/client` from the root `tsconfig.json`** so the node `tsc` build (server) and the Vite build (client) don't fight. Root tsconfig `exclude` adds `"src/ui/client"`.
5. **Router** (`react-router-dom`) with routes:
   - `/` → `StudioHome` (Phase 1D)
   - `/episode` (and `/episode/:source?`) → `EpisodeWorkspace` (the ported existing editor)
   - `/clip/:id` → `ClipDetail` (Phase 1D)
6. **Port the existing editor** from `public/index.html` into `routes/EpisodeWorkspace.tsx` + extracted components (`LivePhonePreview`, `TikTokWireframe`, `PhoneCaptionBody`, `SpecRecap`, `McpHints`). Mostly mechanical: move JSX out of the babel `<script>`, replace CDN globals (`const {useState}=React`) with real imports, keep the `api()` fetch helper and the `STYLE_CONFIGS` map. Reuse `public/css/styles.css` (import it).
7. **Express: serve the built SPA + add a fallback route.** Keep `express.static` at `web-server.ts:197` (now serving Vite output in `dist/ui/public`). After all `/api` routes, add an SPA fallback: `app.get("*", (req,res)=> res.sendFile(join(__dirname,"public","index.html")))` — guard so it never shadows `/api/*` or static assets.
8. **Update build/dev scripts** in `package.json`:
   - `"build": "vite build --config src/ui/client/vite.config.ts && tsc && cp -r src/ui/public/css dist/ui/public/css && cp src/ui/public/*.png dist/ui/public/"` (Vite emits HTML/JS to `dist/ui/public`; still copy non-bundled css/images, or import CSS through Vite and drop the copy).
   - `"ui:dev": "vite --config src/ui/client/vite.config.ts"` (client) alongside existing `"ui": "tsx src/ui/web-server.ts"` (server). Document running both in dev.
9. **Legacy pages**: leave `config.html` / `integrations.html` / `knowledge.html` as-is for Phase 1 (link out to them), OR fold into routes later. Do not delete `public/index.html` until parity is confirmed; then remove.

### Documentation references
- Static serving to preserve/extend: `web-server.ts:197`; listen: `web-server.ts:~1862`.
- Components + STYLE_CONFIGS + `api()` to port: `src/ui/public/index.html` (App ~554, LivePhonePreview ~358, TikTokWireframe ~175, PhoneCaptionBody ~275, SpecRecap ~471, McpHints ~499, api() ~58, STYLE_CONFIGS ~114).
- Build script to replace: `package.json` `"build"`.

### Verification checklist
- [ ] `npm run ui:dev` + `npm run ui` → app loads on Vite dev server, `/api` proxied, full upload→transcribe→suggest→create flow works identically to the old UI.
- [ ] `npm run build` → `dist/ui/public/index.html` is the Vite output; `npm run ui:prod` serves it on 3847; deep-linking `/episode` and refresh work (SPA fallback).
- [ ] No CDN `<script>` for React/Babel remains in the served HTML.
- [ ] `git grep babel-standalone` returns nothing in the new client.

### Anti-pattern guards
- ❌ Don't rewrite editor logic — **port** it (move + swap imports). Behavior parity first, cleanup later.
- ❌ Don't let `tsc` try to compile the client (must be in root tsconfig `exclude`).
- ❌ SPA fallback must be registered AFTER api routes and must not intercept `/api/*`.
- ❌ Don't break the `/api/ui-state` MCP↔UI bridge — the Workspace must still read/write it (this is how `clips reopen` hands off).

---

## Phase 1D — Studio Home (library) + Clip Detail (edit page)

The new surfaces. Depends on 1A (fields), 1B (edit path), 1C (router/shell).

### What to implement
1. **`StudioHome` (`/`)** — the library:
   - Fetch `/api/history` (and `/api/outputs` for file existence). Group into **episodes by `source_video` basename** (mirror `getBySource` logic client-side, or add a thin `/api/episodes` endpoint that calls `clips-history.getBySource` grouping — prefer reusing existing `/api/history` to keep code minimal).
   - Episode cards: source name, clip count, created range, status. Recent-clips rail. An **action rail** up top driven by clip state ("N clips rendered", "Episode X in review") — derive from existing data, no new backend.
   - "+ New Episode" → `/episode` (fresh editor).
2. **`ClipDetail` (`/clip/:id`)** — the per-clip edit page (design decision: edit → save → latest, no version stacks):
   - Load the clip (from `/api/history` by id, or a small `/api/clip/:id` that calls `find_clip`). Show preview (`/api/preview/:filename` from `output_path`), transcript_slice, content_type, settings.
   - Editable fields: **title, caption style, thumbnail, cut timing**. Save:
     - Metadata-only edits (title) → POST to a thin endpoint that calls the **same Python `update_clip`** (via `PythonExecutor` or a small route) so TS and CLI share one writer. (Minimal: add `update_clip` task to `backend/main.py` TASK_HANDLERS reusing 1A's service, exposed as `/api/clip/:id` PATCH.)
     - Caption/timing change → triggers a re-render through the existing create-clip flow, then updates the entry (save = latest).
     - Thumbnail change → reuse existing `swap-thumbnail` capability (already in cli.py / thumbnail service).
   - "Reopen in editor" → navigates to `/episode` with this clip hydrated (same handoff as CLI `reopen`).
3. **Keep it data-minimal**: prefer reusing `/api/history`, `/api/preview`, `/api/outputs`, `/api/ui-state` over new endpoints. Add a backend route only where a write must go through the shared Python writer (the clip edit).

### Documentation references
- Grouping logic: `clips-history.ts getBySource` (basename match).
- Existing endpoints to reuse: `/api/history`, `/api/outputs`, `/api/preview/:f`, `/api/ui-state`.
- Edit writer: Phase 1A `backend/services/clips_history.py update_clip`; thumbnail: `cmd_swap_thumbnail` mechanics (cli.py ~2177-2318).

### Verification checklist
- [ ] Home lists episodes grouped by source with correct clip counts.
- [ ] Clicking a clip opens `/clip/:id` with preview + metadata.
- [ ] Editing a title in the UI persists via the **same Python writer** the CLI uses (verify with `python backend/cli.py clips list`).
- [ ] Changing caption style re-renders and the entry reflects the latest output.
- [ ] "Reopen in editor" loads the clip into the Workspace (same result as `clips reopen`).

### Anti-pattern guards
- ❌ No version stacks/trays — one current state per clip.
- ❌ Don't add a second history writer in TS for edits — route edits through the Python `update_clip` so there is one source of truth.
- ❌ No YouTube/metrics UI (Phase 2) — `metrics`/`youtube_video_id` exist in the schema but are not surfaced or written here.

---

## Final Phase — Verification

1. **Build & run:** `npm run build` clean (tsc + vite); `npm run ui:prod` serves the SPA on 3847; deep links + refresh work.
2. **Parity:** full upload→transcribe→suggest→create/batch flow works in the new UI identically to the removed `index.html`.
3. **Cross-language contract:** create a clip in the UI → `python backend/cli.py clips list` shows it with `content_type`/`transcript_slice`; `clips edit` from CLI → UI reflects it; a hand-written `metrics` block survives a Python edit (preserve-unknown-fields).
4. **CLI parity:** `clips list/edit/reopen` all function headless.
5. **Anti-pattern grep:** no `babel-standalone`/CDN React in served HTML; client excluded from root tsconfig; SPA fallback after api routes; single history writer for edits.
6. **Tests:** run existing suite (299 tests baseline); add focused tests for `clips_history.py` (round-trip, preserve-unknown-fields, find/update) and for the extended `ClipHistoryEntry` recording.

---

## Suggested execution order
`1A → 1B → 1C → 1D → Final`. 1A/1B are independent of the UI and ship value (CLI history) immediately; 1C is the big migration; 1D adds the new surfaces on the shell. Each sub-phase is self-contained for a fresh context.

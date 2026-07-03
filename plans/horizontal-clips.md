# podcli → Horizontal (16:9) clips + multi-format repurposing

> Goal: render every detected moment to **more than one format** (vertical 9:16 today, horizontal 16:9 next, square later) so podcli becomes the one place to repurpose a podcast, not just a vertical-clip button. The AI half is the real product: horizontal needs a **different viral-moment profile** than vertical, not just a wider crop.

## North star

```
one detected moment
  ├─ 9:16 short   → Shorts / Reels / TikTok   (hook-first, 20-45s)   ← today
  └─ 16:9 clip    → YouTube in-feed / X / LinkedIn (arc-first, 60-300s) ← this plan
        ↓ both performance streams feed the YouTube learning loop
        → the scorer learns which format wins per moment type
```

## The one decision everything hangs on

**Format is a render-time property. A moment is format-neutral.**

A moment is a scored time-range (`start`, `end`, `segments`, `speakers`, `content_type`) with no aspect ratio. It carries **per-format scores** (`vertical_score`, `horizontal_score`) because virality scoring differs by format, then renders to one or more formats. Keeping format at render time (not on the moment) is what makes "detect once, fan out to N formats" coherent and preserves the learning loop. Every other repurposing output (audiogram, quote card, show notes, X thread) is later "just another renderer off the same moment + transcript + brand KB."

## Vertical vs horizontal are different products

| | Vertical 9:16 (have) | Horizontal 16:9 (this plan) |
|---|---|---|
| Winning moment | one punchy line, hook in 3s | narrative arc, debate/tension, payoff |
| Length | 20-45s | 60-300s |
| Framing | active-speaker crop / split-screen | wide two-shot, reaction faces are an asset |
| Reframe cost | high (face tracking) | low (near-passthrough, `reframe=false`) |
| Platform | Shorts, Reels, TikTok | YouTube in-feed, X, LinkedIn desktop |

So the work splits in two: **selection** (a second scoring profile + a dialogue-tension signal) and **framing** (which gets *simpler*, since 16:9-from-16:9 is scale/pad, not crop).

---

## Verified coupling inventory

An adversarial audit (7 parallel readers + 8 refutation passes + synthesis) found the real surface is **~31 distinct aspect-ratio coupling sites (~56 raw literals, ~66 edit points including format threading + duration caps)**, not the 8 originally mapped. The audit **refuted 4 design assumptions** — those corrections are baked into the phases below:

1. **Captions are NOT relative.** `HormoziCaptions`/`SubtleCaptions`/`KaraokeCaptions` read only `fps` from `useVideoConfig()`; `BrandedCaptions` also reads `height` (only for a face-avoid nudge), never `width`. All font sizes/margins/logo box/side insets are absolute pixels in `remotion/src/types.ts` STYLES tuned for 1080x1920. Changing composition dims alone leaves captions ~1.8x oversized floating mid-frame. **Caption geometry is the biggest blind spot** and is net-new plumbing across 4 layers (Python cmd → `render.mjs` CLI flag → `inputProps` → `CaptionedClip` prop), plus the ASS fallback (`captions_burn.py`, `caption_renderer.py` PlayRes, `caption_styles.py` margins).
2. **The MCP "two schemas" was wrong.** The handler `inputSchema` objects (`create-clip.handler.ts:33`, `batch-clips.handler.ts`) are **dead code** (`server.tool()` uses only `.name`/`.description`). The real gates are (a) the inline Zod shape in `src/server.ts` (MCP, strips unknown keys), and (b) `src/ui/web-server.ts` — the **primary** path — which manually destructures `req.body` and builds the Python payload with an explicit allowlist (`styledClips` whitelist ~`2671`, `/api/create-clip`, `/api/batch-clips`). A `format` param must be added at ~10 hops or it silently reverts to vertical with no error.
3. **`fit_to_frame` is NOT redundant** with `crop_to_vertical`'s center branch. That branch blur-pillarboxes wider-than-target sources and crops top/bottom off narrower ones; only pixel-exact 16:9 passes through. Real-world near-16:9 inputs (1920x1088, 2.39:1, 4:3 inserts) are mishandled. Keep `fit_to_frame` as a distinct scale+pad/letterbox path.
4. **Render canvas dims are already dynamic.** `render.mjs:170-238` ffprobes the cropped clip and overrides Remotion composition width/height at `renderMedia` time, so a 1920x1080 source auto-produces a 1920x1080 overlay (DaVinci ProRes overlay too). `Root.tsx:38-39` literals govern only Remotion Studio preview. So horizontal render is *dimension*-correct for free; it is *caption-layout*-broken until profile work lands.

### Two hard caps that block horizontal from functioning
- `backend/services/clip_generator.py:711` — `if duration > MAX_CLIP_DURATION: raise ValueError` (45s). A 60-300s clip crashes here. Must become `spec.dur_max`-aware.
- `src/ui/web-server.ts` — 180s HTTP duration cap on `/api/create-clip` and `/api/batch-clips`. Rejects horizontal at the boundary before Python.

### Deferred / do-not-touch
- **Thumbnails** are a separate ~4-file / ~12-literal 9:16 track (`thumbnail_ai.py`, `thumbnail_generator.py`, `thumbnail_html.py`, `ThumbnailTemplate.tsx`). Not part of clip FormatSpec. Horizontal clips get portrait thumbnails until Phase 5.
- **Latent-safe at 9:16, only matters for a non-vertical *reframe* format (square):** `face_analysis.py:171` and the `crop_to_vertical:170` call into `_detect_local_speaker_reframe_plan` (which omits `target_ratio`, so `local_reframe.py:188` stays hardcoded 9/16). Fix only when square/reframe lands.
- **Native/dist:** `formats.py` auto-propagates into `cli/internal/backend/files/` (go:embed) via the sync step; contributors must run `go generate` before a native build. Any Phase-5 CSS variant must land in `src` + `dist/ui` + `dist/studio` copies.

---

## Phases

### Phase 1 — Parameterize rendering (SAFE, no behavior change) ✅ DONE

Verified **byte-identical for the vertical default** (341/341 backend tests pass; smoke-checked that duration aliases hold 20/45/20/35, affected importers load, and `crop_to_vertical`'s `target_dims` default is `(1080,1920)`). Two guardrails were mandatory and applied:

- **Guardrail A — keep duration constants as aliases.** `cli.py:70,398`, `clip_generator.py:31`, `claude_suggest.py:21`, and `DEFAULT_PRESET` itself import `MIN/MAX/TARGET_CLIP_DURATION` by name; deleting them ImportError-crashes all four. They now derive from `FORMATS["vertical"]` but keep the exact module-level names.
- **Guardrail B — change only the crop-path literal.** Only `video_processor.py:103` was touched. Thumbnails, caption PlayRes, `clip_studio.py`, `render.mjs` fallback, and `Root.tsx` were left alone — they resolve to vertical anyway and rewriting them adds risk with no behavior benefit.

Edits shipped:
1. **NEW** `backend/services/formats.py` — `FormatSpec` (frozen dataclass) + `FORMATS` {vertical, horizontal, square} + `get_format()`. Imports nothing from `presets` (avoids cycle; `presets` imports from it).
2. `backend/presets.py` — duration constants now alias `FORMATS["vertical"].{dur_min,dur_max,target_min,target_max}`; added `"format": "vertical"` to `DEFAULT_PRESET` (inert; the `{**DEFAULT_PRESET, **saved}` merge back-fills old presets).
3. `backend/services/video_processor.py` — `crop_to_vertical(...)` gained `target_dims: tuple = (1080, 1920)`; line 103 is now `target_w, target_h = target_dims`.

### Phase 2 — Render horizontal (still defaults vertical) ✅ DONE

Verified end to end: a 1280x720 source rendered `format="horizontal"` → **1920x1080** via `chose=fit-letterbox` (face analysis skipped), and `format="vertical"` → **1080x1920** unchanged. 341 Python + 47 TS tests pass, `tsc` clean.

- **Render fork** in `clip_generator.py` step 2: `spec.reframe ? crop_to_vertical(..., target_dims=spec.dims) : fit_to_frame(..., spec.dims)`. New `fit_to_frame` in `video_processor.py` = `scale=…:force_original_aspect_ratio=decrease` + centered `pad` + `setsar=1` (letterbox, collapses to plain scale for exact-ratio), skips all face analysis. `spec = get_format(format)` resolved once and reused for the dur cap, progress label, and fork. (Kept the `crop_to_vertical` name for now; rename deferred.)
- **Caps relaxed:** `clip_generator.py` uses `spec.dur_max` (vertical still 45); the two web-server 180s HTTP caps use a format-keyed ceiling (horizontal→300, else 180 — *not* `spec.dur_max`, which would tighten vertical).
- **`format` threaded end to end**, defaulting vertical at every hop: `generate_clip` signature + result dict; `main.py` create/batch; **6** `cli.py` `generate_clip` call sites + `--format` flag + `_selection_signature` cache key; `server.ts` create/batch Zod + primary clip literal + export-selected/clip-numbers maps + `findDuplicate`/`record`; `web-server.ts` create-clip destructure/validation/payload/history/recipe + MCP-export resolver + `styledClips` whitelist + batch per-clip cap + `createBatchHistoryRecorder`; `models/index.ts` types; `clips-history.ts` `findDuplicate` (backward-compatible, missing→vertical, so a horizontal re-clip of a vertical range is no longer a false duplicate).
- `Root.tsx`/`render.mjs` untouched (ffprobe already sizes the canvas). Output is dimension-correct; **captions still use vertical geometry** until Phase 3 (conscious deferral, not a silent bug).

### Phase 3 — Correct captions per format (the real blind spot)
- Add a `caption_profile` carrying per-format geometry (fontSize, margins, logo box, side insets). Wire a `--format`/`--caption-profile` flag through `render.mjs` → `inputProps` → `CaptionedClipProps`, and add a `lower_third` profile to `types.ts` STYLES + the 4 Remotion caption components (make absolute pixels profile-driven).
- Mirror in the **ASS fallback** (default path, `allow_ass_fallback=True`): thread real dims into `captions_burn.py:27`, `caption_renderer.py` PlayRes (`17`,`501`), `caption_styles.py:73` margins; wire up the already-parameterized-but-dead `_calibrate_libass_y`.

### Phase 4 — Iterate the AI for horizontal viral moments (the product)
- **Second scoring profile.** Keep `standalone/hook/relevance/quotability` as `vertical_score`; add `arc/tension/depth/payoff` as `horizontal_score` in `claude_suggest.py:_build_prompt`. New KB file `.podcli/knowledge/04b-longform-creation-guide.md` loaded alongside `04-shorts-creation-guide.md`. Moment schema (`suggest-clips.handler.ts`) gains both scores (keep `score` alias). Detect once, rank twice.
- **Dialogue-tension signal** (highest-leverage new AI): a function in `audio_analyzer.py` next to `compute_energy_scores` combining speaker-turn frequency (from diarization labels) + sustained energy → catches debate/back-and-forth that vertical spike-scoring misses. Feeds `horizontal_score`.
- Make the LLM duration window format-aware (`claude_suggest.py:241-311,433` currently bake 20-45s into the prompt).

### Phase 5 — UI + learning loop + more renderers
- ContentStudio format selector + "render both"; 16:9 preview variants in `styles.css` (+ `dist` copies) and `EpisodeWorkspace.jsx:77` (`PROD_TO_PCT` hardcodes 1920).
- Feed both format performance streams into the YouTube learning loop ([studio-phase-1] roadmap) → learn format × moment-type winners → back into scoring.
- Horizontal thumbnails (16:9 / 1280x720) as a deliverable. Then the cheap renderers off the same spine: audiograms, quote cards (reuse `thumbnail_ai.py`), show notes / X threads (LLM over the transcript).

---

## Sequencing guarantee

Each phase is independently mergeable and leaves `main` green. Phase 1 already is (behavior-identical). Phases 2-3 ship behind the default-vertical flag (no existing user sees a change until they pick horizontal). Phase 4 is where the product value lands.

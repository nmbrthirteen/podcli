# DaVinci Resolve integration

Exports podcli shorts as a DaVinci Resolve FCPXML 1.10 project. Each short
lands on the master timeline as a compound clip; double-clicking the compound
reveals V1 source + V2 ProRes 4444 alpha caption overlay as separate editable
layers.

Targets free + Studio Resolve 20.x on macOS, Windows, Linux. No Studio gates
on the import path (FCPXML import, compound clips, alpha auto-detect, SRT
subtitle import, markers — all confirmed free-tier).

## What it does (and doesn't)

- ✅ Source clip on V1, alpha caption overlay on V2 — Resolve auto-detects the
  alpha and composites identically to podcli's baked render.
- ✅ Per-short compound clip on the master timeline. Double-click to dive into
  the nested timeline and edit individual layers.
- ✅ Editable cuts, trims, audio, color grading on top, sidecar SRT.
- ⚠️ Reframe (face-track Pan/Tilt) is **baked into the V1 source** — Resolve's
  FCPXML importer mis-translates transform keyframe values, so we don't emit
  them. To re-frame, swap V1 with the untouched source clip and add your own
  Pan/Tilt, or re-render in podcli with different params.
- ⚠️ Caption animation curves (Hormozi spring physics, karaoke highlight) are
  inside the V2 alpha overlay. Swap the overlay by re-rendering in podcli with
  a different `--style` — no per-word edits inside Resolve.

## Free Resolve caveat

Resolve 20.2 silently watermarks the timeline if **any** Studio-only effect
appears anywhere on it, even unused. The emitter is constrained to free-tier
structure only — no `<adjust-blend>`, no Studio-gated nodes, no FCPXML
transform keyframes (which Resolve mis-translates anyway).

## Spike verification

The spike answers one open question: _does an FCPXML emitted from this
module, referencing a ProRes 4444 alpha overlay inside a compound clip,
round-trip into free Resolve and render identical to podcli's bake?_

Run from `backend/` so the `services.*` import path resolves, or set
`PYTHONPATH=backend`.

### A. Structural check (5 minutes, no re-render)

Confirms Resolve accepts the FCPXML, the compound clip appears, and the
alpha overlay auto-detects on V2 above V1. Uses any baked `*_short.mp4`
as V1 plus a synthetic ProRes 4444 alpha test pattern as V2.

```bash
# Generate a 1080x1920 ProRes 4444 alpha test pattern matching the source's duration.
DUR=50  # seconds — match (or undershoot) the source duration
ffmpeg -y -f lavfi -i "color=c=red@0.0:s=1080x1920:r=24:d=$DUR" \
  -vf "drawbox=x=140:y=1500:w=800:h=180:color=yellow@0.85:t=fill, \
       drawtext=text='ALPHA OVERLAY':fontcolor=black:fontsize=110:x=190:y=1545" \
  -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le \
  /tmp/test_alpha.mov

cd backend
python3 -m services.integrations.davinci_resolve.cli \
  --title "spike_structural" \
  --source ../data/output/your_short.mp4 \
  --captions /tmp/test_alpha.mov
```

Import the resulting `.fcpxml` into Resolve. **Pass criteria:** compound
clip appears on V1 of the master timeline; double-clicking reveals V1
(source) + V2 (alpha overlay) stacked; the yellow `ALPHA OVERLAY` box
appears over the source where the alpha is opaque, with the source fully
visible elsewhere.

### B. Pixel-diff check (full re-render)

Confirms Resolve's composite of (clean source + alpha captions) renders
visually identical to podcli's baked output.

Requires a clean cropped-but-uncaptioned intermediate from podcli. Today
the pipeline discards this intermediate; running render.mjs directly with
`--keep-overlay` is the supported path:

```bash
node remotion/render.mjs \
  --video path/to/cropped_clip.mp4 \
  --words path/to/words.json \
  --style branded \
  --output /tmp/spike_baked.mp4 \
  --keep-overlay
```

That produces:

- `/tmp/spike_baked.mp4` — baked composite (the ground-truth render)
- `/tmp/spike_baked_captions.mov` — ProRes 4444 alpha overlay

Then:

```bash
cd backend
python3 -m services.integrations.davinci_resolve.cli \
  --title "spike_pixel" \
  --source path/to/cropped_clip.mp4 \
  --captions /tmp/spike_baked_captions.mov
```

Import into Resolve, render the master timeline (Deliver → H.264, source
resolution and fps, CRF 18) to `/tmp/resolve_render.mp4`, then diff:

```bash
ffmpeg -i /tmp/spike_baked.mp4 -i /tmp/resolve_render.mp4 \
  -filter_complex "[0:v][1:v]blend=all_mode=difference" \
  -t 5 /tmp/diff.mp4
```

Expect a near-black diff. Encoder differences (libx264 vs Resolve's H.264)
produce minor noise; <5% mean luma is pass.

## Architecture notes (for future integrations)

This module sits in `backend/services/integrations/davinci_resolve/`.
Adding a sibling editor (Premiere, FCP, CapCut) means:

1. Create `backend/services/integrations/<editor>/`
2. Subclass `IntegrationBase` from `..base`
3. Consume the same `Project` IR from `.._shared.timeline_ir`
4. Emit the editor's project file in `emitter.py`
5. Register the integration in `__init__.py`

The `_shared.fcpxml` module is reusable by any FCPXML-consuming editor
(Final Cut Pro X, Premiere via its FCPXML importer).

## Production wiring

- MCP: `manage_integrations` (enable `davinci_resolve`) and `export_to_davinci_resolve`
- Web UI: `/integrations.html` toggle
- Python: `run_integration_tool` in `backend/main.py`
- Caption overlay for V2: `node remotion/render.mjs ... --keep-overlay` (writes `*_captions.mov` beside the output)
- Still manual: per-short `source_path` + optional `captions_path` must be supplied to the export tool
- Optional future work: `--keep-intermediate` on `clip_generator.py` to preserve cropped source in one command

# podcli → Generalized moment detection (multi-signal saliency engine)

> Goal: one detector that works across content types. The podcast clip selector and a new "auto-cut the funny/interesting moments" highlighter (party videos, vlogs, action, streams) become **two profiles of one engine**, not two features. Anchor use case that surfaced the need: a folder of party clips, "cut me the funny bits" — any language, no labels. Podcast selection is preserved as the default profile.

## North star

```text
one long video (any content, any language)
  → extract N signal channels (transcript, energy, laughter, prosody, motion, face)
  → per-video-normalize each into a 0..1 saliency curve on a common time grid
  → per-PROFILE weighted fusion → one interestingness curve
  → peak-pick (NMS + prominence) → reaction-expand → snap to natural boundaries
  → candidate moments → (render to formats via the existing fan-out)
```

## The one decision everything hangs on

**Content profile is orthogonal to output format.** `FormatSpec` (9:16 / 16:9 / 1:1) is a *render-time* aspect-ratio decision. A **`ContentProfile`** (podcast / party / action / auto) is a *detection-time* decision about **which signals matter and how candidates are generated**. A moment is detected under a profile, then rendered to one or more formats. Do not overload `FormatSpec.score_key` for this — mirror its pattern in a separate `ContentProfile` spec.

The existing transcript-LLM selector is **one channel** in this engine (the `transcript_semantic` channel), not the whole thing. That is the reframe: today the LLM *is* selection; tomorrow it is the dominant channel for the podcast profile and near-zero for party.

## Why this generalizes (research-backed)

Multi-signal highlight detection is a solved shape: compute a per-modality saliency curve, normalize **per-video** (never global), fuse (weighted sum with soft-OR bias, audio weighted heaviest for human-centric video), peak-pick, and for reaction-driven content extend **backwards from the reaction** to the cause. FunnyNet-W: a funny moment is "an n-second clip *followed by* laughter," audio carries >50% of the decision, optimal look-back ≈ 8s. Cai/Lu/Cai built laughter+applause+cheer detection *for home videos* at ~93% recall. All of it is unsupervised — no labeled data, only pretrained off-the-shelf models. See the audio-model and fusion research threads (this session) for citations.

## Profiles as concrete weight vectors

Channels each emit a per-video-normalized 0..1 curve; a profile is a weight vector + candidate-source + peak params.

| Channel | podcast | party | action | Source status |
|---|---|---|---|---|
| `transcript_semantic` (LLM dialogue/quotability) | **0.5** | 0.0 | 0.1 | REUSE `claude_suggest.py` |
| `prosody` (pitch/energy/rate spikes) | 0.2 | 0.2 | 0.2 | NEW (numpy, no librosa) |
| `audio_event` (laughter/cheer/applause/scream) | 0.15 | **0.4** | **0.3** | NEW (YAMNet-ONNX, no torch) |
| `energy` (RMS, peak-weighted) | 0.1 | 0.2 | 0.2 | REUSE `audio_analyzer.py` |
| `motion` (optical flow + scene-cut density) | 0.0 | 0.1 | **0.2** | PARTIAL (cuts exist; flow NEW) |
| `face_reaction` (mouth activity / smile proxy) | 0.05 | 0.1 | 0.0 | REUSE `face_analysis.py` (crude) |
| **candidate source** | `llm` | `saliency` | `saliency` | — |

Weights are starting points, tuned later. `auto` = a cheap first pass picks the profile: clean speech + 1-2 stable faces → podcast; sparse speech + high motion + crowd/laughter audio → party; sustained high motion → action.

## Two candidate-generation modes (the key to not regressing podcast)

- **`llm` (podcast):** the existing `claude_suggest` flow generates candidates over the whole transcript, exactly as today. The fused signal score blends in at the ranking seam. **When party channels have weight 0, output is byte-for-byte the current behavior.** This is the safety property.
- **`saliency` (party / action):** the fused curve drives candidate generation directly — `find_peaks` → reaction-expand → boundary-snap → candidate windows. Works with **no usable transcript**. The LLM is optional here, used only to title/caption the already-chosen windows.

## Architecture — new modules

1. **`backend/services/audio_events.py`** (NEW, no-torch)
   - YAMNet exported to a **self-contained ONNX graph** (raw 16 kHz waveform in, log-mel baked into the graph → **no librosa needed**). ~15 MB, Apache-2.0. AudioSet classes `Laughter`, `Cheering`, `Applause`, `Screaming` are native outputs (+ Giggle/Belly-laugh/Whoop children).
   - Reuse `transcription_whispercpp._extract_wav` (`-ar 16000 -ac 1`) for audio; **do not import `speaker_detection`** (drags in torch).
   - Returns per-window (≈1 Hz, matching energy) class probabilities → one 0..1 curve per trigger class.
   - Runtime: `onnxruntime`. **First test whether `cv2.dnn` (already loads YuNet `.onnx`) can run yamnet.onnx** — if yes, zero new deps.
   - Ship `backend/models/yamnet.onnx` + a **dev-only** `scripts/export_yamnet.py` (uses tf2onnx; never shipped or imported at runtime).

2. **`backend/services/saliency.py`** (NEW)
   - `normalize_per_video(curve)` — robust z-score via numpy median/MAD, or percentile-rank. **Never global.**
   - `fuse(channels, profile)` — weighted sum with soft-OR bias.
   - `pick_peaks(curve, profile)` — **numpy reimplementation of `find_peaks`** (~40 lines: height floor `mean + kσ`, min-distance NMS by descending height, prominence filter, min-width). Avoids adding scipy. Keep peaks above the top ~10-15th percentile of the video's **own** prominence distribution (adaptive count).
   - `reaction_expand(peak, events)` — if the peak's driver is an `audio_event` class, move anchor to reaction onset, extend ~8 s back, keep 1-3 s of payoff.
   - `snap_boundaries(window)` — snap start/end to nearest {Whisper sentence-end, silence gap >300 ms, scene cut, motion-quiet local min} within ±500 ms.

3. **`backend/services/profiles.py`** (NEW) — `ContentProfile` dataclass (name, `channel_weights`, `candidate_source`, peak params, reaction-expand params, boundary sources) mirroring `FormatSpec`; `get_profile(name)`; `auto_detect(signals)`.

4. **`motion` channel** (Phase 3) — `cv2.calcOpticalFlowFarneback` on sampled frames (**OpenCV already present**) + reuse `local_reframe.count_scene_cuts` / `clip_generator._detect_scene_cuts`.

5. **`prosody` channel** (Phase 2/3) — numpy F0 (autocorrelation) + short-term energy + speech-rate vs speaker baseline. No librosa.

## Dependency deltas (no-torch native path)

- **ADD** `onnxruntime` to `requirements-runtime.txt` — the only new hard dep. **Settled by spike:** OpenCV-DNN *cannot* run the YAMNet graph (rejects the dynamic-shape mel front-end: `dynamic 'zero' shapes are not supported`), so the zero-new-dep route is out. `onnxruntime` still pulls in no torch/TF.
- **NO** librosa (self-contained waveform-in graph, confirmed), **NO** torch/TF at runtime.
- `scipy` is present in the dev venv (so `find_peaks` works there) but is **not** in `requirements-runtime.txt` — either add it or keep the numpy peak-pick reimpl for the hermetic runtime. Decide at Phase 2.
- Optical flow + face already covered by `opencv-python-headless`.
- `yamnet.onnx` weight (~16 MB) committed to `backend/models/`; no export step needed — a self-contained waveform-in export already exists at HF `zeropointnine/yamnet-onnx` (input `waveform` [dynamic], outputs `[frames,521]` scores + `[frames,1024]` embeddings + `[frames,64]` mel). Verify its license before committing; keep a dev-only `scripts/export_yamnet.py` (tf2onnx) as the reproducible fallback.

## Spike results (validated this session)

Ran the self-contained YAMNet ONNX on real podcli audio (a 38 s Deeptech Decoded clip, extracted with podcli's exact `-ar 16000 -ac 1`):
- **No-torch confirmed:** `torch`/`tensorflow` absent from the process before and after inference (onnxruntime only).
- **Correct classification:** Speech 0.774 (dominant, correct for a talking clip), Silence 0.186, and Laughter/Cheering/Applause/Screaming all exactly 0.000 — correct negative control (serious monologue, no laughter). Mel front-end baked in the graph → featurization provably correct, no code to own.
- **Negligible cost:** 98 ms for 38 s of audio (~380× realtime); a 3 h video ≈ 28 s of audio-event analysis.
- **Positive reaction detection confirmed on podcast content.** Swept 32 real clips: the detector ranked the one comedic show + two chuckle moments above the dry technical majority (which scored exactly 0.00). Zooming in, laughter fires as a **sharp 1-2 frame spike anti-correlated with Speech** (e.g. laugh=0.23 while speech drops 1.00→0.11 at 13.5s, then back to speech) — the exact reaction signature the 8s-backward-expansion relies on. Absolute values are modest (0.23-0.25 for brief chuckles in pre-cut clips) but that is a non-issue: a spike on a 0.00 baseline is trivially caught by **prominence-based peak-picking**, which is why per-video normalization (not absolute thresholds) is mandated above.
- **Still worth a check (needs real party footage):** amplitude/robustness on noisy handheld party audio with music and overlapping speech — YAMNet's known soft spot. If weak, AST ONNX (`onnx-community`, pre-exported, mAP 0.485) is the drop-in fallback; needs a 128-bin log-mel numpy front-end.

## Integration seams (from the code map)

1. **Detect-once hub** — `backend/cli.py` ~658-720 (and MCP twin `backend/main.py:handle_suggest_clips` ~358). Energy is *already* computed once here (`get_energy_profile`) and `face_map` extracted. All new channels compute here; fusion produces the saliency curve.
2. **Scoring merge** — `claude_suggest.suggest_with_claude` normalization (~999) + `_select_top_by_score` (~687). Blend the LLM score in as the `transcript_semantic` channel; key the weights off the active `ContentProfile`.
3. **Transcript-packer co-location** — `transcript_packer.pack_transcript` (~274) already flags top-RMS moments for the in-chat MCP agent. Add laughter/event/motion flags so the conversational path also sees them.

## Contract-change tax

A new `profile` param threads the **same ~12 hops the `format` field did**: `suggest-clips.handler.ts` inputSchema, `src/models/index.ts` types, `src/server.ts` Zod (dual-declaration), `src/ui/web-server.ts` `styledClips` whitelist, `python-executor` stdin, `main.py` params, and `cli.py`'s `generate_clip` call sites — or it silently reverts to the default. Follow the `format` precedent exactly.

## Phasing

- **Phase 0 — profile scaffolding, zero behavior change.** [DONE, PR #43 / Phase 2 branch] `ContentProfile` abstraction added (`profiles.py`); `profile` param threaded through the CLI (`--profile`, config, selection signature). Python-side default `podcast` reproduces current selection. TS-side threading (MCP/web `profile` param) still open — see below.
- **Phase 1 — audio-event channel (the isolated valuable core).** [DONE, PR #43] YAMNet-ONNX laughter/cheer/applause/scream (`audio_events.py`) computed in the detect-once hub; surfaced in the packed transcript and CLI heuristic. Podcast output unchanged unless the channel is weighted. Validated on 32 real clips.
- **Phase 2 — fusion engine + saliency candidate source + party profile (audio-only).** [DONE, this branch] `saliency.py`: per-video robust-z normalization, weighted fusion, numpy peak-pick (NMS), reaction dilation, reaction-first candidate generation, 8 s backward expansion, quiet-point boundary snap. Wired to `--profile party|action` in `podcli process`. Deterministic on clean input; reactions detected with correct run-up windows. **Gate remaining: tune thresholds/weights on real party footage (synthetic podcast-clip concat is not representative).**
- **Phase 3 — visual channels + action profile + multi-file pooling.** Optical flow (OpenCV) + face-reaction channels; action profile; pool peaks across a *folder* of clips and rank globally ("best 15 bits from tonight" across 80 phone videos). **Gate: catches a silent visual gag; folder-level ranking works.**
- **Phase 4 (optional) — highlight reel renderer.** Ordering, pacing, optional music-bed ducking, transitions — a thin renderer atop the detected moments, reusing the clip-render stack.

## Guardrails

- **Per-video normalization is mandatory** — party clips vary wildly in level/room/camera; global normalization is wrong.
- **Podcast profile must reproduce current behavior** when party channels are weight 0. Ship behind the profile param; default is safe. This is the non-regression contract.
- **`find_peaks` `distance` param is the NMS / min-gap** — it deletes lower peaks within the gap automatically. Adaptive clip count = top percentile of the video's own prominence distribution, not an absolute N.
- **YAMNet ONNX must never import torch/TF at runtime.** The export step is dev/CI only; the native runtime installs `onnxruntime` (or reuses OpenCV-DNN) and feeds raw 16 kHz samples.
- **Reaction-expansion only fires for saliency candidates** whose trigger is an audio-event class — never rewrite LLM-chosen podcast windows.

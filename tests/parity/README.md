# Parity harness — keeping every feature correct through the native-CLI migration

This harness is the safety net for `plans/native-cli.md` (Go launcher, hermetic
runtimes, **whisper.cpp** replacing openai-whisper/PyTorch). Its job: prove that
swapping the transcription engine and relocating the runtime does **not** change
what podcli produces.

## The correctness model: two layers split by a contract that already exists

The transcript JSON `{words, segments, ...}` is already a stable, multi-producer
contract (produced by `transcribe_file`, `parse_speaker_transcript`, raw JSON
import, **and** the on-disk cache; consumed by corrections, cropping, moment
selection, and captions). That seam lets correctness decompose into two layers
that are verified independently.

### Layer 1 — everything *downstream* of the transcript JSON
Moments → crop → captions → normalize → encode. **This code does not change** in
the migration; only the runtime relocates (system → hermetic). So the rule is
absolute: a fixed transcript must yield identical output. Any difference is a
runtime *pinning* bug, never a logic change.

- `transcript_synthetic.json` — neutral, no podcast content; packs the word-text
  edge cases (leading-space token, number+symbol, punctuation, apostrophe,
  whitespace-only token, zero-duration token, speaker change).
- `test_caption_parity.py` — renders all four caption styles from that transcript
  and diffs against committed goldens (`golden/*.ass.expected`). Also pins the
  **word-text normalization** the whisper.cpp boundary must reproduce exactly
  (the single highest-risk integration detail) and the 50ms zero-duration floor.

Run it (fast, no media, CI-friendly):

```
venv/bin/python3 -m pytest tests/parity/test_caption_parity.py -q
```

Intentionally update goldens after a deliberate change:

```
UPDATE_GOLDENS=1 venv/bin/python3 -m pytest tests/parity/test_caption_parity.py -q
```

### Layer 2 — the engine (`audio → transcript JSON`)
The only real change. Verified by comparing the new engine's output to a frozen
openai-whisper baseline, with **forgiving** tolerances — because the caption
pipeline already runs in production on evenly-spaced *synthetic* word timings
(`transcript_parser.py:306`), so absolute timestamp fidelity is a quality nicety,
not a correctness requirement.

1. Capture the baseline **now**, while openai-whisper still works. Drop a few
   short representative clips into `tests/parity/local/` (single speaker, two
   speakers, music-heavy, fast speech), then:

   ```
   venv/bin/python3 tests/parity/capture_baseline.py
   ```

   Writes `baseline/<stem>/` = transcript.json + metrics.json + captions per style.

2. Later, run whisper.cpp into `candidate/<stem>/transcript.json` and gate:

   ```
   venv/bin/python3 tests/parity/compare.py <stem>
   ```

   Checks WER and word-timestamp drift (median/p95) against thresholds
   (`PARITY_MAX_WER`, `PARITY_MAX_MEDIAN_DRIFT`, `PARITY_MAX_P95_DRIFT`). Nonzero
   exit = regression; wire it into CI as the swap gate.

## Why this makes "everything still works" tractable

- **Layer 1 is identical by construction** — pinned runtime + frozen-transcript
  goldens. The boring 80% can't drift silently.
- **Layer 2 is bounded against a floor the app already ships** — whisper.cpp only
  has to beat evenly-spaced timings, which it does trivially.
- **The cache protects existing work** — already-transcribed videos reuse their
  openai-whisper JSON, so they produce byte-identical output under the new binary.
- **Dual-engine release** (`--engine whisper-py`, planned) gives an instant
  real-world fallback while whisper.cpp proves itself.

## What is committed vs local

Committed: this README, `transcript_synthetic.json`, `golden/*.ass.expected`,
the harness scripts. **Never committed** (`.gitignore`): `local/` fixtures,
`baseline/`, `candidate/` — they can contain podcast content.

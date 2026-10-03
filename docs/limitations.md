# Limitations

What each stage can and cannot do today. This is a plain statement of current
behavior, not a roadmap.

## Multicam sync

- Sync is audio-only: coarse cross-correlation of onset envelopes, then a
  fine GCC-PHAT match with linear clock-drift correction
  (`backend/services/multicam_signal.py`). There is no timecode-based sync;
  embedded timecode is read as session metadata only
  (`backend/services/multicam.py:278-282`).
- A camera with no audio track cannot be synced automatically. Its offset
  must be set by hand (`backend/services/multicam.py:1082`).
- Remote call recordings (Zoom, Riverside, one file per person) are assumed
  to start together, since there's no shared audio to cross-correlate
  against.

## Auto-cut to speaker

- Speaker assignment comes from per-person microphone levels, or from
  imported diarization turns. There is no video-based speaker detection
  (face tracking, lip sync).
- Short interjections are suppressed so a quick "mm-hm" doesn't steal the
  cut, which means genuine quick back-and-forth exchanges can read as
  slower-paced than they were.
- Call-recording layouts (one file per person as a split screen, one
  gallery recording split into a tile per camera) render to MP4 fine but
  cannot export to Premiere XML or FCPXML yet (`src/server.ts`,
  `manage_multicam` tool description).

## Transcription

- Four engines: `whisper-py` (OpenAI Whisper, default), `whispercpp`
  (local, no network), `omnilingual` (local, ~1600 languages) and
  `assemblyai` (`backend/services/engines.py`).
- `omnilingual` runs Meta's Omnilingual ASR 1B CTC model on the CPU. The
  first run downloads 1 GB. It returns lowercase text with no punctuation,
  so captions and sentence splits come from pauses alone. It takes no
  language hint; test a 40 s sample before a full run, and compare it with
  `compare_transcription_engines`.
- Speaker diarization runs on the `whisper-py` and `assemblyai` paths.
  `whispercpp` and `omnilingual` never produce speaker labels. Every word comes back with
  `speaker: null` (`backend/services/transcription_whispercpp.py:67,215`).
- Diarization needs `HF_TOKEN` set (see `docs/configuration.md`). Without
  it, or if diarization fails for any other reason, transcription still
  completes. It just comes back with no speaker labels, degrading
  silently rather than failing the job.
- Language support follows whichever engine is in use; podcli does not add
  or restrict language coverage beyond what Whisper, whisper.cpp,
  Omnilingual ASR, or AssemblyAI support natively.

## Captions

- Four built-in styles: `hormozi`, `karaoke`, `subtle`, `branded`
  (`backend/services/caption_renderer.py`).
- Burn-in (rendered into the video via ASS/libass) is the primary path.
  Soft `.srt` export also exists, and `.srt`/`.vtt` transcripts can be
  imported directly.
- Non-Latin scripts (Georgian, Russian, etc.) render through font-fallback
  stacks, not through the primary brand font. The brand font only draws
  Latin glyphs. Expect a visibly different typeface on non-Latin captions
  until a matching family is bundled for the style in use.

## Thumbnails

- Layout and copy are generated together: an LLM writes the HTML/CSS, and
  Playwright renders it (`backend/services/thumbnail_ai.py`).
- Split-screen/multicam source frames are detected and cropped to the half
  containing the speaking face.
- The `pair` layout places two people side by side, each cut from their own
  frame of the clip. It tells people apart only by seat (two faces in one
  shot or two call panes) or by a multicam edit whose render is the clip's
  source. A single camera that cuts between people falls back to one face.
- Without a multicam edit, the guest is the speaker who talks more in the
  clip. Without diarized speakers mapped to seats, sides follow the footage.
  `--swap` or explicit images fix either case.
- Headline copy is written to match the clip's theme, not quoted verbatim
  from the transcript. There's no automated check that the headline's
  claim matches what's actually said in the clip.

## Exports

- Supported targets: DaVinci Resolve FCPXML, Premiere XML/FCPXML (via
  `manage_multicam`), and the podcli cloud editor.
- The cloud editor requires a podcli account and an active Pro plan.
- Call-recording-derived layouts (split screen, gallery tiles) cannot
  export to Premiere or FCPXML. See Auto-cut above.

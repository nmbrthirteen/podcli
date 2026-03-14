# /produce-shorts — Full Production Pipeline

> You are the executive producer for this podcast. You run the ENTIRE production pipeline from raw episode file to publish-ready shorts with complete content packages. One command, zero handoffs.

---

## Trigger

User says "produce episode", provides an mp4 + transcript, or wants the full end-to-end pipeline.

---

## Inputs

| Field | Required | How to Get |
|-------|----------|-----------|
| Episode video (.mp4) | Yes | User provides path or it's in `.podcli/working/uploads/` |
| Transcript | Yes | User provides file/text, or auto-transcribe with Whisper |
| Episode number | Yes | User provides or auto-detect |
| Guest name | Preferred | Auto-detect from transcript |
| Company/Org | Preferred | Auto-detect from transcript |
| Caption style | Optional | Default: branded (from presets or user) |
| Crop strategy | Optional | Default: face |

---

## The Full Pipeline (3 Phases, 10 Steps)

### PHASE 1: VIDEO PRODUCTION (podcli engine)

#### Step 1 — Load Video
- Confirm the video file exists and is accessible
- If using the Web UI, call `POST /api/select-file` or check `get_ui_state`
- Note the file path for clip generation

#### Step 2 — Transcribe (if no transcript provided)
- Use the `transcribe_podcast` MCP tool or `POST /api/transcribe`
- Model: base (or user's preference)
- Wait for completion — this produces word-level timestamps + speaker labels
- Cache the result for reuse

#### Step 3 — If transcript is provided as text/file
- Parse it (supports Speaker (MM:SS) format, SRT, VTT, or JSON)
- Use `POST /api/parse-transcript` or the MCP tool
- Ensure word-level timestamps are generated

#### Step 4 — Analyze & Suggest Clips
- Read the full transcript
- Identify the 8-15 best moments based on:
  - Hook strength (first 3 seconds grab attention?)
  - Standalone value (makes sense without context?)
  - Energy/quotability (memorable phrasing?)
  - Audience relevance
- Use the `suggest_clips` MCP tool to submit suggestions
- Each suggestion needs: title, start_second, end_second, reasoning

#### Step 5 — Render Clips
- Use `batch_create_clips` with `export_selected: true`
- Settings: caption style from presets or user, crop strategy, logo if registered
- Wait for all clips to finish rendering
- Note output file paths and durations

**PHASE 1 OUTPUT:** Rendered .mp4 clips in `.podcli/output/`

---

### PHASE 2: CONTENT GENERATION (PodStack)

#### Step 6 — Process Transcript for Content
*Runs `/process-transcript` logic*

Using the same transcript from Phase 1:
1. Score each extracted moment (Standalone + Hook + Relevance + Quotability, 1-5 each)
2. Classify each by content type (Guest Story / Technical Insight / Market / Business / Hot Take)
3. Check for duplicates against `03-episodes-database.md`
4. Extract SEO keywords from the full transcript

#### Step 7 — Generate Titles
*Runs `/generate-titles` logic per clip*

For each rendered clip:
1. Extract the anchor — the single most non-obvious thing
2. Generate 8 title options following the show's title spec
3. Run the 6-point verification checklist
4. Flag top 2 picks
5. Narrow to 2-3 best per clip

#### Step 8 — Generate Descriptions + Thumbnails
*Runs `/generate-descriptions` and `/plan-thumbnails` logic*

For each clip:
- Shorts description: hook + attribution + link placeholder + hashtags
- Thumbnail text: podcast (16:9) lowercase + shorts (9:16) ALL CAPS

For the full episode:
- Long-form description with timestamps, guest links, bullet points
- Podcast thumbnail brief

#### Step 9 — Quality Review
*Runs `/review-content` logic*

4-pass review on ALL generated content:
1. Banned word scan (from `02-voice-and-tone.md`)
2. Voice & tone check
3. Title-specific review (length, keyword position, shapes)
4. Package completeness

Fix all blocking issues before output.

**PHASE 2 OUTPUT:** Complete content package

---

### PHASE 3: DELIVERY

#### Step 10 — Assemble & Save

Compile everything into the final deliverable and save to `episodes/`.

---

## Output Format

```markdown
# Episode [X]: [Guest] — [Company]
## Full Production Package

**Produced:** [Date]
**Video:** [filename]
**Transcript:** [source — Whisper/imported]
**Clips rendered:** [X]
**Keywords:** [comma-separated]

---

## Episode Summary
[2-3 sentences]

---

## Long-Form Episode Metadata

**Title Options:**
1. [Option 1]
2. [Option 2]
3. [Option 3]

**Description (ready to paste):**
[Full long-form description with timestamps, links, hashtags]

**Podcast Thumbnail:**
- Text: "[line 1] / [line 2]"

**Tags:** [comma-separated, under 500 chars]

---

## Shorts Package

### Short 1: [Title]

**File:** [output filename]
**Timestamp:** [XX:XX — XX:XX]
**Duration:** [X]s
**Category:** [Type]
**Score:** [X/20]

> "[Key quote]"

**Title options:**
1. [Best] ← PICK
2. [Alt]
3. [Alt]

**Thumbnail:**
- Podcast: "[line 1] / [line 2]"
- Shorts: "[LINE 1] / [LINE 2]"

**Description (ready to paste):**
[Complete shorts description with hashtags]

---

### Short 2: [Title]
...

---

## Posting Schedule

| Order | Short | Why This Order |
|-------|-------|---------------|
| 1 | [Title] | [Strongest hook — lead with this] |
| 2 | [Title] | [Different topic — variety] |
| ... | ... | ... |

---

## Publish Checklist

### Pre-Upload
- [ ] File names use keywords
- [ ] Titles under 60 chars
- [ ] Descriptions have hooks + hashtags
- [ ] Thumbnails briefed to designer
- [ ] End screen set to ONE specific video
- [ ] Playlist link hack set up

### At Publish
- [ ] Pin comment written
- [ ] Community tab post scheduled (+15 min)
- [ ] ManyChat keyword automation set

### First 24 Hours
- [ ] Reply to every comment
- [ ] Instagram Stories (3-part sequence)
- [ ] Update link in bio

---

## Quality Review
- **Blocking issues:** 0 (all resolved)
- **Status:** READY TO PUBLISH
```

---

## Post-Pipeline Actions

1. **Update episode database** — add to `.podcli/knowledge/03-episodes-database.md`
2. **Save package** — write to `episodes/ep[XX]-[guest]-production-package.md`
3. **Update clip history** — already tracked by podcli engine

---

## How to Use

### Fully automatic (one command)
```
/produce-shorts
```
Then provide the video path and transcript. Everything else is automatic.

### With specific inputs
```
User: /produce-shorts
      Video: /path/to/episode.mp4
      Transcript: /path/to/transcript.txt
      Episode: 7
      Guest: John Smith, Acme Corp
```

### What happens behind the scenes
1. Video gets loaded into podcli
2. Transcript gets parsed (or Whisper runs)
3. Best moments identified and scored
4. Clips rendered with captions and smart crop
5. Titles generated (8 per clip, verified)
6. Descriptions written (ready to paste)
7. Thumbnails planned (both formats)
8. Everything reviewed against brand voice
9. Package assembled and saved
10. Publish checklist included

**Total output: rendered .mp4 shorts + complete content package + publish checklist**

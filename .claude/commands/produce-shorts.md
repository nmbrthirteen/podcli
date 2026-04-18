---
description: Full pipeline from transcript to publish-ready content package
allowed-tools: Read, Write, Edit, Task
argument-hint: [transcript-file-or-episode-number]
triggers:
  - process episode
  - produce shorts
  - full pipeline
  - prep episode
  - make content package
  - run the full thing
---

# /produce-shorts — Producer

> You are the head producer. You run the full content pipeline from raw transcript to publish-ready package. This is the master orchestrator.

---

## Before Starting

Read the full knowledge base to understand the show:
- `knowledge/01-brand-identity.md` — who the show is
- `knowledge/02-voice-and-tone.md` — voice, banned words
- `knowledge/03-episodes-database.md` — existing content (dedup)
- `knowledge/04-shorts-creation-guide.md` — moment criteria
- `knowledge/05-title-formulas.md` — title spec
- `knowledge/06-descriptions-template.md` — description templates
- `knowledge/07-thumbnail-guide.md` — visual specs
- `knowledge/13-learnings.md` — past retro patterns (what worked, what didn't)

---

## Inputs

| Field | Required | Source |
|-------|----------|--------|
| Transcript | Yes | User provides (paste or file) |
| Episode number | Yes | User provides or auto-detect |
| Guest name | Preferred | Auto-detect from transcript |
| Company/Org | Preferred | Auto-detect from transcript |
| Focus areas | Optional | User specifies |

---

## Full Pipeline (6 Phases)

Each phase calls the corresponding skill's logic. Each phase reports its own Completion outcome.

### Phase 1: Transcript Processing
*Runs `/process-transcript` logic*

Extract guest info, flag 15-20 moments, score them, select top moments, classify by content type, check for duplicates.

### Phase 2: Title Development
*Runs `/generate-titles` logic per moment*

For each moment: extract anchor, classify, generate 8 options, verify, flag top 2. Narrow to 2-3 best per moment.

### Phase 3: Description Writing
*Runs `/generate-descriptions` logic*

Shorts descriptions (hook + attribution + link + hashtags) and full episode long-form description.

### Phase 4: Thumbnail Planning
*Runs `/plan-thumbnails` logic*

Two-line text for both podcast and shorts formats per moment.

### Phase 5: Quality Review
*Runs `/review-content` logic — Fix-First + specialist dispatch*

Parallel specialists: voice, banned words, title, standalone, clickbait, SEO, dedup. Auto-fix mechanical issues. Batch ASK items for user.

### Phase 6: Package Assembly

Compile into final deliverable. Save to `episodes/ep[XX]-[guest]-content-package.md`.

---

## Three-Strike Rule (Critical)

Each phase has a budget of 3 attempts. If a phase fails to produce valid output 3 times in a row, **STOP the pipeline** and return:

```
STATUS: BLOCKED
Failed phase: [phase name]
Evidence: [what went wrong, 3 attempts summarized]
Partial output preserved at: [path if any]
Needed to unblock: [specific ask]
```

Do not proceed to downstream phases when an upstream phase is BLOCKED — downstream output would be corrupt.

---

## Phase Handoff

Each phase consumes upstream output and passes structured state downstream. Phase 1's moment list is the contract — if it's empty, Phase 2 has nothing to do.

| Phase | Consumes | Produces |
|-------|----------|----------|
| 1 | Transcript | Moment list with scores, categories, quotes, timestamps |
| 2 | Moment list | 2-3 titles per moment + top picks |
| 3 | Moments + titles | Per-short + long-form descriptions with hashtags |
| 4 | Moments | Podcast + shorts thumbnail briefs |
| 5 | All of above | Fix log + ASK queue + suppressed appendix |
| 6 | Everything | Packaged markdown written to `episodes/` |

---

## Output Format

```markdown
# Episode [X]: [Guest] — [Company]
## Complete Content Package

**Prepared:** [Date]
**Transcript length:** ~[X] minutes
**Shorts extracted:** [X]
**Keywords:** [comma-separated]

---

## Episode Summary
[2-3 sentences]

---

## Long-Form Episode Metadata

**Recommended Title Options:**
1. [Option 1]
2. [Option 2]
3. [Option 3]

**Description:**
[Full long-form description]

**Podcast Thumbnail:**
- Text: "[line 1] / [line 2]"

**Tags/Keywords:** [comma-separated]

---

## Shorts Package

### Short 1: [Best Title]

**Timestamp:** [XX:XX — XX:XX]
**Duration:** ~[XX]s
**Category:** [Type]
**Score:** [X/20]

> "[Key quote]"

**Why it works:** [One sentence]

**Title options:**
1. [Best] ← PICK
2. [Alternative]
3. [Alternative]

**Thumbnail:**
- Podcast: "[line 1] / [line 2]"
- Shorts: "[LINE 1] / [LINE 2]"

**Description (ready to paste):**
[Complete description]

---

### Short 2: [Best Title]
...

---

## Handoff Checklist

### For Video Editor
- [ ] Timestamp ranges for each short
- [ ] Cut instructions (if any)

### For Thumbnail Designer
- [ ] Text briefs for all thumbnails
- [ ] Guest photo specs
- [ ] Layout templates

### For Publishing
- [ ] All titles finalized
- [ ] All descriptions with hashtags
- [ ] Posting order recommendation

---

## Pipeline Report (per CLAUDE.md Completion Protocol)

| Phase | Status | Notes |
|-------|--------|-------|
| 1. Transcript Processing | DONE | [X moments extracted] |
| 2. Title Development | DONE | [X titles per moment] |
| 3. Description Writing | DONE | [Shorts + long-form] |
| 4. Thumbnail Planning | DONE | [Both formats per moment] |
| 5. Quality Review | DONE / DONE_WITH_CONCERNS | [auto-fixes: X, ASK: X] |
| 6. Package Assembly | DONE | [Saved to episodes/ep[XX]...] |

**Overall status:** DONE / DONE_WITH_CONCERNS / BLOCKED
**Blocking issues resolved:** [X]
**Non-blocking concerns:** [list]
```

---

## Post-Pipeline

1. **Update episode database:** Add to `knowledge/03-episodes-database.md`
2. **Save package:** Write to `episodes/ep[XX]-[guest]-content-package.md`
3. **Posting order:** Vary topics, lead with strongest hooks, mix energy levels

---

## Completion

The Producer emits a rollup status based on the 6 phases:

- **DONE** — All 6 phases DONE, zero BLOCKING issues after review.
- **DONE_WITH_CONCERNS** — All phases completed, but review surfaced WARNING items that need user call, or one phase was DONE_WITH_CONCERNS.
- **BLOCKED** — Any phase hit the Three-Strike Rule or returned BLOCKED. Pipeline halted. Partial output preserved.
- **NEEDS_INPUT** — A phase needs missing input (episode number, guest name, banned-words file, etc.). Ask once, batched, then resume.

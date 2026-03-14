# /prep-episode — Producer

> You are the head producer. You run the full content pipeline from raw transcript to publish-ready package. This is the master orchestrator.

---

## Trigger

User says "process episode", "prep episode", provides a transcript with an episode number, or asks for a full content package.

---

## Before Starting

Read the full knowledge base to understand the show:
- `.podcli/knowledge/01-brand-identity.md` — who the show is
- `.podcli/knowledge/02-voice-and-tone.md` — voice, banned words
- `.podcli/knowledge/03-episodes-database.md` — existing content (dedup)
- `.podcli/knowledge/04-shorts-creation-guide.md` — moment criteria
- `.podcli/knowledge/05-title-formulas.md` — title spec
- `.podcli/knowledge/06-descriptions-template.md` — description templates
- `.podcli/knowledge/07-thumbnail-guide.md` — visual specs

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
*Runs `/review-content` logic*

4-pass review: banned words, voice check, title review, completeness. Fix all blocking issues.

### Phase 6: Package Assembly

Compile into final deliverable.

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

## Quality Review Summary
- **Blocking issues:** [X] (all resolved)
- **Warnings:** [X]
- **Status:** READY TO PUBLISH
```

---

## Post-Pipeline

1. **Update episode database:** Add to `.podcli/knowledge/03-episodes-database.md`
2. **Save package:** Write to `episodes/ep[XX]-[guest]-content-package.md`
3. **Posting order:** Vary topics, lead with strongest hooks, mix energy levels

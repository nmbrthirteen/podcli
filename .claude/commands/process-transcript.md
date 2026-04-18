---
description: Extract, score, and classify the best moments from a raw podcast transcript
allowed-tools: Read, Write
argument-hint: [transcript-file-or-paste]
triggers:
  - transcript
  - process transcript
  - extract moments
  - podcast transcript
  - here's a transcript
---

# /process-transcript — Content Analyst

> You are a senior content analyst. Your job is to take a raw podcast transcript and extract the best moments for YouTube Shorts, score them, and deliver a structured content brief.

---

## Before Starting

Read the knowledge base to understand this show's brand, voice, and existing content:
- `knowledge/01-brand-identity.md` — who the show is, positioning
- `knowledge/02-voice-and-tone.md` — voice fingerprint, banned words
- `knowledge/03-episodes-database.md` — existing episodes (avoid duplicates)
- `knowledge/04-shorts-creation-guide.md` — moment selection criteria
- `knowledge/05-title-formulas.md` — title patterns
- `knowledge/13-learnings.md` — patterns from past retros

If using podcli, replace `knowledge/` with `.podcli/knowledge/`.

---

## Inputs Required

| Field | Source |
|-------|--------|
| Transcript | User provides (paste or file path) |
| Guest name | Auto-detect from transcript |
| Company/Org | Auto-detect from transcript |
| Episode number | User provides or auto-detect |

If guest/company/episode can't be detected, return **NEEDS_INPUT** with one specific question.

---

## Execution Protocol

### Phase 1: Scan (Silent — Do Not Output)

1. Read the full transcript
2. Identify guest name, company/org, episode number
3. Extract primary topics discussed
4. Estimate episode length from transcript density
5. Determine target shorts count:

| Episode Length | Target Shorts |
|----------------|---------------|
| 30-45 min | 4-6 |
| 45-60 min | 6-8 |
| 60-90 min | 8-12 |
| 90+ min | 12-15 |

### Phase 2: Flag Moments (Silent)

Scan the entire transcript for:

- **Energy shifts** — speaker gets passionate, animated, emphatic
- **Surprising facts/stats** — numbers that make you pause
- **Counterintuitive insights** — challenges assumptions
- **Story beginnings** — origin moments, pivots, "When we first..."
- **Strong statements** — bold claims, definitive positions
- **Actionable wisdom** — concrete advice viewers can use today
- **Emotional peaks** — vulnerable admissions, passion, humor
- **Future predictions** — bold visions, "In 10 years..."

Flag 15-20 potential moments.

### Phase 3: Score Each Moment

For every flagged moment, score on four dimensions (1-5 each):

| Dimension | What It Measures |
|-----------|-----------------|
| **Standalone value** | Makes complete sense without episode context? |
| **Hook strength** | Grabs attention in first 3 seconds of the clip? |
| **Relevance** | Matters to the show's target audience? |
| **Quotability** | Contains memorable, shareable phrasing? |

**Total score = sum of 4 dimensions (max 20).**

### Phase 4: Select & Classify

1. Rank all moments by total score
2. Select the top moments (per target count)
3. Classify each into one of five content types:

| Type | What It Is |
|------|-----------|
| **Founder/Guest Story** | Key decision, pivot, hard moment, personal journey |
| **Product/Technical Insight** | How something works or why it matters |
| **Market / Landscape** | Maps space, compares options, calls trends |
| **Business / Strategy** | Revenue, fundraising, pricing, go-to-market |
| **Hot Take / Opinion** | Challenges widely held belief |

### Phase 5: Check for Duplicates

Read `knowledge/03-episodes-database.md` and verify no selected moments overlap with existing shorts.

### Phase 6: Extract Keywords

From the full transcript, extract:
- Main topics discussed
- Guest expertise areas
- Problems solved
- Industry terms
- Company/product names

Format: comma-separated, under 500 characters.

---

## Output Format

```markdown
# Episode [X]: [Guest Name] — [Company/Org]

**Topic:** [Main topic in one line]
**Estimated Length:** [X minutes]
**Keywords:** [comma-separated]

---

## Episode Summary
[2-3 sentences: who the guest is, what they do, why it matters]

---

## Extracted Moments ([X] total)

### Moment 1: [Working Title]

**Timestamp:** [XX:XX — XX:XX]
**Duration:** ~[XX] seconds
**Category:** [Story / Insight / Market / Business / Hot Take]
**Score:** [X/20] (Standalone: X, Hook: X, Relevance: X, Quotability: X)

> "[Exact quote from transcript — the hook sentence]"

**Why it works:** [One sentence explaining the appeal]

**Suggested titles:**
1. [Best option]
2. [Alternative]

**Thumbnail text:**
- Podcast (16:9): "[line 1] / [line 2]"
- Shorts (9:16): "[LINE 1] / [LINE 2]"

**Description:**
[Ready-to-paste shorts description with hashtags]

---

### Moment 2: [Working Title]
...
```

---

## Quality Gates (Check Before Output)

For every moment included:
- [ ] Makes sense without the full episode
- [ ] Has a clear start and satisfying end
- [ ] Hook lands in first 3 seconds
- [ ] Single focused idea, fully delivered
- [ ] Would share this clip independently
- [ ] No banned words (check `knowledge/02-voice-and-tone.md`)
- [ ] Titles match the show's voice
- [ ] Thumbnail text is 4-6 words max, two-line format

---

## Self-Correction Rules

1. If output feels generic → add specificity from the transcript
2. If a moment needs context → it's not a good short, skip it
3. If you can't find enough strong moments → flag it honestly, don't pad with weak ones
4. Always prioritize variety across content types

---

## Completion

Return one of (per `CLAUDE.md` Completion Protocol):

- **DONE** — Target moment count hit, all quality gates pass, no duplicates.
- **DONE_WITH_CONCERNS** — Shipped under target count because strong moments were scarce. List which moments were borderline and why.
- **BLOCKED** — Transcript quality too low or too short. Cite evidence (word count, structure).
- **NEEDS_INPUT** — Missing episode number, guest name, or company. Ask once, specifically.

**Three-Strike Rule:** If scoring produces zero moments at score ≥14 after 3 passes of the transcript, return BLOCKED — the transcript likely doesn't have short-worthy content.

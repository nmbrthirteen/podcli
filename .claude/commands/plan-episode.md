---
description: Design questions, story arc, and moment map BEFORE recording an episode
allowed-tools: Read, Write
argument-hint: [guest-name-and-company]
triggers:
  - plan episode
  - upcoming recording
  - guest prep
  - prepare for interview
  - design questions
  - new episode plan
---

# /plan-episode — Episode Architect

> You are an episode architect. You work BACKWARDS from the ideal output — viral shorts, compelling titles, a story that holds attention — to design questions, story arcs, and moments BEFORE the recording happens.

---

## Before Starting

Read the knowledge base:
- `knowledge/01-brand-identity.md` — show positioning, what makes it unique
- `knowledge/02-voice-and-tone.md` — voice fingerprint, what resonates with the audience
- `knowledge/03-episodes-database.md` — past episodes (avoid repetition, find gaps)
- `knowledge/05-title-formulas.md` — title patterns (design questions that produce title-worthy answers)
- `knowledge/08-topics-themes.md` — core topics, audience interests
- `knowledge/11-inspiration-channels.md` — reference channels and what they do well
- `knowledge/13-learnings.md` — what consistently works / doesn't across past episodes

If using podcli, replace `knowledge/` with `.podcli/knowledge/`.

---

## Inputs

| Field | Required | Source |
|-------|----------|--------|
| Guest name | Yes | User provides |
| Company/Org | Yes | User provides |
| Guest role/title | Preferred | User provides or research |
| Topic area | Preferred | User provides or suggest |
| Guest background | Optional | User provides links, bio, or ask to research |
| Episode number | Optional | Auto from database |

---

## The Backwards Method

Start from the END (what you want to publish) and work backwards to the BEGINNING (what to ask).

### Step 1: Define the Ideal Output

Before designing questions, answer:

**What 8-12 shorts would you want from this episode?**

For each potential short, draft:
- A title that would stop the scroll (using the show's title formulas)
- The content type (Guest Story / Technical Insight / Market / Business / Hot Take)
- The one sentence the guest would need to say

This gives you a "moment map" — the specific things you need the guest to say or reveal.

### Step 2: Design the Story Arc

Every great podcast episode follows an arc. Design one:

```
ACT 1 — ORIGIN (10-15 min)
  Hook: The surprising thing about this guest/company
  Setup: How they got here (the non-obvious path)
  Tension: The moment everything almost failed

ACT 2 — INSIGHT (20-30 min)
  The core idea: What they know that most people don't
  The proof: Specific numbers, results, examples
  The contrarian take: Where they disagree with consensus

ACT 3 — FUTURE (10-15 min)
  The vision: Where this is going
  The advice: What they'd tell someone starting today
  The hot take: The bold prediction or unpopular opinion
```

### Step 3: Write the Questions

For each moment in the moment map, write the question that will produce it.

**Question types that produce viral moments:**

| Type | Pattern | Example | What it produces |
|------|---------|---------|-----------------|
| **The Origin Question** | "Take me back to the moment when..." | "Take me back to when you first realized [X] was broken" | Founder Story clips |
| **The Contrarian Probe** | "Most people think [X]. You disagree. Why?" | "Everyone says AI needs more GPUs. You think it needs more electricians. Why?" | Hot Take clips |
| **The Specific Number** | "What's the actual number?" | "What did that actually cost? Not the round number — the real one." | Business/Strategy clips with ugly numbers |
| **The Failure Question** | "What's the biggest mistake you made?" | "What's the decision you'd reverse if you could?" | Emotional, shareable clips |
| **The Insider Knowledge** | "What does everyone get wrong about [X]?" | "What does everyone get wrong about raising a seed round?" | Market/Insight clips |
| **The Future Bet** | "If you had to bet on one thing..." | "If you had to bet your company on one trend for the next 5 years..." | Vision/Prediction clips |
| **The Tactical Question** | "Walk me through exactly how..." | "Walk me through exactly how you closed your first enterprise deal" | Actionable wisdom clips |
| **The Uncomfortable Truth** | "What's the thing nobody in your industry wants to admit?" | "What's the thing nobody in [space] wants to say out loud?" | Hot Take clips with tension |

### Step 4: Plan the Moments

Map questions to ideal moments:

| Time | Question | Target Moment | Potential Title | Content Type |
|------|----------|--------------|-----------------|-------------|
| 5 min | Origin question | How they started | "[Name]: The Bet Nobody Understood" | Guest Story |
| 15 min | Specific number | The real cost | "The $X.XM Cost Nobody Budgets For" | Business |
| 25 min | Contrarian probe | Industry hot take | "[Bold claim stated directly]" | Hot Take |
| 35 min | Failure question | The pivot moment | "The Mistake That Almost Killed [Company]" | Guest Story |
| 45 min | Future bet | Bold prediction | "[Specific prediction about industry]" | Market |

### Step 5: Reference Check

Compare this plan against inspiration channels:
- Would **Huberman** ask this? (protocol/depth angle)
- Would **Cleo Abram** frame it this way? (optimistic/visual angle)
- Would **DOAC** use this hook? (emotional/5-second-rule angle)

Adapt the best patterns from each.

### Step 6: Pre-Title Draft

For each planned moment, draft 2-3 title options BEFORE recording. This gives the host a "target" — they know which answers will become shorts.

### Step 7: Apply Learnings

Read `knowledge/13-learnings.md` and adjust:
- Prefer question types that have produced high-performing shorts in past retros
- Avoid patterns flagged as "consistently doesn't work"
- Consider testing one Open Question if relevant

---

## Output Format

```markdown
# Episode Plan: [Guest Name] — [Company]

**Episode:** [Number]
**Topic:** [Main topic]
**Recording date:** [Date if known]

---

## Guest Profile
- **Name:** [Full name]
- **Role:** [Title at Company]
- **Background:** [2-3 sentences — what they've built, why they're interesting]
- **Unique angle:** [The one thing about this guest that's different from past episodes]

---

## Story Arc

### Act 1: Origin (0:00 — ~15:00)
**Goal:** [What the audience should feel/know after this section]
- Opening hook: [The surprising thing to lead with]
- Key question: [The question that unlocks the origin story]
- Target moment: [The clip you want from this section]

### Act 2: Insight (~15:00 — ~40:00)
**Goal:** [The core insight or knowledge transfer]
- Key questions: [2-3 questions for this section]
- Target moments: [2-3 clips you want]
- The contrarian angle: [Where to push back or probe deeper]

### Act 3: Future (~40:00 — ~55:00)
**Goal:** [The vision, advice, or bold prediction]
- Key questions: [2-3 questions]
- Target moments: [2-3 clips]
- The closer: [How to end on a high note]

---

## Question Sheet (Print This)

| # | Question | Why | Target Clip |
|---|----------|-----|-------------|
| 1 | [Question] | [Produces what] | [Title angle] |
| 2 | [Question] | [Produces what] | [Title angle] |
| ... | ... | ... | ... |

**Follow-up probes (use when answers are too general):**
- "Can you give me a specific example?"
- "What's the actual number?"
- "What happened next?"
- "Why do you think most people get that wrong?"
- "What would you tell someone starting that today?"

---

## Moment Map (Target Shorts)

| # | Target Title | Content Type | Comes From | Question # |
|---|-------------|-------------|-----------|-----------|
| 1 | [Draft title] | [Type] | Act [X] | Q[X] |
| 2 | [Draft title] | [Type] | Act [X] | Q[X] |
| ... | ... | ... | ... | ... |

---

## Reference Notes
- **From [Inspiration Channel 1]:** [Technique to borrow for this episode]
- **From [Inspiration Channel 2]:** [Technique to borrow]
- **Avoid:** [What NOT to do based on past episodes]

---

## Applied Learnings (from knowledge/13-learnings.md)
- [Pattern applied in this plan] → [which question/moment uses it]
- [Pattern avoided] → [what we're doing instead]

---

## Pre-Recording Checklist
- [ ] Guest briefed on format (length, style, topics)
- [ ] Questions printed / on screen
- [ ] Audio check done
- [ ] Recording software ready
- [ ] Moment map visible (know what clips you're aiming for)
```

Save to: `episodes/ep[XX]-[guest]-plan.md`

---

## Quality Gates

Before delivering the plan:
- [ ] Every question has a clear target moment
- [ ] Moment map covers all 5 content types
- [ ] Story arc has clear acts with rising tension
- [ ] Questions avoid generic "tell me about..." (specific and sharp)
- [ ] At least 2 questions designed to produce ugly numbers
- [ ] At least 1 question designed to produce a contrarian hot take
- [ ] No overlap with topics from previous episodes (check database)
- [ ] Reference channels consulted for format ideas
- [ ] Applied Learnings section filled in (not "[Pattern applied...]" placeholder)

---

## Completion

Return one of (per `CLAUDE.md` Completion Protocol):

- **DONE** — Plan saved, all quality gates pass, moment map covers 5 content types, learnings applied.
- **DONE_WITH_CONCERNS** — Plan saved but one or more gates borderline (e.g., only 4 content types covered because guest's scope is narrow). Flag which.
- **BLOCKED** — Not enough guest context to write sharp, specific questions. Ask for background (links, bio, prior interviews).
- **NEEDS_INPUT** — Missing guest role, topic focus, or episode number.

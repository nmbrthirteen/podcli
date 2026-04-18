---
description: Analyze published performance; append patterns to cross-episode learnings file
allowed-tools: Read, Write, Edit
argument-hint: [episode-number-and-metrics]
triggers:
  - retro
  - episode review
  - analytics
  - what worked
  - post-mortem
  - performance review
---

# /retro-episode — Analyst

> You are the performance analyst. You review how episodes and shorts performed, identify patterns, and make data-driven recommendations. You also **persist learnings** to `knowledge/13-learnings.md` so future episodes benefit.

---

## Inputs

| Field | Source |
|-------|--------|
| Episode number | User specifies |
| Performance data | User provides from YouTube Studio |
| Previous retros | Check `episodes/` for past retros |
| Existing learnings | Read `knowledge/13-learnings.md` before writing new ones |

---

## Analysis Framework

### 1. Performance Metrics

| Metric | Below Average | Average | Good | Great |
|--------|---------------|---------|------|-------|
| CTR | <2% | 2-4% | 4-7% | >7% |
| Avg View Duration | <30% | 30-50% | 50-70% | >70% |
| Shorts Views (48hr) | <1K | 1-10K | 10-50K | >50K |

### 2. Content Analysis

**What worked:** Which shorts performed best and why? (title angle, topic, hook type, content type)

**What didn't work:** Which underperformed and why? (weak hook, niche topic, poor packaging)

### 3. Pattern Recognition

Compare against previous episodes:
- Content types that consistently perform
- Title shapes that outperform
- Optimal posting times
- Shorts → long-form traffic conversion

### 4. Guest & Topic Analysis
- Did guest recognition help?
- Which topics resonated?
- Unexpected viral moments?

---

## Learnings Persistence (Critical)

After every retro, append any **new, repeatable** pattern to `knowledge/13-learnings.md`. Follow the entry format:

```markdown
- **[Pattern stated as a rule]** ([evidence: episode numbers, metrics]) → [action to take next time]
```

Rules for appending:
1. **Minimum 2 episodes of evidence.** Never write a pattern from a single data point — mark it as an Open Question instead.
2. **Check for existing entries.** If the pattern contradicts or refines an existing one, *update* the existing entry rather than appending a duplicate.
3. **Prune stale entries.** If a prior learning no longer holds in this retro, flag it for removal with evidence.
4. **One line per entry.** If it needs a paragraph, it's not a pattern yet — leave in Open Questions.

---

## Output Format

```markdown
# Episode Retro: EP[X] — [Guest] ([Company])

**Review Date:** [Date]

---

## Performance Summary

### Long-Form
| Metric | Value | Rating |
|--------|-------|--------|
| Views (7d) | [X] | [Rating] |
| CTR | [X]% | [Rating] |
| Avg View Duration | [X]% | [Rating] |

### Shorts
| Short | Title | Views (48h) | CTR | Rating |
|-------|-------|-------------|-----|--------|
| 1 | [Title] | [X] | [X]% | [Rating] |

---

## What Worked
1. [Insight]
2. [Insight]

## What Didn't Work
1. [Issue]
2. [Issue]

## Patterns
- [Cross-episode pattern — tag as [NEW] or [CONFIRMED from prior retro]]

---

## Recommendations

### Content
1. [Recommendation]

### Packaging
1. [Recommendation]

### Distribution
1. [Recommendation]

---

## Action Items
- [ ] [Do differently]
- [ ] [Keep doing]
- [ ] [Test this]

---

## Learnings File Updates

Appended to `knowledge/13-learnings.md`:
- [Entry 1]
- [Entry 2]

Updated (refined existing entry):
- [Entry if any]

Flagged for removal (no longer holds):
- [Entry if any]
```

Save retro to: `episodes/ep[XX]-[guest]-retro.md`

---

## Completion

Return one of (per `CLAUDE.md` Completion Protocol):

- **DONE** — Retro written, learnings file updated (appended / refined / pruned as needed), action items listed.
- **DONE_WITH_CONCERNS** — Retro written but evidence is thin on some patterns (flagged as Open Questions rather than new learnings).
- **BLOCKED** — No performance data provided; can't analyze. Ask for YouTube Studio export or specific metrics.
- **NEEDS_INPUT** — Episode number missing, or can't locate the content package to cross-reference short titles against performance.

# PodStack: personas and protocols

> One repo, one install, entire podcast content workflow.
>
> `CLAUDE.md` is the primary instruction document: it holds the command index, pipeline, MCP tool table, knowledge base table, and quality gate. This file adds the working protocols behind those commands. For cross-tool usage (Codex, Cursor, opencode, generic), see `AGENTS.podstack.md`.

You are the content production team for this podcast. You handle episode planning, moment extraction, title writing, descriptions, thumbnail planning, brand review, publishing, and performance analysis.

---

## Sprint workflow

PodStack is a process. The skills run in the order a content sprint runs:

```
Plan → Process → Write → Review → Publish → Retro
```

Every skill feeds the next. Every skill returns a structured outcome (see completion protocol). The full pipeline diagram and command index live in `CLAUDE.md`.

---

## Completion protocol (every skill)

Every skill returns one of four outcomes at the end of its output:

| Outcome | Meaning |
|---------|---------|
| **DONE** | All gates passed. Output is ready to use. |
| **DONE_WITH_CONCERNS** | Shipped, but flag non-blocking issues. List them with evidence. |
| **BLOCKED** | Cannot complete. State the blocker with evidence + what's needed to unblock. |
| **NEEDS_INPUT** | One specific missing input. State the question and wait. |

Never report DONE if a blocking quality gate failed. Never report DONE with silent concerns.

**Three-strike rule:** if a phase (moment extraction, title generation, etc.) fails to produce valid output 3 times in a row, stop the pipeline and return BLOCKED with the failing phase and evidence. Do not loop indefinitely.

---

## Routing

When the user provides input without a slash command, match against each skill's `triggers` frontmatter. If one fires with high confidence, run it. If two fire, ask which. If none fire, ask what they want. The common routes are listed under "Auto-detection" in `CLAUDE.md`.

---

## Reference tables

The knowledge base file list (14 files at `.podcli/knowledge/`), the command index, and the quality gate live in `CLAUDE.md`. Full content philosophy in `ETHOS.podstack.md`.

---

## One rule

> **Earn attention in 5 seconds. Deliver value that matches the promise.**

Everything else flows from this.

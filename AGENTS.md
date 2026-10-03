# AGENTS.md

This file exists for coding agents (OpenAI Codex, opencode, Aider, Cursor Agent,
and others) that read `AGENTS.md` by convention.

- `CLAUDE.md` is the primary instruction document: project layout, the MCP tool
  table, the knowledge base, and the quality gate all live there and are not
  repeated here.
- `AGENTS.podstack.md` covers cross-tool PodStack usage: how each host runs the
  content-production commands (`/plan-episode`, `/process-transcript`,
  `/generate-titles`, and the rest), and where each host installs them.
- `.claude/commands/*.md` are the PodStack command sources. Claude Code reads
  them directly from that path; `podcli auto` (and the other PodStack
  commands) also installs them as Codex skills under `~/.codex/skills/` when
  the `codex` CLI is present.

Start with `CLAUDE.md`, then `AGENTS.podstack.md` for command-by-command detail.

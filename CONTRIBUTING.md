# Contributing to podcli

Thank you for helping improve podcli. This project is AGPL-3.0 — contributions are welcome under the same license.

## Development setup

```bash
./setup.sh
npm install
npm run build
```

Python backend lives in `backend/`. TypeScript MCP server and Web UI live in `src/`.

## Project layout

| Path                                    | Purpose                                                         |
| --------------------------------------- | --------------------------------------------------------------- |
| `.podcli/`                              | Config home (knowledge, presets, assets, settings) — gitignored |
| `data/`                                 | Runtime data (cache, output, working) — gitignored              |
| `backend/config/paths.py`               | Canonical path resolution (Python)                              |
| `src/config/paths.ts`                   | Path resolution for Node (must stay aligned)                    |
| `backend/config_bundle.py`              | Portable profile export/import                                  |
| `backend/services/transcript_packer.py` | Transcript cache keys + packed markdown                         |

## Before you open a PR

1. Run tests: `python3 -m unittest discover -s tests -v`
2. Run TypeScript build: `npm run build`
3. If you change paths, env vars, or cache layout, update `README.md` and add a note to the config migration logic.
4. Keep diffs focused — one feature or fix per PR when possible.

## Path and cache conventions

- **Config home** (`PODCLI_HOME` or `.podcli-home` marker): portable settings only.
- **Data dir** (`PODCLI_DATA` or `data/`): transcripts cache, outputs, temp files.
- **Transcript cache**: `data/cache/transcripts/{16-char-hash}.json` — same hash algorithm in Python (`compute_cache_hash`) and TypeScript (`TranscriptCache`).

Do not reintroduce a separate CLI-only cache path without updating both sides.

## Adding an integration

1. Create `backend/services/integrations/<name>/` with `IntegrationBase` subclass.
2. Register in that package’s `__init__.py`.
3. Add MCP tool wiring in `src/server.ts` (or a thin handler) and optional UI toggle at `/integrations.html`.

## Security

- Config import uses zip path validation (`_safe_extract_zip`). Do not replace with raw `extractall` on untrusted archives.
- Do not commit `.env`, API keys, or personal media under `.podcli/` or `data/`.

## Questions

Open a GitHub issue with reproduction steps for bugs, or a short design note for larger features.

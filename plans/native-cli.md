# podcli → Native CLI (codex-style)

> Goal: turn podcli from a git-clone + `setup.sh` + venv/npm hybrid into a **native CLI you install once and that auto-updates**, like `openai/codex`. Users run the install script (`curl -fsSL https://podcli.com/install.sh | sh`; npm distribution is out because the unscoped `podcli` name is blocked, see RELEASE.md) and `podcli process video.mp4` just works, everywhere, with no Python/Node/FFmpeg setup.

## North star

```
curl -fsSL https://podcli.com/install.sh | sh
podcli process pod.mp4 --top 5
  → first run: silently provisions a hermetic runtime (one time)
  → 9:16 clips with burned captions
podcli                     # auto-updates itself on launch
```

No `setup.sh`. No venv. No `pip`. No `npm install` of the engine. No "is the right Python/FFmpeg on PATH?" The system environment becomes irrelevant.

---

## Why this is hard (the core tension)

codex is a single static Rust binary with **zero** runtime deps. podcli is the opposite — a **three-runtime hybrid**:

- **Python engine** (`backend/cli.py`, ~188KB): Whisper (→ PyTorch ~2GB), OpenCV face-crop, Pillow, FFmpeg, Google API.
- **Node/TS**: MCP server (`src/server.ts`), React web UI, Remotion → headless **Chromium** (studio bookends).
- **Bash launcher** routing PodStack AI commands to Claude/Codex, everything else to Python.

You can't fold PyTorch + Chromium + FFmpeg into one static binary. So we **package the hybrid**: a tiny native launcher that provisions and drives hermetic runtimes, and we **kill the single worst dependency (PyTorch) by swapping Whisper → whisper.cpp.**

---

## Locked decisions

| Area | Decision |
|---|---|
| **Target artifact** | Package the hybrid. Thin Go launcher provisions + drives hermetic runtimes; self-updates. Not a rewrite. |
| **Launcher language** | **Go.** One `go build` → 5 static binaries. Replaces both bash `podcli` and `install.cmd`. |
| **Runtimes** | **Fully hermetic.** Launcher downloads pinned standalone CPython, static FFmpeg, whisper.cpp (+ Node/Chromium later). System python/node/ffmpeg ignored. |
| **Transcription** | **whisper.cpp** replaces `openai-whisper`/PyTorch. GGUF models. Metal on Apple Silicon, CUDA/CPU elsewhere. ~145MB vs ~2GB. |
| **Bundle model** | Tiny launcher; **first run provisions the full core stack** (download-once, like today's `setup.sh` but automatic + cross-platform). |
| **Storage** | **Global** managed dir for runtimes + model cache (`%LOCALAPPDATA%\podcli` / `~/Library/Application Support/podcli` / `~/.local/share/podcli`). **Per-project** `.podcli/` (knowledge, output, history) stays in cwd — podcli stays project-scoped like git. |
| **Distribution** | **npm + bun only.** Thin wrapper package fetches the platform binary on install (codex-style). **No code signing, no brew/winget/curl\|sh/.exe.** |
| **Auto-update** | On launch: fast (~250ms, short-timeout) check against GitHub Releases. Newer → update then load. Offline/slow → proceed on current version (never blocks). Self-replace the managed binary in `~/.podcli/bin/`; if that's impossible, print the matching upgrade command (`npm i -g podcli` / `bun add -g podcli`). |
| **Update opt-out** | Persistent off switch: `podcli config set update.auto off` + `PODCLI_NO_UPDATE=1`. When off: no checks, runs installed version. `podcli update` still works on demand. |
| **AI features** | API key preferred → AI-CLI fallback → core works without. If a key is set, call the Claude/OpenAI API directly (self-contained); else shell to installed Claude/Codex CLI (today's behavior); else the video pipeline still works and AI features print how to enable them. |
| **Platforms** | macOS arm64, macOS x64, Linux x64, Linux arm64, Windows x64. |
| **First milestone** | **Thin vertical slice** — `process` pipeline only, fully hermetic, whisper.cpp, npm/bun, self-update, all 5 platforms. Studio / AI-API / MCP come after. |

---

## Target architecture

```
┌─ podcli (Go launcher, ~8MB, per-platform) ──────────────────────────┐
│  • on-launch self-update (GitHub Releases, throttle-free fast check) │
│  • first-run provisioning → global managed dir                       │
│  • subcommand routing: process/studio/thumbnails… → hermetic python  │
│                         studio render            → hermetic node      │
│  • config, version pinning, rollback                                 │
└──────────────────────────────────────────────────────────────────────┘
                         │ provisions (pinned versions)
                         ▼
   Global managed dir (~/.local/share/podcli, etc.)
     bin/        podcli-<version>            (the real engine binary, self-updatable)
     runtime/    cpython-standalone/  ffmpeg  whisper.cpp  (+ node/ later)
     models/     ggml-base-q5_1.bin  …        (fetched/cached)
     venv/       hermetic pip env for backend/ deps (opencv, Pillow, …)

   Per-project (cwd)/.podcli/
     knowledge/  output/  history/  presets/  cache/      (unchanged)
```

**Subcommand routing (MVP):** `process` and friends → `runtime/cpython/python backend/cli.py …` with all paths pointing at the hermetic runtime. The Go launcher sets `PYTHON`, `FFMPEG`, model paths, and env so `cli.py` never touches the system.

---

## Transcription swap (the keystone engine change)

Clean seam: `backend/services/transcription.py::transcribe_file()` returns a fixed dict (`segments`, word-level `words`, `duration`, `language`, speaker fields). Only the engine behind it changes.

- Replace `import whisper; model.transcribe(..., word_timestamps=True)` with a subprocess call to the vendored `whisper-cli` (whisper.cpp) emitting JSON, then map its output → the existing dict shape.
- **Validation risk to prove early:** whisper.cpp word-level timestamps must be good enough for the karaoke/word-highlight captions. Build a parity test (same clip, compare word timings old vs new) before committing.
- Diarization is already optional/off by default (Claude handles speakers; paste-transcript supports `Speaker (MM:SS)`), so it's not a blocker.
- Models: ship/fetch `ggml-base-q5_1` (~57MB) by default; allow `--model small/medium/large` to lazily fetch bigger GGUFs into `models/`.

---

## Roadmap

### Phase 0 — Foundation spike (de-risk everything)
- Go launcher skeleton: arg parse, subcommand passthrough to a hand-placed python.
- Managed-dir layout + OS-appropriate paths.
- Hermetic provisioning: download pinned **CPython standalone**, **static FFmpeg**, **whisper.cpp** binary + base-q5 model for the **current** platform; create hermetic venv; `pip install` backend deps into it.
- Prove `go run . process sample.mp4` produces a clip using **only** hermetic components (rename/hide system python+ffmpeg to verify).

### Phase 1 — whisper.cpp engine swap
- Reimplement `transcribe_file()` on whisper.cpp behind the existing dict contract.
- Word-timestamp parity test vs `openai-whisper` on a fixture; tune `--max-len`/token-timestamp flags.
- Remove `openai-whisper` from `requirements.txt`; confirm captions (karaoke/Hormozi/subtle) still render correctly.

### Phase 2 — Distribution + self-update (→ first installable release)
- CI matrix builds the Go launcher for all **5 targets**.
- **npm + bun wrapper package**: `postinstall` downloads the platform binary into `~/.podcli/bin/`; `bin` shim execs it. Publish to npm (bun consumes the same registry).
- GitHub Release per version carries: the 5 binaries + a **version manifest** pinning required runtime versions (so an update knows what to re-provision).
- Self-update: fast on-launch check, atomic self-replace of the managed binary, npm/bun fallback message, `PODCLI_NO_UPDATE` + `config set update.auto off`, `podcli update`, keep-previous-binary for `podcli rollback`.
- **Ship.** `npm i -g podcli` → `podcli process` works hermetic on all 5 platforms and auto-updates. ← *this is the MVP gate.*

### Phase 3 — Lazy tiers + studio
- Demote OpenCV face-crop to lazy (center-crop default offline; fetch opencv on first smart crop — the center-crop fallback already exists at `cli.py:621`).
- Lazy bigger Whisper models.
- **Studio**: provision hermetic **Node + Remotion + Chromium** on first `studio` use; route `studio` render through it.

### Phase 4 — AI goes native
- Port PodStack prompt files (`.claude/commands/*.md`) into the engine as direct **Claude API** calls.
- `podcli config set api-key …`; precedence: API key → installed Claude/Codex CLI → "enable AI" hint.
- Keep core pipeline fully functional without any AI.

### Phase 5 — MCP / web UI (or deprecate)
- Decide whether the MCP server still matters once AI is native via API, or it's only for "use podcli from inside Claude/Codex."
- If kept: `podcli serve` (MCP stdio) + `podcli ui` (web dashboard) provisioned on demand via hermetic Node.

---

## Risks / open questions

- **whisper.cpp timestamp quality** for word-highlight captions — *prove in Phase 1 before deleting PyTorch path.*
- **Hermetic Node + Chromium on Windows** for studio (Phase 3) — Remotion's Chromium download + headless render is the heaviest non-ML surface; expect platform-specific pain.
- **First-run download size/time** — set expectations with a clear progress UI; cache aggressively in the global dir.
- **npm self-update vs package-manager ownership** — managed-binary-in-`~/.podcli/bin` sidesteps it; fallback message covers the rest.
- **GPU acceleration** — whisper.cpp Metal (mac) is automatic; CUDA (linux/win) needs the right prebuilt — decide CPU-only baseline + optional CUDA fetch.
- **Versioning** — single version for launcher + manifest of pinned runtime versions; SemVer; changelog drives the "update available" line.
- **MCP/web UI fate** — genuinely open; resolve at Phase 5.

---

## Immediate next step

Start **Phase 0** on a branch: stand up the Go launcher + hermetic provisioning for the current platform (darwin/arm64) and get `process` running end-to-end against only hermetic components. That single spike validates the launcher, the managed-dir model, hermetic provisioning, and the whisper.cpp integration surface in one shot.

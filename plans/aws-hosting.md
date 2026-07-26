# podcli → Hosted SaaS on AWS

> Goal: run podcli as a multi-tenant product — Clerk auth, Creem billing, a pro-tier browser video editor — on the cheapest AWS shape that still scales past 1000 users. The hard part is not AWS. It is that podcli is single-user *by construction*: `web-server.ts` binds loopback **because there is no auth**, every piece of state is a process global, and every byte of storage is a raw `fs` call.

## North star

```text
Browser ──► CloudFront ──► S3            (React SPA, static)
   │
   ├─ Clerk JWT
   │
   ├──► ALB ──► Fargate ARM64 [api ×2] ──► RDS Postgres   (state, jobs, entitlements)
   │                    │
   │                    └──► SQS ──► Fargate Spot [worker 0..N]
   │                                        │  stage in → scratch → upload out
   └──► CloudFront (signed) ◄── S3 media ◄──┘
```

Two invariants generate every decision below:

1. **Video bytes never transit the API server.** Presigned multipart upload in, CloudFront out.
2. **Heavy compute scales to zero.** Renders and transcription are bursty. The API is not. They must never share a scaling unit.

## The one decision everything hangs on

**The worker stages in, works on local scratch, uploads out, and deletes scratch.**

`backend/main.py` is already a stateless one-shot process: read one JSON line on stdin, dispatch to one of 21 handlers, exit — with `PODCLI_HOME`/`PODCLI_DATA` read from env at spawn time. That is a queue worker that does not know it yet.

If the worker gives each job a private scratch root and copies S3 objects in and out around it, **`backend/cli.py` (5294 lines) needs zero changes**, and the OSS CLI keeps working unmodified. Every alternative — an S3 filesystem shim, EFS, teaching Python about buckets — costs more and buys less.

## Facts this plan is built on (verified, not assumed)

| Fact | Where | Consequence |
|---|---|---|
| No auth anywhere; loopback bind is the security model | `src/ui/web-server.ts:3762` | Every one of ~95 routes needs a tenant guard |
| State is process-global | `uiState:227`, `jobs:100`, `sessionTranscripts:117`, `sseClients:272` | Each is a cross-tenant leak until moved |
| Jobs are an in-memory `Map`, reaped at 30 min | `web-server.ts:100,106` | A restart loses every job; no durability, no queue |
| `paths` is computed at module load | `src/config/paths.ts:66` | Must become per-job; `pythonEnv():97` is the one injection point |
| `moveToOutput()` is a bare `fs.rename` | `src/services/file-manager.ts` | First thing to break on object storage |
| Transcript cache already keys on sha256(first 10 MB + size) | `src/services/transcript-cache.ts` | Free content-addressed dedup — reuse as the S3 master key |
| **No GPU needed**: whisper.cpp default, ONNX pinned to CPU, torch excluded | `transcription_whispercpp.py`, `audio_events.py:82`, `requirements-runtime.txt` | Skip the entire GPU fleet. This is the single biggest cost saving |
| **AssemblyAI is already a first-class engine** | `backend/services/engines.py` (11 lines), `transcription.py` | Managed-API transcription ships with no new code |
| `build-studio.sh` emits dependency-free `.mjs` | `scripts/build-studio.sh` | Container image needs no `node_modules` |
| Remotion composition is React | `remotion/src/CaptionedClip.tsx` | `@remotion/player` gives a WYSIWYG browser editor off the same code |

## Locked decisions

1. **No NAT Gateway.** $32.85/mo before a byte moves, and the classic surprise bill. Fargate tasks go in public subnets with `assignPublicIp` and an all-deny inbound SG; S3 traffic goes through the free Gateway VPC Endpoint.
2. **No EFS for media.** ~$300/TB/mo, **13× S3 Standard**. It is the tempting lift-and-shift of the filesystem assumptions and it is a trap. Scratch lives on ephemeral task storage.
3. **ALB, not API Gateway.** `GET /api/job/:id/stream` is SSE; HTTP API buffers it.
4. **RDS `db.t4g.micro`, not Aurora Serverless v2.** ~3× cheaper at this size. Revisit at real load.
5. **Postgres is the job source of truth; SQS is only dispatch.** Progress writes to the `jobs` table, so any API replica can serve the SSE stream.
6. **CPU spot, not GPU spot, for self-hosted transcription.** whisper.cpp on `c7g` matches the shipped architecture: no CUDA image, no quota request, no torch.
7. **Dual-license.** `git log` shows two committers: the owner and dependabot (lockfile bumps). Sole copyright holder → OSS core stays AGPL, hosted fork is licensed commercially. This option closes the moment an outside PR lands without a CLA.

## Gates before any code

| # | Item | Action |
|---|---|---|
| G1 | AGPL-3.0-only + §13 | Hosting triggers source disclosure *including* auth/billing/tenancy work. Resolve via dual-license (above); add a CLA to `CONTRIBUTING.md` first |
| G2 | Remotion license | Source-available, not OSS. Free ≤3 people; paid company license beyond, `@remotion/lambda` metered separately. Budget before hiring |
| G3 | `POST /api/download-video` (yt-dlp) | Ship-blocker. Third-party downloads on your infra is DMCA/ToS exposure a desktop tool does not carry. Disable in the hosted build, keep in the CLI |
| G4 | `backend/services/claude_suggest.py` | Shells out to a local `claude`/`codex` binary. Becomes a direct Anthropic API call with prompt caching on the KB. ~$0.05/episode |
| G5 | pyannote diarization | Needs `HF_TOKEN` + ~2 GB torch + its own commercial terms. Don't ship it — AssemblyAI includes diarization in the per-minute price |

## Roadmap

### Phase 1 — Tenancy refactor (≈60% of total effort; ships to the OSS CLI first)

- **1.1 Per-job paths.** `src/config/paths.ts` → `getPaths(tenantId, jobId)` returning roots under `/scratch/<jobId>/`. Mirror in `backend/config/paths.py` — it already has `reload_paths()`, so mutable roots are a supported concept.
- **1.2 Storage adapter.** New `src/services/storage.ts`: `LocalStorage` (current behavior, CLI unchanged) + `S3Storage`. Route `file-manager.ts`, `transcript-cache.ts`, `asset-manager.ts`, `clips-history.ts`, `knowledge-base.ts`, `utils/atomic-file.ts` through it. Master key = the existing content hash, so re-uploads dedupe and the transcript cache becomes a global hit.
- **1.3 Globals → Postgres.** `uiState`/`ui-state.json` → `sessions`; `jobs` Map → `jobs` table + SQS; `clips.json`/`registry.json`/`integrations.json`/`sources.json`/`thumbnail-config.json` → tenant-keyed tables; `activeBlockingJobs()` → per-tenant counter. Schema: `tenants, users(clerk_id), projects, media(sha256,s3_key,bytes,duration), jobs, clips, subscriptions, usage_ledger, entitlements`.
- **1.4 Amputate the host-filesystem surface.** Behind `PODCLI_HOSTED=1`, remove `/api/browse-file`, `/api/select-file`, `/api/knowledge/dir`, `/api/stream-source`, `/api/download-video`. Re-audit `safePath():78` and the filename-taking routes (`/api/download/:filename`, `/api/preview/:filename`, `/api/image`) — the loopback bind was doing much of that work. Replace `POST /api/upload` (multer disk, 10 GB cap) with presigned multipart.
- **1.5 Two images.** `api` (Node only, ~150 MB) and `worker` (Node + Python + ffmpeg + whisper-cli + ggml models + Remotion/Chrome, ~2–3 GB). Bake runtimes at build time; never run `cli/internal/provision/` at container start — it pulls ~GB from HuggingFace/nodejs.org.

### Phase 2 — Infrastructure

| Layer | Choice |
|---|---|
| SPA | S3 + CloudFront + OAC (`npm run build` already emits it; 1 TB/mo egress free, perpetual) |
| API | Fargate ARM64, 2 × 0.5 vCPU / 1 GB, scale on ALB request count |
| DB | RDS Postgres `db.t4g.micro`, gp3 20 GB, single-AZ → Multi-AZ when revenue justifies |
| Queue | SQS standard + DLQ, long polling, visibility timeout > max render |
| Workers | Fargate **Spot** ARM64, min 0, scale on `ApproximateNumberOfMessagesVisible`. Renders are idempotent, so interruption just requeues |
| Secrets | SSM Parameter Store (free) over Secrets Manager ($0.40/secret/mo for the same thing) |
| Logs | CloudWatch, **14-day retention** — the default never expires, which is a silent leak |

### Phase 3 — Auth + billing

- **Clerk.** `@clerk/clerk-react` in the SPA; JWKS-validating middleware mounted ahead of all `/api/*`, populating `req.auth = {userId, tenantId, plan}`. `tenantId` = Clerk Org ID when present, else user ID — teams come free later. `user.created` webhook seeds `tenants`/`users`.
- **Creem.** Merchant of record, so global VAT/sales tax is handled — the main reason to prefer it over raw Stripe for EU sales. Checkout carries `metadata.tenant_id`; `POST /api/billing/webhook` verifies the HMAC signature and handles `checkout.completed`, `subscription.active|paid|canceled|expired`, `refund.created`, idempotent on event ID.
- **Meter usage, don't just sell seats.** Video cost is usage-driven. `usage_ledger` tracks transcription minutes, render minutes, GB-months. **Enforce quota at enqueue, not at render.**

| Plan | Transcribe min/mo | Retention | Max source | Editor | Concurrency |
|---|---|---|---|---|---|
| Free | 60 | **7 days** | 30 min | Trim only | 1 |
| Pro | 600 | 90 days | 3 hr | Full | 3 |
| Studio | 3000 | 365 days | unlimited | Full + brand kit | 10 |

Retention-by-tier is not a detail. It is the largest single lever on the storage bill.

### Phase 4 — Storage economics

Keys: `t/<tenant>/masters/<sha256>.mp4`, `…/proxies/`, `…/clips/`, `…/tmp/<job_id>/`.

**Store less**

1. **Proxy-first.** Generate a 480p ~600 kbps proxy on ingest (~1/20 the size). Every preview, scrub, and editor session reads the proxy. The master is opened only for the final render.
2. **Content-hash dedup** via the existing sha256 (§1.2).
3. **Intermediates never reach S3** — ffmpeg working files die with the task.
4. **Offer master deletion after processing.** Once clips + transcript + proxy exist, most users never need the 4 GB original. "Keep master" becomes an explicit Pro toggle.

**Lifecycle (the 3–5× lever)**

| Prefix | Rule |
|---|---|
| `masters/` | **Intelligent-Tiering** with Deep Archive Access enabled — right default because media access is unpredictable; $0.0025/1000 objects/mo monitoring, no retrieval fee in the frequent/infrequent tiers |
| `clips/` | Standard → Standard-IA @ 60 days |
| `proxies/`, `thumbnails/`, `transcripts/` | Standard (tiny, always hot) |
| `tmp/` | Expire @ 2 days |
| bucket-wide | **Abort incomplete multipart uploads @ 3 days** — classic invisible cost |
| bucket-wide | App-level deletion per plan retention, running *ahead* of lifecycle |

Ladder (us-east-1, GB/mo): Standard `$0.023` → Standard-IA `$0.0125` → Glacier Instant `$0.004` → Deep Archive `$0.00099`.

**Move less.** Presigned multipart upload direct to S3. CloudFront + OAC + signed URLs out — S3→CloudFront origin transfer is free, first 1 TB/mo out is free forever, $0.085/GB after. Enable Storage Lens and tag by `tenant_id` so overage is priced from real numbers.

### Phase 5 — Transcription: API default, self-host valve

Both, behind the existing `normalize_engine()` switch — an 11-line file, so adding an engine is trivial.

| Tier | Engine | ~Cost/min | Note |
|---|---|---|---|
| **Default** | **AssemblyAI** — *already implemented* | $0.0062 (Universal) / $0.0021 (Nano) | Zero new code. Diarization included, which retires the pyannote/torch dependency |
| Cheap | Groq `whisper-large-v3-turbo` (add) | $0.0007 | ~9× cheaper, **no diarization** — monologue content only |
| **Valve** | **whisper.cpp on `c7g` Spot** — *binary already built by `release.yml`* | ~$0.0005 at load | CPU, not GPU |

Route to the self-host fleet only when sustained backlog exceeds ~2 h of audio; below that, idle capacity costs more than the API. GPU (`g4dn` + NVENC, which `backend/services/encoder.py` already probes for) is a later *render*-volume optimization, not a launch concern.

### Phase 6 — Pro editor

**Browser timeline on proxy playback, server-side final render.**

The deciding factor is specific to this repo: `@remotion/player` runs *the same React composition in the browser* that `@remotion/renderer` runs on the server, and `remotion/src/CaptionedClip.tsx` already is that composition. True WYSIWYG with no second caption engine. Client-side export (WebCodecs/ffmpeg.wasm) cannot reproduce the branded compositions and burns the user's laptop; server-streamed preview multiplies compute by every scrub.

1. Mount `CaptionedClip` in `@remotion/player` with live props — zero server cost while editing.
2. Extend what exists: `MomentTrim.tsx` (142 lines), `ReframeEditor.tsx` (194), `ClipPlayer.tsx` (62).
3. Transcript-driven caption editing off the existing word timestamps — reuse `src/ui/client/captionChunks.ts` and `remotion/src/chunks.ts`.
4. Add tracks: b-roll/overlay, music bed with ducking (`audio_normalize.py`), logo/intro/outro (`asset-manager.ts`).
5. Export → SQS → worker runs the *identical* composition through `remotion/render.mjs`.

`@remotion/lambda` is the alternative export path (parallel, no idle) — worth it if clip volume gets spiky, but it is a second deploy target with separate license metering. Start on the Fargate Spot worker you need anyway.

## Cost model

200 monthly-active users · 2 h source each (400 h/mo) · ~10 clips each (2000/mo) · ~600 GB new masters/mo.

| Fixed | $/mo | | Variable | $/mo |
|---|---|---|---|---|
| ALB | 18 | | Transcription (AssemblyAI Nano, 24k min) | 50 |
| Fargate API 2 × 0.5 vCPU/1 GB ARM | 29 | | *…or self-host `c7g` spot* | *12* |
| RDS `t4g.micro` + 20 GB gp3 | 14 | | Rendering, 2000 clips on Spot | 5 |
| CloudFront + S3 static | 3 | | S3 ~3 TB blended **with** lifecycle | 35 |
| SSM / CloudWatch / SQS / ECR | 10 | | *…same 7.2 TB **without*** | *165* |
| NAT Gateway | **0** | | CloudFront egress (<1 TB free tier) | 0 |
| **Fixed total** | **~74** | | **Variable total** | **~90** |

**≈ $165/mo for 200 active users ≈ $0.82/user.** At $19–29 Pro pricing, comfortable margin.

**Day-1, pre-revenue:** drop the ALB and second API task (single task + public IP behind CloudFront) → **~$35–45/mo fixed**. Graduating to the table above is configuration, not re-architecture.

## Execution order

| # | Work | Blocks |
|---|---|---|
| 1 | Gates G1–G5: license call, kill yt-dlp route, `claude_suggest.py` → Anthropic API | all |
| 2 | Storage adapter + per-job `getPaths()` (1.1–1.2) | 3, 4 |
| 3 | Postgres schema; `uiState`/`jobs` off globals (1.3) | 5, 6 |
| 4 | Two Dockerfiles + `docker-compose` for local parity (1.5) | 5 |
| 5 | IaC: VPC (no NAT), S3 + lifecycle, RDS, SQS, ECS api/worker, ALB, CloudFront | 6 |
| 6 | Clerk middleware + tenant scoping; strip host-path routes (1.4, 3) | 7 |
| 7 | Creem checkout + webhook + `usage_ledger` + entitlement gates (3) | 8 |
| 8 | Presigned multipart + proxy generation on ingest (4) | 9 |
| 9 | `@remotion/player` editor (6) | — |

Steps 2–4 are the bulk of the work and are pure refactor. They ship to the OSS CLI and get verified locally **before a single AWS resource exists**.

## Verification

**Local, before any AWS spend**

- `npm test` + `pytest` stay green through Phase 1 — `src/services/*.test.ts` and `src/config/paths.test.ts` already cover the code being changed.
- `docker compose up` with LocalStack (S3 + SQS) + Postgres; run upload → transcribe → suggest → render → download under `PODCLI_HOSTED=1`.
- **Tenancy isolation test — the one that matters.** Two tenants, concurrent jobs; assert A cannot read any B key and that scratch roots are disjoint. Every global in §1.3 is a leak until this passes.
- `LocalStorage` still passes the full suite, proving the OSS CLI is not regressed.

**Staging**

- Drive the hosted URL with the MCP tools: `get_ui_state`, `transcribe_start` → `job_status`, `suggest_clips`, `batch_create_clips`, `list_outputs`.
- Kill a worker mid-render → job requeues and completes (proves spot-safety).
- Workers at 0, enqueue → cold start completes end-to-end.
- Creem test webhooks for `subscription.active` / `canceled` / `refund.created` → entitlements flip and quota blocks at enqueue.
- 4 GB presigned multipart upload → confirm zero bytes through Fargate via task network metrics.
- Force `tmp/` lifecycle expiry and confirm objects clear.

**Cost**

- Tag everything with `tenant_id` where possible; enable Storage Lens and Cost Anomaly Detection before the first real customer.
- Billing alarm at 2× modeled fixed cost. The failure mode of this architecture is a runaway worker loop, and SQS + autoscaling will spend happily.

## Risks / open questions

- **The tenancy refactor touches ~15 services across TS and Python.** It is the schedule risk, not AWS. Mitigated by shipping it to the OSS CLI behind `LocalStorage` first, where the existing test suite guards it.
- **Remotion in the worker image** drags Chrome Headless Shell (hundreds of MB). Watch Fargate Spot task start time; if cold starts hurt, that is the trigger to move exports to `@remotion/lambda`.
- **SSE through ALB** needs idle timeout raised above the current 500 ms poll cadence's session length; verify before relying on it.
- **AGPL §13 compliance** if the dual-license path is rejected: the hosted modifications must be published. Decide before, not after, writing the billing code.

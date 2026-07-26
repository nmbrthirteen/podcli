# podcli → Pricing

> Goal: prices that are unambiguously profitable for us and unambiguously good value for the customer, at the same time. Both halves have to be provable from the unit economics — not asserted. The market leader charges **$5.80 per source hour**; our marginal cost is **$0.29**. That gap is the whole opportunity, and it is wide enough that we never have to choose between margin and generosity.

## North star

```text
cost per source hour: $0.29        ← what an hour of podcast actually costs us
Opus Clip Pro:        $5.80/hr     ← what the category leader charges
podcli Creator:       $0.75/hr     ← 8× their value, 49% margin at FULL quota,
                                      ~77% at real utilisation
```

## The one decision everything hangs on

**Be generous on hours; differentiate on features.**

Hours are cheap for us ($0.29) and are the loudest source of anxiety for the customer — the single most-cited complaint about Opus Clip is that a 90-minute upload burns 90 credits whether it yields 8 usable clips or 25. Buying that anxiety away costs us almost nothing and is the clearest possible signal that we are not the same product.

So the caps go high enough that ~95% of individual podcasters never think about them, and the tier boundary is drawn at **the editor, formats, seats, and retention** instead. Competing on price alone attracts churn-prone customers and can't be defended; competing on value density at the same price can.

## What competitors charge (verified July 2026)

| Tool | Free | Entry | Mid | Top | Unit |
|---|---|---|---|---|---|
| Opus Clip | 60 min, watermark, expires 3 days | **$15** (150 min) | **$29** Pro (300 min) | Business custom | upload minutes |
| Vizard | — | — | **$29** Creator | $39 Business | upload minutes |
| Klap | — | — | **$29** | +API $0.32–0.48/op | upload minutes |
| Submagic | 3 videos, watermark | **$20** (30 videos) | $40 Pro (100) | $80 Agency (300) | videos |
| Descript | 60 min | $24 Hobbyist (~10 hr) | $35 Creator (30 hr) | $65 Business | **hours**, per seat |

Two structural reads:

- **$29 is the category anchor.** Opus Pro, Vizard Creator, and Klap landed there independently. Fighting for it head-on is a brand war a newcomer loses.
- **Descript is the value benchmark at ~$1.17/hr** — because it is an editor, not a clipper. podcli is both. That is the wedge.

## Locked decisions

1. **Never watermark, on any plan — including Free.** Every competitor watermarks their free tier. Making this a stated policy rather than an omission costs nothing and is the fastest trust signal available.
2. **Clips are kept forever; masters are transient.** Opus deletes projects 3 days after cancellation and it is their most-hated behaviour. Clips + transcripts are ~0.25 GB/hr, so keeping them permanently costs cents. Masters are ~1.5 GB/hr and are what would actually eat us — so master retention becomes the tier feature.
3. **Bill in hours, never minutes.** "20 hours" reads generous; "1,200 minutes" reads like a meter running. Identical quantity, opposite feeling.
4. **Re-processing is free, forever.** `transcript-cache.ts` already content-hashes source video, so re-running an episode off the cached transcript costs ~nothing. Re-run suggestions, restyle captions, generate new formats — free. No competitor can afford to promise this.
5. **Push annual at ~20%.** Creem's flat $0.40 costs 5.3% on a $15 monthly plan vs 4.0% annual, *and* annual kills churn.
6. **No credit system.** No cost pressure forces it, and it is the category's most resented pattern.

## Unit economics — the proof

Marginal cost of one source hour, on the recommended stack:

| Component | $/hour |
|---|---|
| Transcription (AssemblyAI Nano) | 0.126 |
| LLM — selection + titles/descriptions (`aws-hosting.md` §5b) | 0.110 |
| Rendering ~6 clips + proxy generation (Fargate Spot) | 0.032 |
| Storage, first month | 0.026 |
| **Total** | **~0.29** |

Plus ~$0.004/GB-month carrying cost on whatever is retained. With the self-hosted whisper.cpp valve, transcription drops to ~$0.03 and the total falls to **~$0.20**.

For reference: Opus Clip Pro is **$5.80/hr**. We have roughly 20× headroom.

## Tiers

| | Free | **Creator $15** | **Studio $35** | Team $99 |
|---|---|---|---|---|
| Annual | — | $12/mo ($144) | $28/mo ($336) | $79/mo ($948) |
| **Hours/mo** | **1** | **20** | **60** | **120** |
| **$/hour** | — | **0.75** | **0.58** | **0.83** |
| Watermark | **never** | never | never | never |
| Quality | 1080p | 1080p | 4K | 4K |
| Formats | 9:16 | 9:16 + 16:9 | all | all |
| Editor | trim | trim | **full** | full + white-label |
| Clips retained | **forever** | forever | forever | forever |
| Masters retained | 7 days | 30 days | 90 days | 180 days (2 TB cap) |
| Seats / concurrency | 1 / 1 | 1 / 3 | 1 / 10 | 5 / priority |

**Overage $1.50/hr** (83% margin), auto-billed on paid tiers, never hard-blocked. Free hard-stops at 1 hour.

## Margins — worst case and realistic

Full quota is the worst case: a customer who consumes every included hour, every month. Realistic utilisation for individual podcasters is 20–35%.

| | Revenue | Creem fee | COGS at full quota | **Margin, full quota** | **Margin, realistic** |
|---|---|---|---|---|---|
| Creator $15 | 15.00 | 0.99 | 6.71 | **49%** | **77%** (7 hr) |
| Studio $35 | 35.00 | 1.77 | 22.83 | **30%** | **81%** (12 hr) |
| Team $99 | 99.00 | 4.26 | 53.76 | **41%** | **79%** (36 hr) |

Free costs **~$0.32/user/month**. At 5% conversion that is ~$6.40 of free-tier spend per paying customer — cheap acquisition, and the reason 1 unwatermarked hour is safe to give away.

**Studio at full quota (30%) is the number to watch.** It is the only tier where a genuine power user compresses margin. Two things protect it, and both are already in the architecture plan:

1. **The self-hosted transcription valve.** Transcription is the largest single COGS line. Moving it from AssemblyAI ($0.126/hr) to whisper.cpp on `c7g` spot ($0.03/hr) takes Studio's full-quota margin from **30% → 46%**. This is why "keep both engines" was the right call — it is not just flexibility, it is margin insurance that lets us price this aggressively without fear.
2. **Master retention by tier.** Unbounded master retention at 60 hr/mo reaches 1 TB inside a year and would cost more than the subscription. The 90-day window keeps it at ~270 GB.

Sixty hours a month is sixty podcast episodes. Practically nobody reaches it — which is exactly why a high cap is cheap to offer and reads generous. **High caps are affordable precisely because utilisation is low; that is the arbitrage, and it is real, not a trick.**

## Why these numbers, specifically

**Free = 1 hour, but complete.** One full episode at 1080p, unwatermarked, clips kept forever. *Small but complete* rather than *big but crippled* — strictly better than Opus Free (same 60 minutes, watermarked, deleted in 3 days). One hour proves the AI without running anyone's show.

**Creator $15 / 20 hours.** Undercuts Opus Starter's price *and* gives 8× the hours. Covers ~95% of individual podcasters, so the cap stops being a source of anxiety at all.

**Studio $35 / 60 hours + the editor.** Priced against Descript Creator ($35 / 30 hr, editor but no clipping): same price, double the hours, clipping included. Against Opus Pro + Descript together ($64 of tools), it is the obvious consolidation.

**Team $99 / 5 seats.** Descript bills per seat — 5 × $65 = $325/mo. The `tenantId` = Clerk Org ID decision in the hosting plan gives this tier away nearly for free.

## Value density vs the field

| | $/mo | Hours | **$/hour** |
|---|---|---|---|
| Opus Clip Starter | 15 | 2.5 | 6.00 |
| Opus Clip Pro | 29 | 5 | 5.80 |
| Vizard Creator | 29 | ~5 | ~5.80 |
| Descript Creator | 35 | 30 | 1.17 |
| **podcli Creator** | **15** | **20** | **0.75** |
| **podcli Studio** | **35** | **60** | **0.58** |

Creator is ~8× the value density of Opus Pro at half the price, and still holds a 49% worst-case margin. Both halves are true at once — which was the whole requirement.

## Execution order

| # | Work |
|---|---|
| 1 | Launch **three** tiers only — Free / Creator $15 / Studio $35. Hold Team until customers ask for seats; four tiers on day one is a decision-paralysis tax |
| 2 | Put the no-watermark policy in the **headline**, not the feature list |
| 3 | Ship master-retention-by-tier in v1 — retrofitting a deletion policy onto existing customers is a support event; setting the expectation on day one is free |
| 4 | Ship `usage_ledger` even if never displayed. Don't touch prices for six months — tune from real hours/user, clips/hour, GB/user, not from this document's estimates |
| 5 | Grandfather the first 100 customers permanently at founding prices. At $0.29/hr marginal cost this is cheap and is what gets podcli talked about in creator communities |
| 6 | Build the self-host transcription valve before ~100 paying users — that is where the AssemblyAI bill starts to matter and where these margins need protecting |

## Risks / open questions

- **Studio power users** are the only real margin risk (30% at full quota). Mitigated by the self-host valve and 90-day master retention; monitor `usage_ledger` for anyone sustaining >40 hr/mo and consider a fair-use conversation rather than a repricing.
- **Creem's flat $0.40** makes any sub-$10 monthly tier structurally unattractive (8%+ effective rate). Don't add a cheaper tier; push annual instead.
- **These COGS figures assume the Phase 4 lifecycle rules and Phase 5b LLM routing from `aws-hosting.md` are actually implemented.** Without them, storage and LLM costs roughly triple and every margin in this document is wrong. The pricing and the architecture are one decision, not two.
- **A/B $15 vs $19 on Creator** once there is traffic. Instinct says $15 wins on volume given the margin, but it is the one number worth testing rather than reasoning about.

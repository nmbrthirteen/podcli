---
description: Draft the knowledge base from an existing channel, feed, or show description
allowed-tools: Read, Write, WebFetch, WebSearch, Bash
argument-hint: [channel-url-or-show-description]
triggers:
  - bootstrap knowledge
  - set up knowledge base
  - fill knowledge from channel
  - import my channel
  - new show setup
---

# /bootstrap-knowledge: knowledge base bootstrapper

> You turn an existing channel, podcast feed, or plain description of a show into a first draft of the knowledge base, so the owner edits instead of starting from blank templates.

---

## Before starting

1. If `` has no files, run `podcli knowledge init` first so all 14 templates exist.
2. Ask for whichever of these the user has not provided:
   - Channel or podcast URL (YouTube channel, Spotify show, RSS feed)
   - Or a few sentences about the show if nothing is published yet

## Research phase

From the channel URL, gather with WebFetch/WebSearch:

- Show name, tagline, and about text
- The 10-15 most recent episode titles and descriptions
- The 5 best-performing videos you can identify (views relative to channel size)
- Host names and how they describe themselves
- Recurring topics across episode titles
- Any shorts already published: their titles and hooks

If there is no published content, interview the user instead: format, audience, topics, tone, 3 shows they admire.

## Drafting phase

Fill these files with drafts, marking every inference with `<!-- draft: verify -->` where you are guessing:

| File | Draft from |
|------|-----------|
| `01-brand-identity.md` | About text, episode patterns, host info |
| `02-voice-and-tone.md` | Actual phrasing in titles and descriptions; propose banned words from cliches the niche overuses |
| `03-episodes-database.md` | The recent episode list, with links |
| `05-title-formulas.md` | Patterns extracted from their best-performing titles |
| `08-topics-themes.md` | Topic clusters across episodes |
| `11-inspiration-channels.md` | 3-5 comparable channels in the niche |
| `12-quick-reference.md` | Platform links found during research |

Leave `04`, `06`, `07`, `09`, `10`, `13` as templates unless the user gave you enough to draft them: those encode preferences research cannot infer.

## Handoff

End with a short list of the specific `[brackets]` and `<!-- draft: verify -->` marks the owner must confirm, ordered by how much they affect output quality (voice and banned words first).

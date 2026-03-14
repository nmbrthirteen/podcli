# /plan-thumbnails — Art Director

> You are the art director. You plan thumbnail text, layouts, and briefs for the design team. Every thumbnail follows the show's visual system.

---

## Trigger

User asks for thumbnail text/plans, or as part of a content package.

---

## Before Starting

Read:
- `.podcli/knowledge/07-thumbnail-guide.md` — layouts, colors, typography, examples
- `.podcli/knowledge/01-brand-identity.md` — brand positioning and visual identity

---

## Two Format System

### Format 1: Podcast Thumbnails (16:9 Landscape)

**Text rules:**
- **Line 1:** Setup text (primary color)
- **Line 2:** Payoff text (accent color/highlight)
- **Max:** 4-6 words total across both lines
- **Feel:** Conversational, would-actually-say-this

**Layout elements** (customize per `.podcli/knowledge/07-thumbnail-guide.md`):
- Border/frame treatment
- Dark or branded background
- Guest photo placement
- Show branding placement

### Format 2: Shorts Thumbnails (9:16 Vertical)

**Text rules:**
- **Line 1:** ALL CAPS, primary text
- **Line 2:** ALL CAPS, accent color (often italic)
- **Position:** Lower third, centered
- **Max:** 4-6 words total
- **Feel:** Urgent, stop-the-scroll

---

## Execution Protocol

For each moment/clip:

### Step 1: Extract the Visual Anchor
What is the single most compelling image or concept?

### Step 2: Write Two-Line Text
- **Podcast version:** Based on show style from `.podcli/knowledge/07-thumbnail-guide.md`
- **Shorts version:** ALL CAPS, urgent, Line 2 = payoff

### Step 3: Specify Layout
- Guest photo requirements
- Background suggestion
- Special visual elements

### Step 4: Quality Check
- [ ] Readable at phone screen size
- [ ] Two lines max
- [ ] 4-6 words total
- [ ] Line 2 is the payoff
- [ ] No banned words

---

## Output Format

```markdown
### Thumbnail Brief: [Short/Episode Title]

**Podcast (16:9):**
- Text: "[line 1] / [line 2 — accent]"
- Guest photo: [headshot / action / none]
- Background: [description]

**Shorts (9:16):**
- Text: "[LINE 1] / [LINE 2 — accent]"
- Visual: [action shot / dramatic imagery / B-roll]
- Text position: Lower third, centered

**Designer notes:** [specific direction]

**Generate thumbnail:** `./podcli thumbnails "[LINE 1] / [LINE 2]" --logo [asset]`
_(The " / " separator tells the generator where to split lines)_
```

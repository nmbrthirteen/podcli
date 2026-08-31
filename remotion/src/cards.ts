import type { Motion } from "./motion";

interface CardBase {
  /** Seconds on the clip's own clock. */
  start: number;
  end: number;
  accent?: string;
  background?: string;
  motion?: Motion;
  /**
   * Whether the speaker stays on screen above the card.
   *
   * Drawn unless a card says null. A podcast card that drops the person is the
   * news-explainer reflex this format is not: the moment is somebody saying
   * something, and cutting their face out mid-sentence throws away the only
   * thing the clip has.
   *
   * Null is for the card that earns the whole frame, like a screenshot nobody
   * could read at half the height.
   */
  speaker?: null;
}

/**
 * What a card can say.
 *
 * Four kinds rather than a free-form layout, because a card is only worth
 * taking the frame for when it says something the talking head cannot: a
 * number, a claim, a list, or the sentence itself set large enough to read
 * at arm's length. Anything past that is a slide deck.
 */
export type Card =
  | (CardBase & {
      kind: "stat";
      /** The number, and only the number. */
      value: string;
      eyebrow?: string;
      caption?: string;
    })
  | (CardBase & {
      kind: "headline";
      eyebrow?: string;
      lead: string;
      /** The payload word, set apart from the lead. */
      emphasis?: string;
      sub?: string;
    })
  | (CardBase & { kind: "bullets"; eyebrow?: string; items: string[] })
  /**
   * Two or three things measured against each other.
   *
   * Emphasis rather than a colour per bar: the story is that one of them is
   * the point, and giving every bar its own hue buries the one that is. The
   * subject wears the accent, the rest are context.
   */
  | (CardBase & {
      kind: "compare";
      eyebrow?: string;
      rows: { label: string; value: number; display?: string; subject?: boolean }[];
    })
  /** One thing, before and after. A dumbbell, in two steps of one hue. */
  | (CardBase & {
      kind: "change";
      eyebrow?: string;
      label?: string;
      from: { value: string; note?: string };
      to: { value: string; note?: string };
    })
  /** One ratio against its whole. A meter, never a two-slice pie. */
  | (CardBase & {
      kind: "share";
      eyebrow?: string;
      /** 0 to 1. */
      value: number;
      display?: string;
      caption?: string;
    })
  /** A name nobody in the audience necessarily knows, with its mark. */
  | (CardBase & {
      kind: "entity";
      eyebrow?: string;
      name: string;
      note?: string;
      /** Logo or photograph. A URL the renderer can reach, or a bundle path. */
      src?: string;
    })
  | (CardBase & { kind: "quote"; text: string; attribution?: string })
  | (CardBase & {
      kind: "image";
      /** A URL the renderer can reach, or a path inside the bundle. */
      src: string;
      /**
       * "fit" shows the whole picture, which is what a screenshot needs: a
       * cropped headline is not evidence of anything. "fill" covers the frame
       * and is for a photograph, where the edges carry nothing.
       */
      fit?: "fit" | "fill";
      caption?: string;
    });

/**
 * The card in front at this moment, or none.
 *
 * A later card wins an overlap rather than the array order deciding it,
 * because the planner writes cards in the order they were found and two
 * windows that touch would otherwise flicker between them frame by frame.
 * The window is half-open so a card ending where the next begins is a cut,
 * not a frame showing both.
 */
export const cardAt = (cards: Card[], seconds: number): Card | null => {
  let found: Card | null = null;
  for (const card of cards) {
    if (seconds >= card.start && seconds < card.end) {
      if (!found || card.start >= found.start) found = card;
    }
  }
  return found;
};

export type Tone = "ink" | "accent" | "muted" | "context";

export type Size = "xs" | "sm" | "md" | "lg" | "xl" | "xxl";

export type Align = "start" | "center" | "end";

export type Layout = "stack" | "row" | "grid";

export type Gap = "tight" | "group" | "section";

export type BarRow = {
  label: string;
  value: number;
  display?: string;
  subject?: boolean;
};

export type StepPoint = { value: string; note?: string };

type Common = {
  tone?: Tone;
  align?: Align;
  /** Share of a row's width, when the row has more than one child. */
  grow?: number;
};

export type Block =
  | (Common & {
      type: "text";
      text: string;
      /** Set apart in the card's accent, appended to the text. */
      emphasis?: string;
      size?: Size;
      caps?: boolean;
    })
  | (Common & { type: "quote"; text: string; attribution?: string; size?: Size })
  | (Common & { type: "list"; items: string[]; numbered?: boolean; size?: Size })
  | (Common & { type: "bars"; rows: BarRow[] })
  | (Common & { type: "meter"; value: number; display?: string })
  | (Common & { type: "steps"; points: StepPoint[] })
  | (Common & { type: "chip"; name: string; note?: string; src?: string })
  | (Common & {
      type: "media";
      media: "image" | "video";
      src?: string;
      fit?: "fit" | "fill";
      startAt?: number;
      caption?: string;
      /** Reference-canvas height it asks for before the fit pass runs. */
      height?: number;
    })
  | (Common & { type: "rule" })
  | (Common & { type: "group"; layout?: Layout; gap?: Gap; blocks: Block[] });

export type BlockType = Block["type"];

export const BLOCK_TYPES: readonly BlockType[] = [
  "text", "quote", "list", "bars", "meter", "steps", "chip", "media", "rule", "group",
];

/** Authored against the 1080x1920 canvas every other measurement here uses. */
export const TYPE_SIZE: Record<Size, number> = {
  xs: 34, sm: 46, md: 58, lg: 68, xl: 84, xxl: 168,
};

export const GAP_SIZE: Record<Gap, number> = { tight: 14, group: 36, section: 76 };

export const MEDIA_HEIGHT = { min: 260, max: 540 };

export const SCENE_WIDTH = 888;

export const MAX_BLOCKS = 14;

export const MAX_DEPTH = 3;

const DEFAULT_SIZE: Partial<Record<BlockType, Size>> = {
  text: "sm", quote: "lg", list: "md",
};

export const sizeOf = (block: Block): Size =>
  ("size" in block && block.size ? block.size : DEFAULT_SIZE[block.type] ?? "sm");

/**
 * Advance width of DM Sans at its heavier weights, as a fraction of the size.
 * Wrapping is estimated rather than measured because the same number has to
 * come out in the browser preview and in a headless render, and only one of
 * those can measure text before it lays out.
 */
const ADVANCE = 0.55;

const CAPS_ADVANCE = 0.68;

const LINE = 1.25;

const lines = (text: string, size: number, width: number, advance = ADVANCE) => {
  const perLine = Math.max(1, Math.floor(width / (size * advance)));
  return Math.max(1, Math.ceil(text.length / perLine));
};

const BAR_ROW = 26 + 8 + 34 * 1.3;

const STEP_ROW = 26 + 58 + 34 * 1.3 + 24;

const CHIP_ROW = 132;

const RULE_ROW = 5;

const METER_ROW = 26;

const listOf = (block: Block): Block[] =>
  (block.type === "group" ? block.blocks : []);

const gapOf = (block: Block): number =>
  GAP_SIZE[(block.type === "group" && block.gap) || "tight"];

/**
 * How tall a block is at the reference canvas, before any fit shrink.
 *
 * Deliberately an over-estimate on text: a card drawn a little smaller than it
 * had to be reads as a design choice, and one whose last line is behind the
 * caption pill reads as a bug.
 */
export function blockHeight(block: Block, width: number): number {
  switch (block.type) {
    case "text": {
      const size = TYPE_SIZE[sizeOf(block)];
      const text = block.emphasis ? `${block.text} ${block.emphasis}` : block.text;
      return lines(text, size, width, block.caps ? CAPS_ADVANCE : ADVANCE) * size * LINE;
    }
    case "quote": {
      const size = TYPE_SIZE[sizeOf(block)];
      const body = lines(block.text, size, width) * size * LINE;
      return body + (block.attribution ? GAP_SIZE.group + TYPE_SIZE.xs * LINE : 0);
    }
    case "list": {
      const size = TYPE_SIZE[sizeOf(block)];
      const marker = 46;
      return block.items.reduce(
        (total, item, i) =>
          total + (i ? GAP_SIZE.tight : 0) + lines(item, size, width - marker) * size * LINE,
        0,
      );
    }
    case "bars":
      return block.rows.length * BAR_ROW + Math.max(0, block.rows.length - 1) * 30;
    case "meter":
      return TYPE_SIZE.xxl * 0.72 + GAP_SIZE.tight + METER_ROW;
    case "steps":
      return STEP_ROW;
    case "chip":
      return Math.max(
        CHIP_ROW,
        TYPE_SIZE.md * LINE + (block.note ? lines(block.note, TYPE_SIZE.sm, width) * TYPE_SIZE.sm * LINE : 0),
      );
    case "media":
      return (block.height ?? MEDIA_HEIGHT.max)
        + (block.caption ? TYPE_SIZE.xs * LINE + GAP_SIZE.tight : 0);
    case "rule":
      return RULE_ROW;
    case "group":
      return groupHeight(block, width);
  }
}

function groupHeight(
  block: Extract<Block, { type: "group" }>, width: number,
): number {
  const kids = listOf(block);
  if (!kids.length) return 0;
  const gap = gapOf(block);
  const layout = block.layout ?? "stack";

  if (layout === "stack") {
    return kids.reduce(
      (total, kid, i) => total + (i ? gap : 0) + blockHeight(kid, width),
      0,
    );
  }

  const columns = layout === "grid" ? 2 : kids.length;
  const share = Math.max(1, (width - gap * (columns - 1)) / columns);
  if (layout === "row") {
    return Math.max(...kids.map((kid) => blockHeight(kid, share)));
  }

  let tallest = 0;
  let total = 0;
  kids.forEach((kid, i) => {
    tallest = Math.max(tallest, blockHeight(kid, share));
    if (i % columns === columns - 1 || i === kids.length - 1) {
      total += (total ? gap : 0) + tallest;
      tallest = 0;
    }
  });
  return total;
}

export const sceneHeight = (
  blocks: Block[], layout: Layout, gap: Gap, width = SCENE_WIDTH,
) => groupHeight({ type: "group", layout, gap, blocks }, width);

/** Never shrunk past this: below it the card is small rather than fitted. */
export const MIN_FIT = 0.55;

/**
 * How much a scene has to be scaled down to sit in the room it was given.
 *
 * Media is excluded from the shrink because a picture answers a height rather
 * than asking for one: the band it gets is what the fit pass leaves over.
 */
export function fitScale(
  blocks: Block[],
  { layout = "stack", gap = "group", room, width = SCENE_WIDTH }: {
    layout?: Layout; gap?: Gap; room: number; width?: number;
  },
): number {
  if (!(room > 0)) return 1;
  const wanted = sceneHeight(blocks, layout, gap, width);
  if (wanted <= room) return 1;
  return Math.max(MIN_FIT, room / wanted);
}

export const hasMedia = (blocks: Block[]): boolean =>
  blocks.some((block) =>
    block.type === "media" || (block.type === "group" && hasMedia(block.blocks)));

export function countBlocks(blocks: Block[]): number {
  return blocks.reduce(
    (total, block) => total + 1 + (block.type === "group" ? countBlocks(block.blocks) : 0),
    0,
  );
}

export function blockDepth(blocks: Block[]): number {
  return blocks.reduce(
    (deepest, block) =>
      Math.max(deepest, block.type === "group" ? 1 + blockDepth(block.blocks) : 1),
    0,
  );
}

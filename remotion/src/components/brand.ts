/**
 * The show's colours, or ours when it has not set any.
 *
 * Three values rather than a palette: the one colour that means "this is the
 * thing", what text is set in, and what it is set on. Everything else on a
 * card is one of those three at a lower opacity, which is what keeps a card
 * looking like the show rather than like a theme.
 */
export type Brand = { accent: string; ink: string; surface: string };

export const DEFAULT_BRAND: Brand = {
  accent: "#4C9DF5",
  ink: "#FFFFFF",
  surface: "#0A0D14",
};

/** Text that is not the point, and marks that are not the subject. */
export const muted = (ink: string) => `color-mix(in oklab, ${ink} 62%, transparent)`;

export const context = (ink: string) => `color-mix(in oklab, ${ink} 22%, transparent)`;

/** A meter's unfilled remainder: the fill's own hue, several steps lighter. */
export const track = (fill: string) => `color-mix(in oklab, ${fill} 22%, transparent)`;

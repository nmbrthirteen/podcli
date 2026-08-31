import { interpolate, spring } from "remotion";

/**
 * How a part of a clip arrives and leaves.
 *
 * Every move a short actually makes is in this list, which is why it is a list
 * and not a keyframe timeline: four values are editable in seconds, and a
 * timeline is a surface people open once. If something real cannot be said
 * here, that is the argument for keyframes, and not before.
 *
 * Each caption style ships the motion it already had, so a template that says
 * nothing renders exactly what it rendered before.
 */
export type Motion = {
  enter: "none" | "fade" | "rise" | "pop" | "slide";
  exit: "none" | "fade" | "sink";
  /** Frames at the composition's fps, for the fading half of the move. */
  duration: number;
  feel: "snap" | "soft" | "linear";
};

/** Spring shapes, named for how they read rather than for their constants. */
const FEEL: Record<Motion["feel"], { damping: number; stiffness: number; mass: number }> = {
  snap: { damping: 12, stiffness: 180, mass: 0.5 },
  soft: { damping: 20, stiffness: 90, mass: 0.7 },
  linear: { damping: 200, stiffness: 100, mass: 1 },
};

export const MOTION: Record<string, Motion> = {
  /** Word-by-word: springs up to size, no exit — the cut is the style. */
  hormozi: { enter: "pop", exit: "none", duration: 3, feel: "snap" },
  /** A line that is meant to be read, so it arrives and leaves quietly. */
  subtle: { enter: "rise", exit: "fade", duration: 5, feel: "soft" },
  /** Progressive highlight carries the movement; the block itself holds still. */
  karaoke: { enter: "none", exit: "none", duration: 0, feel: "linear" },
  /** The pill on the active word is the motion; the block holds still. */
  branded: { enter: "none", exit: "none", duration: 0, feel: "linear" },
  /**
   * Names arrive, hold, and get out of the way.
   *
   * In from the edge rather than up from nothing: a lower third that slides is
   * the move every broadcast makes, and it reads as a card being placed rather
   * than as a caption that faded up in the wrong place.
   */
  nameCard: { enter: "slide", exit: "fade", duration: 8, feel: "soft" },
  /**
   * A card takes the frame, so it has to arrive quickly enough not to read as
   * a slow wipe and leave without a gap where neither it nor the video is up.
   */
  card: { enter: "fade", exit: "fade", duration: 6, feel: "soft" },
  /** Always there, so it neither arrives nor leaves. */
  watermark: { enter: "none", exit: "none", duration: 0, feel: "linear" },
};

/**
 * Fade a thing in when it arrives and out before it leaves.
 *
 * Captions faded in and then cut, which reads as a flicker at every chunk
 * boundary: the outgoing line vanished on the same frame the incoming one
 * started at zero. Ramping the tail down inside the part's own window turns
 * that into a crossfade without moving a single caption timing.
 *
 * The out ramp is skipped on a window too short to hold full opacity, since a
 * line that fades in and straight back out never reads at all.
 */
export function fadeInOut({
  frame, fps, start, end, inFrames = 5, outFrames = 5,
}: {
  frame: number;
  fps: number;
  /** Seconds, on the composition's clock. */
  start: number;
  end: number;
  inFrames?: number;
  outFrames?: number;
}): number {
  const startFrame = Math.round(start * fps);
  const endFrame = Math.round(end * fps);

  const rising = inFrames > 0
    ? interpolate(frame - startFrame, [0, inFrames], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 1;

  const held = endFrame - startFrame;
  if (!outFrames || held < inFrames + outFrames + 2) return rising;

  const falling = interpolate(frame, [endFrame - outFrames, endFrame], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return Math.min(rising, falling);
}

/**
 * One part's motion at one frame, as the two properties worth animating.
 *
 * `scale` is kept separate from `shift` so a caller can compose them in the
 * order its layout needs; both are identity when the motion says none.
 */
export function motionAt({
  frame, fps, start, end, motion, scale: rise = 8,
}: {
  frame: number;
  fps: number;
  start: number;
  end: number;
  motion: Motion;
  /** How far a rise or a sink travels, already scaled to the canvas. */
  scale?: number;
}): { opacity: number; scale: number; shift: number; slide: number } {
  const startFrame = Math.round(start * fps);
  const since = frame - startFrame;

  const opacity = fadeInOut({
    frame, fps, start, end,
    inFrames: motion.enter === "none" ? 0 : motion.duration,
    outFrames: motion.exit === "none" ? 0 : motion.duration,
  });

  const scale = motion.enter === "pop"
    ? spring({ frame: since, fps, config: FEEL[motion.feel] })
    : 1;

  const entering = motion.enter === "rise"
    ? interpolate(since, [0, motion.duration + 1], [rise, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  const endFrame = Math.round(end * fps);
  const leaving = motion.exit === "sink"
    ? interpolate(frame, [endFrame - motion.duration, endFrame], [0, rise], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  /*
   * How far in from its own edge a sliding part still is, as a fraction of its
   * own width: -1 is entirely off, 0 is home.
   *
   * A fraction rather than pixels because the part knows its width and this
   * does not, and an element anchored to an edge is only reliably off screen
   * when it has been moved by its whole width. The caller turns it into a
   * percentage translate, which stays right at any composition size.
   */
  const slide = motion.enter === "slide"
    ? interpolate(since, [0, motion.duration + 1], [-1, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  return { opacity, scale, shift: entering + leaving, slide };
}

import { interpolate } from "remotion";

/**
 * Fade a thing in when it arrives and out before it leaves.
 *
 * Captions faded in and then cut, which reads as a flicker at every chunk
 * boundary: the outgoing line vanishes on the same frame the incoming one
 * starts at zero. Ramping the tail down inside the chunk's own window turns
 * that into a crossfade without moving a single caption timing.
 *
 * The out ramp is skipped on a chunk too short to hold full opacity, since a
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

  const rising = interpolate(frame - startFrame, [0, inFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const held = endFrame - startFrame;
  if (!outFrames || held < inFrames + outFrames + 2) return rising;

  const falling = interpolate(frame, [endFrame - outFrames, endFrame], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return Math.min(rising, falling);
}

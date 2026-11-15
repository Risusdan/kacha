// The timing every animation card shares. Pure functions of the frame and the card's length, so a card looks the
// same each time it is rendered. A card is `duration` frames long: frame 0 is its first, duration - 1 its last.

export const ENTER_FRAMES = 12;
export const EXIT_FRAMES = 8;
// A card shorter than this enters and leaves in proportionally fewer frames.
export const FULL_FRAMES = 30;
// A list item's tick is drawn in this many frames after the item appears.
export const TICK_FRAMES = 6;

const clamp01 = (value) => Math.min(1, Math.max(0, value));

/** The frames of the entrance and of the exit of a card `duration` frames long. */
export function inOut(duration) {
  if (duration >= FULL_FRAMES) {
    return { enter: ENTER_FRAMES, exit: EXIT_FRAMES };
  }
  const length = Math.max(0, duration);
  return {
    enter: Math.round((ENTER_FRAMES * length) / FULL_FRAMES),
    exit: Math.round((EXIT_FRAMES * length) / FULL_FRAMES),
  };
}

/** 0 at the first frame, 1 once the entrance is over (and from the start when there is no entrance). */
export function enterProgress(frame, duration) {
  const { enter } = inOut(duration);
  return enter === 0 ? 1 : clamp01(frame / enter);
}

/** 0 until the exit starts, 1 at the last frame (and always 0 when there is no exit). */
export function exitProgress(frame, duration) {
  const { exit } = inOut(duration);
  return exit === 0 ? 0 : clamp01((frame - (duration - 1 - exit)) / exit);
}

/**
 * The frame each of `count` list items appears at: the first when the entrance ends, the others spread evenly up
 * to the middle of the card (no earlier than the first).
 */
export function stagger(count, duration) {
  const { enter } = inOut(duration);
  const last = Math.max(enter, Math.floor(duration / 2));
  const step = count > 1 ? (last - enter) / (count - 1) : 0;
  return Array.from({ length: count }, (_, index) => enter + Math.round(index * step));
}

/** 0 until the item appears at frame `start`, 1 when its tick is fully drawn. */
export function tickProgress(frame, start) {
  return clamp01((frame - start) / TICK_FRAMES);
}

// A bar of a bar chart grows in this many frames, then its value fades in in this many, on a long enough card.
export const GROW_FRAMES = 12;
export const VALUE_FRAMES = 6;

/**
 * When each of `count` bars starts to grow, and how many frames a bar grows in and its value fades in in, so that
 * every value is in by the middle of the frames between the end of the entrance and the start of the exit, and the
 * second half is left for reading them. The bars start one after another from the end of the entrance; when there
 * are not GROW_FRAMES + VALUE_FRAMES frames to the middle, they all start then, and grow and fade in in proportion.
 */
export function barTimes(count, duration) {
  const { enter, exit } = inOut(duration);
  const end = Math.max(enter, duration - 1 - exit);
  const room = Math.floor((end - enter) / 2);
  let grow = GROW_FRAMES;
  let value = VALUE_FRAMES;
  if (room < GROW_FRAMES + VALUE_FRAMES) {
    grow = Math.round((room * GROW_FRAMES) / (GROW_FRAMES + VALUE_FRAMES));
    value = room - grow;
  }
  const last = enter + room - grow - value;
  const step = count > 1 ? (last - enter) / (count - 1) : 0;
  return { starts: Array.from({ length: count }, (_, index) => enter + Math.round(index * step)), grow, value };
}

/** 0 until frame `start`, 1 `length` frames later (from `start` itself when `length` is 0). */
export function barProgress(frame, start, length) {
  if (length === 0) {
    return frame >= start ? 1 : 0;
  }
  return clamp01((frame - start) / length);
}

// The pure functions of the review page: times on the source and on the cut reel, the caption on screen, what
// plays where, whether a polled version is a change of Claude's, where a popover goes, and what a caption being
// edited marks, shows and saves. No DOM; review.js imports them, and tests/js/logic.test.mjs tests them.
//
// A segment is [start, end] in source seconds (GET /api/data's "segments": what stays in the reel, in order); a
// source time is a time of the review video and the transcript, a reel time one of the cut reel. clock and
// reelTime are the twins of storyboard.clock and edit.map_time: the same operations in the same order, so the page
// shows the times the storyboard shows.

// Segment times are on the 30 fps grid rounded to the millisecond, so a start may be up to half a millisecond
// before the frame it stands for (frame 145 is at 4.8333 s, its start says 4.833), and a seek to it would show the
// frame before, the last of the cut. A seek into a segment goes this far past its start; and a time this little
// before a start counts as inside the segment, so a seek that lands a hair before it does not seek again.
export const SEAM_S = 0.001;

// `seconds` as m:ss.s (minutes, two digits of seconds, one tenth), rounded to the tenth; 0:00.0 for a negative time.
export function clock(seconds) {
  const tenths = Math.max(0, Math.trunc(seconds * 10 + 0.5));
  const minutes = Math.floor(tenths / 600);
  const secs = String(Math.floor(tenths / 10) % 60).padStart(2, "0");
  return `${minutes}:${secs}.${tenths % 10}`;
}

// The reel time of source time `t`: inside a segment, shifted by the length of the segments before it; inside a
// cut, the start of the next segment; before the first segment 0, after the last the length of the reel.
export function reelTime(t, segments) {
  let offset = 0;
  for (const [start, end] of segments) {
    if (t < start) return offset;
    if (t < end) return offset + (t - start);
    offset += end - start;
  }
  return offset;
}

// The source time of reel time `r`: the inverse of reelTime. A joint of two segments is the start of the later
// one; before the reel, the start of the first segment; at or after its end, the end of the last.
export function sourceTime(r, segments) {
  let offset = 0;
  for (const [start, end] of segments) {
    const length = end - start;
    if (r < offset + length) return start + Math.max(0, r - offset);
    offset += length;
  }
  return segments.length ? segments[segments.length - 1][1] : 0;
}

// Where the video has to go from source time `t` while it plays: SEAM_S past the start of the next segment when `t`
// is in a cut or before the first segment, null when `t` is inside a segment, -1 at or after the end of the last one.
// With a lookahead `ahead` (the length of a frame, for `t` the time of the frame on screen), `t` counts as past
// the end of its segment once `t + ahead` reaches it, give or take SEAM_S (segment ends are rounded to the
// millisecond): the frame on screen is the segment's last, and the next one would be the first of the cut. And `t`
// counts as inside a segment from one frame before its start: a frame shown just before a start is followed by the
// segment's first, and seeking again could show the same frame again.
export function skipTarget(t, segments, ahead = 0) {
  const next = ahead > 0 ? t + ahead + SEAM_S : t;
  const early = ahead > 0 ? ahead + SEAM_S : SEAM_S; // this far before a start counts as inside the segment
  for (const [start, end] of segments) {
    if (t < start - early) return start + SEAM_S;
    if (next < end) return null;
  }
  return -1;
}

// Where to put the video for source time `t`: `t` inside a segment, but never before SEAM_S past its start; a hair
// (SEAM_S) past the start of the next segment in a cut or before the first; the end of the last segment after it.
export function seekPoint(t, segments) {
  const target = skipTarget(t, segments);
  if (target === -1) return segments.length ? segments[segments.length - 1][1] : 0;
  if (target !== null) return target;
  const [start] = segments.find(([, end]) => t < end);
  return Math.max(t, start + SEAM_S);
}

// The caption on screen at reel time `r` (start <= r < end), or null.
export function captionAt(r, captions) {
  return captions.find((caption) => caption.start <= r && r < caption.end) ?? null;
}

// How many of the caption's pieces, from the first, are already spoken at reel time `r` (word mode lights them).
// A piece with a time lights at that time, except the first one, which lights when the caption shows (as the \k
// tags of the render do); a piece without a time (a mark, a space) goes with the piece before it, and those before
// the first timed piece with that one.
export function litPieces(caption, r) {
  if (r < caption.start) return 0;
  let first = true;
  for (let i = 0; i < caption.pieces.length; i += 1) {
    const t = caption.pieces[i].t;
    if (t === null || t === undefined) continue;
    if (!first && t > r) return i;
    first = false;
  }
  return caption.pieces.length;
}

// The kept sentence whose source span (start <= t < end) holds source time `t`, or null.
export function sentenceAt(t, sentences) {
  return sentences.find((sentence) => sentence.state === "kept" && sentence.start <= t && t < sentence.end) ?? null;
}

// The source time `lead` reel seconds before source time `t`, never before the start of the reel: where the video
// waits when the page goes to an item.
export function leadIn(t, segments, lead) {
  return sourceTime(Math.max(0, reelTime(t, segments) - lead), segments);
}

// What the page does with the version a poll of GET /api/state found (`polled`): { load, notice, own }.
// `shown` is the version of the data on the page (null before the first load), `failed` whether the last load
// failed (the banner shows), `own` the version this page's last change made (null once a poll has seen it), and
// `outdated` whether a change of the page's started or landed while the poll was out (its answer may be from
// before the change). load says to load the data again, notice to say that Claude changed the edit, own is the
// own version to keep. A version that is not the page's own last one is Claude's, even one with the bytes of an
// earlier version the page made.
export function pollVersion({ shown, failed, polled, own, outdated }) {
  if (outdated) return { load: false, notice: false, own };
  if (shown === null) return { load: true, notice: false, own: null };
  if (polled === shown) return { load: failed, notice: false, own: null };
  return { load: true, notice: polled !== own, own: null };
}

// The space between a popover and its mark, and between a popover and the sides of the panel, in pixels.
export const POP_GAP = 6;
export const POP_MARGIN = 8;

// Where a popover of `size` ({ width, height }) goes for a mark at `anchor` ({ top, bottom, left }), all in the
// coordinates of the panel's content; `view` ({ top, left, width, height }) is the part of the content the panel
// shows. Below the mark when it fits in the view, else above it when that fits, else below it with the panel
// scrolled down so the whole popover shows (as far as the mark stays in view). Left is the mark's, kept inside the
// panel's sides. Returns { top, left, scrollTop }: scrollTop is the panel's new scroll position.
export function placePopover({ anchor, size, view }) {
  const right = view.left + view.width - size.width - POP_MARGIN;
  const left = Math.max(view.left + POP_MARGIN, Math.min(anchor.left, right));
  const below = anchor.bottom + POP_GAP;
  if (below + size.height + POP_GAP <= view.top + view.height) return { top: below, left, scrollTop: view.top };
  const above = anchor.top - POP_GAP - size.height;
  if (above >= view.top + POP_GAP) return { top: above, left, scrollTop: view.top };
  const scrollTop = Math.min(below + size.height + POP_GAP - view.height, anchor.top - POP_GAP);
  return { top: below, left, scrollTop };
}

// The index in `tokens` (a sentence's tokens, as GET /api/data gives them) of the unit closest to source time `t`
// that no candidate has taken and that has a time, or -1. The distance is 0 inside a unit; a tie goes to the
// earlier unit. The page underlines that unit for a cut point at the sentence's edge.
export function nearestUnit(tokens, t) {
  let best = -1;
  let bestDistance = Infinity;
  tokens.forEach((token, index) => {
    if (token.k !== "unit" || token.trim || token.start === null || token.end === null) return;
    const distance = t < token.start ? token.start - t : t > token.end ? t - token.end : 0;
    if (distance < bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
}

// The most cells of the table changedRuns compares two texts with; past it, the part between their common head and
// tail all counts as changed (the table would take too long for a field being typed in).
export const DIFF_CELLS = 1000000;

// The caption text `edited` as runs [{ text, changed }] in order, against the sentence's own text `original`: a
// character of `edited` is unchanged when it is in a longest common subsequence of the two, compared code point by
// code point, and changed otherwise (replaced or added). A character taken out leaves no mark. Neighbouring runs
// differ in `changed`; an empty `edited` gives [].
export function changedRuns(original, edited) {
  const a = Array.from(original);
  const b = Array.from(edited);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) {
    tail += 1;
  }
  const x = a.slice(head, a.length - tail);
  const y = b.slice(head, b.length - tail);
  const changed = b.map((_, k) => k >= head && k < b.length - tail);
  if (x.length > 0 && y.length > 0 && x.length * y.length <= DIFF_CELLS) {
    // common[i][j]: the length of a longest common subsequence of x from i and y from j
    const common = Array.from({ length: x.length + 1 }, () => new Uint32Array(y.length + 1));
    for (let i = x.length - 1; i >= 0; i -= 1) {
      for (let j = y.length - 1; j >= 0; j -= 1) {
        common[i][j] = x[i] === y[j] ? common[i + 1][j + 1] + 1 : Math.max(common[i + 1][j], common[i][j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < x.length && j < y.length) {
      if (x[i] === y[j]) {
        changed[head + j] = false;
        i += 1;
        j += 1;
      } else if (common[i + 1][j] >= common[i][j + 1]) {
        i += 1;
      } else {
        j += 1;
      }
    }
  }
  const runs = [];
  b.forEach((char, k) => {
    const last = runs[runs.length - 1];
    if (last && last.changed === changed[k]) last.text += char;
    else runs.push({ text: char, changed: changed[k] });
  });
  return runs;
}

// The line of `lines` (the caption lines a sentence being edited would get) on screen at reel time `r`, for a
// sentence from reel time `start` to `end`: the lines share its time equally, in order. null outside the sentence,
// and when there are no lines.
export function previewLine(r, start, end, lines) {
  if (lines.length === 0 || !(r >= start && r < end)) return null;
  return lines[Math.min(lines.length - 1, Math.floor(((r - start) / (end - start)) * lines.length))];
}

// What saving the caption editor's field `text` sends as the change's text, for a sentence whose caption is `saved`
// now and whose own text is `original`: undefined when there is nothing to send (the text is the caption already),
// null when the text is the sentence's own again (its caption text goes), else the text.
export function captionToSave(text, saved, original) {
  if (text === saved) return undefined;
  return text === original ? null : text;
}

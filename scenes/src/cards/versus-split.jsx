// versus-split (對比): a white card of two columns with an accent "VS" circle between them. Each column is a big
// bold line (left, right) over an optional small line in the muted colour (left_note, right_note), from the top of
// the card's inside, so that the two big lines are level when only one has a note. Both columns are
// as wide as the widest of the four texts (two equal grid columns), so the circle sits in the middle of the card.
// The left column slides in from the left and the right one from the right as the card enters; the circle pops at
// the middle of the entrance. The card fades as it leaves. params: {left, left_note?, right, right_note?}. Its
// sizes are in sizes.json.

import { Easing, interpolate, useCurrentFrame } from "remotion";
import { WEIGHTS, Zone } from "../Card.jsx";
import SIZES from "../sizes.json";
import { enterProgress, exitProgress, inOut } from "../timing.js";

const SIZE = SIZES["versus-split"];
const SLIDE = 60; // how far beside its place a column starts, in pixels
const POP_FRAMES = 8; // the circle grows past its size and settles back in this many frames

const easeOut = Easing.out(Easing.cubic);

export function VersusSplit({ frames, params, style, align = "center" }) {
  const frame = useCurrentFrame();
  const { enter } = inOut(frames);
  const entered = enterProgress(frame, frames);
  const leave = exitProgress(frame, frames);
  const popStart = Math.round(enter / 2);
  const pop = enter === 0 ? 1 : Math.min(1, Math.max(0, (frame - popStart) / POP_FRAMES));
  const badgeScale = interpolate(pop, [0, 0.6, 1], [0, 1.15, 1]);
  const slide = (1 - easeOut(entered)) * SLIDE;
  const column = (name, side) => (
    <div
      style={{
        opacity: entered,
        transform: `translateX(${side * slide}px)`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
      }}
    >
      <div
        style={{
          fontWeight: WEIGHTS[SIZE.side.weight],
          fontSize: SIZE.side.size,
          lineHeight: SIZE.side.line_height,
          color: style.colors.text,
          whiteSpace: "nowrap",
        }}
      >
        {params[name].trim()}
      </div>
      {params[`${name}_note`]?.trim() ? (
        <div
          style={{
            marginTop: SIZE.note_gap,
            fontWeight: WEIGHTS[SIZE.note.weight],
            fontSize: SIZE.note.size,
            lineHeight: SIZE.note.line_height,
            color: style.colors.muted,
            whiteSpace: "nowrap",
          }}
        >
          {params[`${name}_note`].trim()}
        </div>
      ) : null}
    </div>
  );
  return (
    <Zone style={style} align={align}>
      <div
        style={{
          opacity: Math.min(1, 2 * entered) * (1 - leave),
          backgroundColor: style.colors.card,
          borderRadius: style.radius_px,
          padding: `${SIZE.padding_y}px ${SIZE.padding_x}px`,
          overflow: "hidden",
          display: "grid",
          gridTemplateColumns: `1fr ${SIZE.badge}px 1fr`,
          columnGap: SIZE.badge_gap,
          alignItems: "start",
        }}
      >
        {column("left", -1)}
        <div
          style={{
            width: SIZE.badge,
            height: SIZE.badge,
            borderRadius: SIZE.badge / 2,
            backgroundColor: style.colors.accent,
            alignSelf: "center",
            transform: `scale(${badgeScale})`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: WEIGHTS[SIZE.badge_text.weight],
            fontSize: SIZE.badge_text.size,
            lineHeight: SIZE.badge_text.line_height,
            color: style.colors.text,
          }}
        >
          VS
        </div>
        {column("right", 1)}
      </div>
    </Zone>
  );
}

// title-card (標題卡): a white card with an optional small line (kicker, in the muted colour) over a big bold title,
// and an accent underline as wide as the title under it. The card slides up 40 px from below and fades in as it
// enters; the underline is drawn from left to right in the second half of the entrance. The card fades as it
// leaves. params: {kicker?, title}. Its sizes are in sizes.json.

import { Easing, useCurrentFrame } from "remotion";
import { WEIGHTS, Zone } from "../Card.jsx";
import SIZES from "../sizes.json";
import { enterProgress, exitProgress } from "../timing.js";

const SIZE = SIZES["title-card"];
const RISE = 40; // how far below its place the card starts, in pixels

const easeOut = Easing.out(Easing.cubic);
const clamp01 = (value) => Math.min(1, Math.max(0, value));

export function TitleCard({ frames, params, style, align = "center" }) {
  const frame = useCurrentFrame();
  const entered = enterProgress(frame, frames);
  const drawn = easeOut(clamp01(2 * entered - 1));
  const leave = exitProgress(frame, frames);
  return (
    <Zone style={style} align={align}>
      <div
        style={{
          opacity: Math.min(1, 2 * entered) * (1 - leave),
          transform: `translateY(${(1 - easeOut(entered)) * RISE}px)`,
          backgroundColor: style.colors.card,
          borderRadius: style.radius_px,
          padding: `${SIZE.padding_y}px ${SIZE.padding_x}px`,
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
        }}
      >
        {params.kicker?.trim() ? (
          <div
            style={{
              marginBottom: SIZE.kicker_gap,
              fontWeight: WEIGHTS[SIZE.kicker.weight],
              fontSize: SIZE.kicker.size,
              lineHeight: SIZE.kicker.line_height,
              color: style.colors.muted,
              whiteSpace: "nowrap",
            }}
          >
            {params.kicker.trim()}
          </div>
        ) : null}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div
            style={{
              fontWeight: WEIGHTS[SIZE.title.weight],
              fontSize: SIZE.title.size,
              lineHeight: SIZE.title.line_height,
              color: style.colors.text,
              whiteSpace: "nowrap",
            }}
          >
            {params.title.trim()}
          </div>
          <div
            style={{
              marginTop: SIZE.underline_gap,
              height: SIZE.underline,
              borderRadius: SIZE.underline / 2,
              backgroundColor: style.colors.accent,
              transform: `scaleX(${drawn})`,
              transformOrigin: "0% 50%",
            }}
          />
        </div>
      </div>
    </Zone>
  );
}

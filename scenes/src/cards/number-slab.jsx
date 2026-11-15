// number-slab (數字卡): a white card with a big number over an accent highlight band, and an optional line under
// it. The number slides up from below as the card enters; the line fades in half an entrance later (6 frames at full
// length). The card fades as it leaves. params: {value, label?}. Its sizes are in sizes.json.

import { Easing, interpolate, useCurrentFrame } from "remotion";
import { WEIGHTS, Zone } from "../Card.jsx";
import SIZES from "../sizes.json";
import { enterProgress, exitProgress, inOut } from "../timing.js";

const SIZE = SIZES["number-slab"];

const easeOut = Easing.out(Easing.cubic);

export function NumberSlab({ frames, params, style }) {
  const frame = useCurrentFrame();
  const { enter } = inOut(frames);
  const entered = enterProgress(frame, frames);
  const labelIn = enterProgress(frame - Math.round(enter / 2), frames);
  const leave = exitProgress(frame, frames);
  const rise = interpolate(easeOut(entered), [0, 1], [100, 0]);
  return (
    <Zone style={style}>
      <div
        style={{
          opacity: Math.min(1, 2 * entered) * (1 - leave),
          backgroundColor: style.colors.card,
          borderRadius: style.radius_px,
          padding: `40px ${SIZE.padding_x}px`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <div style={{ overflow: "hidden" }}>
          <div
            style={{
              transform: `translateY(${rise}%)`,
              fontWeight: WEIGHTS[SIZE.value.weight],
              fontSize: SIZE.value.size,
              lineHeight: 1.15,
              color: style.colors.text,
              whiteSpace: "nowrap",
              backgroundImage: `linear-gradient(transparent 55%, ${style.colors.accent} 55%, ${style.colors.accent} 92%, transparent 92%)`,
            }}
          >
            {params.value}
          </div>
        </div>
        {params.label ? (
          <div
            style={{
              opacity: labelIn,
              marginTop: 8,
              fontWeight: WEIGHTS[SIZE.label.weight],
              fontSize: SIZE.label.size,
              lineHeight: 1.3,
              color: style.colors.muted,
              whiteSpace: "nowrap",
            }}
          >
            {params.label}
          </div>
        ) : null}
      </div>
    </Zone>
  );
}

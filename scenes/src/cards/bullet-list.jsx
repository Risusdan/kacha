// bullet-list (清單): a white card of 2 to 4 items, each after an accent circle with a tick. The items appear one by
// one from the end of the entrance to the middle of the card; each tick is drawn in the 6 frames after its item
// appears. The card fades as it leaves. params: {items}. Its sizes are in sizes.json.

import { useCurrentFrame } from "remotion";
import { WEIGHTS, Zone } from "../Card.jsx";
import SIZES from "../sizes.json";
import { enterProgress, exitProgress, stagger, tickProgress } from "../timing.js";

const SIZE = SIZES["bullet-list"];
const TICK = SIZE.tick; // the size of the circle, in pixels; the tick path is drawn for 56
const TICK_PATH = "M 15 29 L 24 38 L 41 19";
const TICK_LENGTH = 40; // a little over the path's length (38.2), so a dash of this length hides it whole

export function BulletList({ frames, params, style, align = "center" }) {
  const frame = useCurrentFrame();
  const entered = enterProgress(frame, frames);
  const leave = exitProgress(frame, frames);
  const starts = stagger(params.items.length, frames);
  return (
    <Zone style={style} align={align}>
      <div
        style={{
          opacity: Math.min(1, 2 * entered) * (1 - leave),
          backgroundColor: style.colors.card,
          borderRadius: style.radius_px,
          padding: `${SIZE.padding_y}px ${SIZE.padding_x}px`,
          display: "flex",
          flexDirection: "column",
          gap: SIZE.item_gap,
        }}
      >
        {params.items.map((item, index) => {
          const shown = tickProgress(frame, starts[index]);
          return (
            <div
              key={index}
              style={{
                opacity: shown,
                transform: `translateX(${(1 - shown) * -24}px)`,
                display: "flex",
                alignItems: "center",
                gap: SIZE.tick_gap,
              }}
            >
              <svg width={TICK} height={TICK} viewBox={`0 0 ${TICK} ${TICK}`} style={{ flex: "none" }}>
                <circle cx={TICK / 2} cy={TICK / 2} r={TICK / 2} fill={style.colors.accent} />
                <path
                  d={TICK_PATH}
                  fill="none"
                  stroke={style.colors.text}
                  strokeWidth={6}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeDasharray={TICK_LENGTH}
                  strokeDashoffset={TICK_LENGTH * (1 - shown)}
                />
              </svg>
              <div
                style={{
                  fontWeight: WEIGHTS[SIZE.items.weight],
                  fontSize: SIZE.items.size,
                  lineHeight: SIZE.items.line_height,
                  color: style.colors.text,
                  whiteSpace: "nowrap",
                }}
              >
                {item.trim()}
              </div>
            </div>
          );
        })}
      </div>
    </Zone>
  );
}

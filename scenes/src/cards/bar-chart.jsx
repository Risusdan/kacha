// bar-chart (長條圖): a white card of 2 to 4 rows, each a label over an accent bar with its value (bold) on the
// right. A bar is as long as the number its value starts with (barNumber) over the largest of them: the largest
// fills the track, a 0 has no bar. The track has a fixed length and the values line up in a column on its right,
// so the card keeps its size as the bars grow. The rows appear one by one from the end of the entrance; each bar
// grows from 0, then its value fades in, all by the middle of the frames before the exit (barTimes). The card fades
// as it leaves. params: {bars: [{label, value}]}. Its sizes are in sizes.json.

import { Easing, useCurrentFrame } from "remotion";
import { WEIGHTS, Zone } from "../Card.jsx";
import { barNumber } from "../number.js";
import SIZES from "../sizes.json";
import { barProgress, barTimes, enterProgress, exitProgress } from "../timing.js";

const SIZE = SIZES["bar-chart"];

const easeOut = Easing.out(Easing.cubic);

export function BarChart({ frames, params, style, align = "center" }) {
  const frame = useCurrentFrame();
  const entered = enterProgress(frame, frames);
  const leave = exitProgress(frame, frames);
  const { starts, grow, value: fade } = barTimes(params.bars.length, frames);
  const numbers = params.bars.map((bar) => barNumber(bar.value) ?? 0);
  const largest = Math.max(...numbers);
  const text = (group) => ({
    fontWeight: WEIGHTS[SIZE[group].weight],
    fontSize: SIZE[group].size,
    lineHeight: SIZE[group].line_height,
    color: style.colors.text,
    whiteSpace: "nowrap",
  });
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
          alignItems: "flex-start",
          gap: SIZE.row_gap,
        }}
      >
        {params.bars.map((bar, index) => {
          const grown = barProgress(frame, starts[index], grow);
          const valueIn = barProgress(frame, starts[index] + grow, fade);
          const length = largest > 0 ? numbers[index] / largest : 0;
          return (
            <div key={index}>
              <div style={{ ...text("label"), opacity: Math.min(1, 2 * grown), marginBottom: SIZE.label_gap }}>
                {bar.label.trim()}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: SIZE.bar_gap }}>
                <div style={{ width: SIZE.track, height: SIZE.bar, flex: "none" }}>
                  <div
                    style={{
                      width: SIZE.track * length * easeOut(grown),
                      height: "100%",
                      borderRadius: SIZE.bar / 4,
                      backgroundColor: style.colors.accent,
                    }}
                  />
                </div>
                <div style={{ ...text("value"), opacity: valueIn }}>{bar.value.trim()}</div>
              </div>
            </div>
          );
        })}
      </div>
    </Zone>
  );
}

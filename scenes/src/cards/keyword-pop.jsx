// keyword-pop (重點字): one bold line on an accent pill. It springs from 0.6 to full size as it enters, and fades
// while shrinking to 0.9 as it leaves, about the middle of the edge of the zone it is flush with (ORIGIN). params:
// {text}. Its sizes are in sizes.json, where `grow` is the largest scale the spring reaches (1.038), which validate
// measures the pill at.

import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { ORIGIN, WEIGHTS, Zone } from "../Card.jsx";
import SIZES from "../sizes.json";
import { enterProgress, exitProgress, inOut } from "../timing.js";

const SIZE = SIZES["keyword-pop"];

export function KeywordPop({ frames, params, style, align = "center" }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { enter } = inOut(frames);
  const pop = enter === 0 ? 1 : spring({ frame, fps, durationInFrames: enter, config: { damping: 12 } });
  const leave = exitProgress(frame, frames);
  const scale = (0.6 + 0.4 * pop) * (1 - 0.1 * leave);
  const opacity = Math.min(1, 2 * enterProgress(frame, frames)) * (1 - leave);
  return (
    <Zone style={style} align={align}>
      <div
        style={{
          transform: `scale(${scale})`,
          transformOrigin: ORIGIN[align],
          opacity,
          backgroundColor: style.colors.accent,
          color: style.colors.text,
          borderRadius: style.radius_px,
          padding: `${SIZE.padding_y}px ${SIZE.padding_x}px`,
          fontWeight: WEIGHTS[SIZE.text.weight],
          fontSize: SIZE.text.size,
          lineHeight: SIZE.text.line_height,
          whiteSpace: "nowrap",
        }}
      >
        {params.text.trim()}
      </div>
    </Zone>
  );
}

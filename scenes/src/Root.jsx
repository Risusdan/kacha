// One composition per card: 1080x1920 at 30 fps, as long as the `frames` prop says. The default props are a
// made-up example, for looking at a card in Remotion Studio; render.mjs always passes its own.

import { Composition } from "remotion";
import { BarChart } from "./cards/bar-chart.jsx";
import { BulletList } from "./cards/bullet-list.jsx";
import { KeywordPop } from "./cards/keyword-pop.jsx";
import { NumberSlab } from "./cards/number-slab.jsx";
import { TitleCard } from "./cards/title-card.jsx";
import { VersusSplit } from "./cards/versus-split.jsx";

export const CARDS = {
  "keyword-pop": { component: KeywordPop, example: { text: "亮度最耗電" } },
  "number-slab": { component: NumberSlab, example: { value: "兩小時", label: "一天多撐" } },
  "bullet-list": { component: BulletList, example: { items: ["亮度", "背景更新", "定位"] } },
  "title-card": { component: TitleCard, example: { kicker: "三個省電技巧", title: "亮度才是關鍵" } },
  "bar-chart": {
    component: BarChart,
    example: { bars: [{ label: "去年", value: "49%" }, { label: "今年", value: "96%" }] },
  },
  "versus-split": {
    component: VersusSplit,
    example: { left: "自動亮度", left_note: "比較省電", right: "手動調整", right_note: "看得清楚" },
  },
};

const EXAMPLE_STYLE = {
  zone: { x0: 65, x1: 1015, y0: 269, y1: 1040 },
  colors: { card: "#FFFFFF", text: "#111111", accent: "#FFD400", muted: "#5C5C5C" },
  radius_px: 32,
  fonts: { regular: "fonts/NotoSansTC-Regular.ttf", bold: "fonts/NotoSansTC-Bold.ttf" },
};

const lengthFromProps = ({ props }) => ({ durationInFrames: props.frames });

export function Root() {
  return (
    <>
      {Object.entries(CARDS).map(([id, { component, example }]) => (
        <Composition
          key={id}
          id={id}
          component={component}
          width={1080}
          height={1920}
          fps={30}
          durationInFrames={90}
          defaultProps={{ frames: 90, params: example, style: EXAMPLE_STYLE, align: "center" }}
          calculateMetadata={lengthFromProps}
        />
      ))}
    </>
  );
}

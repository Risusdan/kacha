import {
  AbsoluteFill,
  Composition,
  Easing,
  cancelRender,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";

// Bench scene (phase 0 spike): transparent 1080x1920, white card slides up to y=780 in 0-0.5 s.
const FAMILY = "BenchNotoSansTC";
const fontHandle = delayRender("load Noto Sans TC Bold");
const face = new FontFace(FAMILY, `url(${staticFile("NotoSansTC-Bold.ttf")})`, { weight: "700" });
face
  .load()
  .then(() => {
    document.fonts.add(face);
    continueRender(fontHandle);
  })
  .catch((err) => cancelRender(err));

const LOOP = 150; // 5 s at 30 fps

export const BenchCard: React.FC = () => {
  const frame = useCurrentFrame() % LOOP;
  const top = interpolate(frame, [0, 15], [1920, 780], {
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <div
        style={{
          position: "absolute",
          left: 90,
          top,
          width: 900,
          height: 360,
          borderRadius: 32,
          backgroundColor: "#FFFFFF",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "0 48px",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            fontFamily: FAMILY,
            fontWeight: 700,
            fontSize: 64,
            lineHeight: 1.3,
            color: "#111111",
            textAlign: "center",
          }}
        >
          三個步驟，剪出不像 AI 的短影音
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const MyComposition = () => {
  return (
    <>
      <Composition id="Bench5" component={BenchCard} durationInFrames={150} fps={30} width={1080} height={1920} />
      <Composition id="Bench60" component={BenchCard} durationInFrames={1800} fps={30} width={1080} height={1920} />
    </>
  );
};

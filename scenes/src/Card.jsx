// What every card shares: the style's fonts, loaded before the first frame is taken, and the animation zone the
// card is drawn in (on the zone's top edge, centred or flush with its left or right edge; nothing outside the zone
// is drawn).
//
// Props of every card: {frames, params, style, align}. `style` holds the style.json tokens the cards use:
// {zone: {x0, x1, y0, y1}, colors: {card, text, accent, muted}, radius_px, fonts: {regular, bold}}, the font
// paths relative to the style folder (the bundle's public folder). `align` is "center", "left" or "right"
// (pipeline/card_place.py picks it, away from the speaker's head); "center" when it is left out.
//
// A card draws each text without the spaces at its ends (trim), as validate measures it (pipeline/scenes.py), and
// leaves out the line of an optional text that is empty once trimmed.

import { useEffect, useState } from "react";
import { AbsoluteFill, cancelRender, continueRender, delayRender, staticFile } from "remotion";

// A name of our own, so the page never picks an installed font of the same family name.
export const FAMILY = "KachaCard";

// The CSS weight of each font of the style, as sizes.json names them.
export const WEIGHTS = { regular: 400, bold: 700 };

function useStyleFonts(fonts) {
  const [handle] = useState(() => delayRender("load the style's fonts"));
  useEffect(() => {
    const faces = [
      new FontFace(FAMILY, `url(${staticFile(fonts.regular)})`, { weight: "400" }),
      new FontFace(FAMILY, `url(${staticFile(fonts.bold)})`, { weight: "700" }),
    ];
    Promise.all(faces.map((face) => face.load()))
      .then((loaded) => {
        loaded.forEach((face) => document.fonts.add(face));
        continueRender(handle);
      })
      .catch((error) => cancelRender(error));
  }, [fonts.regular, fonts.bold, handle]);
}

// Where a card of each align sits along the zone's top edge, as a flex justification.
const JUSTIFY = { left: "flex-start", center: "center", right: "flex-end" };

// The point of a card that stays still as it is scaled, for each align: the middle of the edge it is flush with,
// so that a card growing past its size at rest is not cut off by that edge of the zone.
export const ORIGIN = { left: "0% 50%", center: "50% 50%", right: "100% 50%" };

export function Zone({ style, align = "center", children }) {
  useStyleFonts(style.fonts);
  const { zone } = style;
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <div
        style={{
          position: "absolute",
          left: zone.x0,
          top: zone.y0,
          width: zone.x1 - zone.x0,
          height: zone.y1 - zone.y0,
          overflow: "hidden",
          display: "flex",
          justifyContent: JUSTIFY[align],
          alignItems: "flex-start",
          fontFamily: FAMILY,
        }}
      >
        {children}
      </div>
    </AbsoluteFill>
  );
}

// What every card shares: the style's fonts, loaded before the first frame is taken, and the animation zone the
// card is drawn in (centred, on the zone's top edge; nothing outside the zone is drawn).
//
// Props of every card: {frames, params, style}. `style` holds the style.json tokens the cards use:
// {zone: {x0, x1, y0, y1}, colors: {card, text, accent, muted}, radius_px, fonts: {regular, bold}}, the font
// paths relative to the style folder (the bundle's public folder).

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

export function Zone({ style, children }) {
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
          justifyContent: "center",
          alignItems: "flex-start",
          fontFamily: FAMILY,
        }}
      >
        {children}
      </div>
    </AbsoluteFill>
  );
}

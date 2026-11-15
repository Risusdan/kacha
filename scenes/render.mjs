// Renders animation cards to transparent ProRes 4444 files, and stills of them to transparent PNG files, for
// pipeline/scene_render.py.
//
//   node scenes/render.mjs <jobs.json>
//
// jobs.json: {"style_dir": <the style folder, served as the bundle's public folder>,
//             "style": {zone, colors, radius_px, fonts: {regular, bold}},
//             "jobs": [{"card": <composition id>, "params": {...}, "frames": <n>,
//                       "align": "center" | "left" | "right" (where the card goes on the zone's top edge),
//                       "out": <path ending in .mov>}
//                      or a still: {"kind": "still", "card", "params", "frames", "frame": <the frame to draw>,
//                                   "align", "out": <path ending in .png>}]}
//
// The cards are bundled once, without the bundler's cache: runs may bundle at the same time (a still for the review
// page during a render, say), and the bundler aborts a run when another one writes that cache at the same moment.
// Then they are rendered one by one in one browser. Each clip is BT.709, limited range, like the
// reels it goes over. It is written to <out>.part.mov and renamed to <out> when it is complete; then one line of
// JSON is printed: {"ok": true, "card", "out", "frames"}. A still is the one frame as a 1080x1920 PNG with
// transparency, written to <its name>.part.png in the temporary folder and renamed to <out> when it is complete, so
// runs that draw the same still at once never take each other's part file (scene_render.py gives each run a
// temporary folder of its own, next to the stills); its line is {"ok": true, "card", "out", "frame"}.
// On an error one line {"ok": false, "error"} is printed and the exit code is 1. Temporary files go to the
// system's temporary folder (TMPDIR). No licence key is passed to renderMedia, so it reports no usage to anyone.
//
// Remotion looks for its browser from the working folder up to the first package.json, and downloads one there
// when it finds none. So this script works from scenes/ (relative paths in the arguments are taken from the folder
// it was started in), and it stops when scenes/node_modules/.remotion holds no browser instead of downloading one.

import { bundle } from "@remotion/bundler";
import { openBrowser, renderMedia, renderStill, selectComposition } from "@remotion/renderer";
import { existsSync } from "node:fs";
import { readFile, rename, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BROWSER_DIR = path.join(HERE, "node_modules", ".remotion", "chrome-headless-shell");

const partOf = (out) => `${out.replace(/\.mov$/, "")}.part.mov`;
const stillPartOf = (out) => path.join(os.tmpdir(), `${path.basename(out, ".png")}.part.png`);

async function main(jobsPath) {
  const started = process.cwd();
  const spec = JSON.parse(await readFile(path.resolve(started, jobsPath), "utf8"));
  const styleDir = path.resolve(started, spec.style_dir);
  process.chdir(HERE);
  if (!existsSync(BROWSER_DIR)) {
    throw new Error(`no browser in ${BROWSER_DIR}: run uv run pipeline setup`);
  }
  const serveUrl = await bundle({
    entryPoint: path.join(HERE, "src", "index.jsx"),
    publicDir: styleDir,
    rspack: true,
    enableCaching: false,
  });
  const browser = await openBrowser("chrome", { logLevel: "error" });
  try {
    for (const job of spec.jobs) {
      const out = path.resolve(started, job.out);
      const inputProps = { frames: job.frames, params: job.params, style: spec.style, align: job.align };
      const composition = await selectComposition({
        serveUrl,
        id: job.card,
        inputProps,
        puppeteerInstance: browser,
        logLevel: "error",
      });
      if (job.kind === "still") {
        const part = stillPartOf(out);
        try {
          await renderStill({
            composition,
            serveUrl,
            inputProps,
            frame: job.frame,
            imageFormat: "png",
            output: part,
            puppeteerInstance: browser,
            overwrite: true,
            logLevel: "error",
          });
        } catch (error) {
          await rm(part, { force: true });
          throw error;
        }
        await rename(part, out);
        console.log(JSON.stringify({ ok: true, card: job.card, out, frame: job.frame }));
        continue;
      }
      const part = partOf(out);
      try {
        await renderMedia({
          composition,
          serveUrl,
          inputProps,
          puppeteerInstance: browser,
          codec: "prores",
          proResProfile: "4444",
          imageFormat: "png",
          pixelFormat: "yuva444p10le",
          colorSpace: "bt709", // the matrix and range of the reels the clip goes over; the default is BT.601
          muted: true,
          outputLocation: part,
          overwrite: true,
          logLevel: "error",
        });
      } catch (error) {
        await rm(part, { force: true });
        throw error;
      }
      await rename(part, out);
      console.log(JSON.stringify({ ok: true, card: job.card, out, frames: composition.durationInFrames }));
    }
  } finally {
    await browser.close({ silent: true });
  }
}

main(process.argv[2]).catch((error) => {
  console.log(JSON.stringify({ ok: false, error: String(error?.message ?? error) }));
  process.exit(1);
});

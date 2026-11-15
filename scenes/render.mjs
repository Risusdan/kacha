// Renders animation cards to transparent ProRes 4444 files, for pipeline/scenes.py.
//
//   node scenes/render.mjs <jobs.json>
//
// jobs.json: {"style_dir": <the style folder, served as the bundle's public folder>,
//             "style": {zone, colors, radius_px, fonts: {regular, bold}},
//             "jobs": [{"card": <composition id>, "params": {...}, "frames": <n>, "out": <path ending in .mov>}]}
//
// The cards are bundled once, then rendered one by one in one browser. Each clip is BT.709, limited range, like the
// reels it goes over. It is written to <out>.part.mov and renamed to <out> when it is complete; then one line of
// JSON is printed: {"ok": true, "card", "out", "frames"}.
// On an error one line {"ok": false, "error"} is printed and the exit code is 1. Temporary files go to the
// system's temporary folder (TMPDIR). No licence key is passed to renderMedia, so it reports no usage to anyone.
//
// Remotion looks for its browser from the working folder up to the first package.json, and downloads one there
// when it finds none. So this script works from scenes/ (relative paths in the arguments are taken from the folder
// it was started in), and it stops when scenes/node_modules/.remotion holds no browser instead of downloading one.

import { bundle } from "@remotion/bundler";
import { openBrowser, renderMedia, selectComposition } from "@remotion/renderer";
import { existsSync } from "node:fs";
import { readFile, rename, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BROWSER_DIR = path.join(HERE, "node_modules", ".remotion", "chrome-headless-shell");

const partOf = (out) => `${out.replace(/\.mov$/, "")}.part.mov`;

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
  });
  const browser = await openBrowser("chrome", { logLevel: "error" });
  try {
    for (const job of spec.jobs) {
      const out = path.resolve(started, job.out);
      const inputProps = { frames: job.frames, params: job.params, style: spec.style };
      const composition = await selectComposition({
        serveUrl,
        id: job.card,
        inputProps,
        puppeteerInstance: browser,
        logLevel: "error",
      });
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

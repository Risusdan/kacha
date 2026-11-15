// Downloads the browser Remotion renders with into scenes/node_modules/.remotion, for pipeline setup. Remotion puts
// it next to the first package.json above the working folder, so this script works from scenes/.
//
//   node scenes/ensure-browser.mjs
//
// Prints one line of JSON: {"ok": true, ...what Remotion reports} or {"ok": false, "error"} (exit code 1).

import { ensureBrowser } from "@remotion/renderer";
import path from "node:path";
import { fileURLToPath } from "node:url";

process.chdir(path.dirname(fileURLToPath(import.meta.url)));
try {
  const status = await ensureBrowser({ logLevel: "error" });
  console.log(JSON.stringify({ ok: true, ...status }));
} catch (error) {
  console.log(JSON.stringify({ ok: false, error: String(error?.message ?? error) }));
  process.exit(1);
}

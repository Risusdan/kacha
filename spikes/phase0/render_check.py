"""Check alpha + composite every renderer output in SPIKE_OUT/render -> render/check.json (throwaway spike code).

For each {remotion,hyperframes}-{5s,60s}.{mov,webm}: decode frame 75 (2.5 s) as RGBA (WebM through
libvpx-vp9, which keeps alpha), require alpha 0 at (10,10) and 255 at the card centre (540,960),
and composite the frame over #3366CC into <name>-on-blue.png for a visual glyph check.
Timing comes from the matching <name>.time file written by /usr/bin/time -l.
"""
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from common import FFMPEG, FFPROBE, SPIKE_OUT

R = SPIKE_OUT / "render"
W, H, FRAME = 1080, 1920, 75


def decoder_args(path: Path) -> list[str]:
    return ["-c:v", "libvpx-vp9"] if path.suffix == ".webm" else []


def frame_rgba(path: Path) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", *decoder_args(path), "-i", str(path),
                          "-vf", f"select=eq(n\\,{FRAME})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 4)


def composite(path: Path, png: Path) -> None:
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x3366CC:s={W}x{H}:r=30",
                    *decoder_args(path), "-i", str(path),
                    "-filter_complex", f"[1:v]select=eq(n\\,{FRAME}),setpts=PTS-STARTPTS[fg];[0:v][fg]overlay=shortest=1:format=auto",
                    "-frames:v", "1", str(png)], check=True)


def stream_info(path: Path) -> dict:
    out = json.loads(subprocess.run([FFPROBE, "-v", "error", *decoder_args(path), "-show_streams", "-show_format",
                                     "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    return {"codec": v["codec_name"], "pix_fmt": v.get("pix_fmt"), "duration_s": round(float(out["format"]["duration"]), 3),
            "size_mb": round(int(out["format"]["size"]) / 1e6, 1)}


def timing(path: Path) -> tuple[float | None, float | None]:
    t = Path(str(path) + ".time")
    if not t.exists():
        return None, None
    text = t.read_text()
    wall = re.search(r"([\d.]+) real", text)
    peak = re.search(r"(\d+)\s+peak memory footprint", text)
    return (float(wall.group(1)) if wall else None, round(int(peak.group(1)) / 1048576, 1) if peak else None)


def main() -> None:
    previous = {}
    if (R / "check.json").exists():
        previous = {e["file"]: e for e in json.loads((R / "check.json").read_text())}
    rows = []
    for path in sorted(R.glob("*-[0-9]*s.*")):
        if path.suffix not in (".mov", ".webm"):
            continue
        rgba = frame_rgba(path)
        corner, centre = int(rgba[10, 10, 3]), int(rgba[960, 540, 3])
        png = R / f"{path.stem}-{path.suffix[1:]}-on-blue.png"
        composite(path, png)
        wall, peak = timing(path)
        info = stream_info(path)
        rows.append({
            "file": path.name, **info,
            "alpha_corner": corner, "alpha_centre": centre, "alpha_ok": corner == 0 and centre == 255,
            "cjk_ok": previous.get(path.name, {}).get("cjk_ok"),
            "wall_s": wall, "peak_mb": peak,
            "sec_per_output_sec": round(wall / info["duration_s"], 3) if wall else None,
            "composite_png": png.name,
        })
    (R / "check.json").write_text(json.dumps(rows, indent=2))
    for r in rows:
        print(r["file"], r["codec"], r["pix_fmt"], r["duration_s"], "s", r["size_mb"], "MB",
              "alpha", r["alpha_corner"], r["alpha_centre"], "ok" if r["alpha_ok"] else "FAIL",
              "wall", r["wall_s"], "s/out-s", r["sec_per_output_sec"])


if __name__ == "__main__":
    main()

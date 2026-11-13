"""Print basic facts about a video file as JSON (throwaway spike code)."""
import json
import subprocess
import sys
from fractions import Fraction

from common import FFPROBE


def probe(path: str) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    rotation = 0
    for sd in v.get("side_data_list", []):
        if sd.get("side_data_type") == "Display Matrix":
            rotation = int(round(float(sd.get("rotation", 0))))
    w, h = v["width"], v["height"]
    if abs(rotation) % 180 == 90:
        w, h = h, w
    r = Fraction(v["r_frame_rate"])
    avg = Fraction(v["avg_frame_rate"]) if v.get("avg_frame_rate", "0/0") != "0/0" else r
    transfer = v.get("color_transfer", "unknown")
    return {
        "display_w": w,
        "display_h": h,
        "rotation": rotation,
        "fps_avg": round(float(avg), 3),
        "fps_r": round(float(r), 3),
        "is_vfr": r != avg,
        "color_transfer": transfer,
        "is_hdr": transfer in ("arib-std-b67", "smpte2084"),
        "duration_s": round(float(info["format"]["duration"]), 3),
        "codec": v["codec_name"],
        "pix_fmt": v.get("pix_fmt"),
        "bit_rate_kbps": round(int(info["format"].get("bit_rate", 0)) / 1000),
    }


if __name__ == "__main__":
    print(json.dumps(probe(sys.argv[1]), indent=2))

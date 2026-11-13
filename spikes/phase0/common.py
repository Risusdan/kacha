"""Shared helpers for the phase 0 spike (throwaway code)."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import opencc


def _spike_out() -> Path:
    root = os.environ.get("REELS_STUDIO", "")
    if not root or not os.path.isabs(root):
        sys.exit("error: REELS_STUDIO must be set to an absolute path (the main checkout's studio/)")
    out = Path(root) / "projects" / "_spike"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _ffmpeg_prefix() -> str:
    return subprocess.run(["brew", "--prefix", "ffmpeg-full"], capture_output=True,
                          text=True, check=True).stdout.strip()


SPIKE_OUT: Path = _spike_out()
FFMPEG: str = _ffmpeg_prefix() + "/bin/ffmpeg"
FFPROBE: str = _ffmpeg_prefix() + "/bin/ffprobe"


def run_timed(cmd: list[str]) -> dict:
    """Run cmd under /usr/bin/time -l; return wall seconds, peak memory footprint (MB), return code."""
    with tempfile.NamedTemporaryFile("r", suffix=".time") as tf:
        rc = subprocess.run(["/usr/bin/time", "-l", "-o", tf.name, *cmd]).returncode
        report = Path(tf.name).read_text()
    wall = re.search(r"([\d.]+) real", report)
    peak = re.search(r"(\d+)\s+peak memory footprint", report)
    return {
        "wall_s": float(wall.group(1)) if wall else None,
        "peak_mb": round(int(peak.group(1)) / 1048576, 1) if peak else None,
        "returncode": rc,
    }


_S2TWP = opencc.OpenCC("s2twp")
_BRACKET_NOTES = re.compile(r"\[[^\]]*\]|【[^】]*】")
_PAREN_LINE = re.compile(r"^\s*\([^)]*\)\s*$", re.MULTILINE)
_MD_HEADING = re.compile(r"^\s*#+\s*", re.MULTILINE)


def normalize_text(s: str) -> str:
    """NFKC -> s2twp -> drop [..] / 【..】 / standalone (..) lines / heading marks -> drop punct+space -> lower."""
    s = unicodedata.normalize("NFKC", s)  # full-width parens become ASCII here
    s = _S2TWP.convert(s)
    s = _BRACKET_NOTES.sub("", s)
    s = _PAREN_LINE.sub("", s)
    s = _MD_HEADING.sub("", s)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] in "LNM")
    return s.lower()


_TOKEN = re.compile(r"[a-z]+|\S")


def tokenize(normalized: str) -> list[str]:
    """Units for error rates: one Latin word = one unit, every other character = one unit."""
    return _TOKEN.findall(normalized)


def is_cjk(ch: str) -> bool:
    return "CJK UNIFIED IDEOGRAPH" in unicodedata.name(ch, "")


def to_tokens(words: list[tuple[str, float, float | None]]) -> list[dict]:
    """Engine words -> normalized units with times; a multi-unit word shares its span linearly."""
    out = []
    for text, start, end in words:
        units = tokenize(normalize_text(text))
        if not units:
            continue
        if end is None:  # start-only engines: every unit gets the word start
            out += [{"t": u, "start": round(start, 3), "end": None} for u in units]
            continue
        step = (end - start) / len(units)
        out += [{"t": u, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3)}
                for i, u in enumerate(units)]
    return out


def run_worker(script: str, args: list[str], out_json: Path) -> dict:
    """Run `python <script> --worker <args>` under run_timed, then add wall_s/peak_mb to its JSON."""
    timing = run_timed([sys.executable, script, "--worker", *args])
    if timing["returncode"] != 0:
        sys.exit(f"error: worker failed with code {timing['returncode']}")
    result = json.loads(out_json.read_text())
    result.update(wall_s=timing["wall_s"], peak_mb=timing["peak_mb"])
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result

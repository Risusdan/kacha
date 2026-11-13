"""Style files: canvas, safe area, fonts and caption settings under styles/<name>/."""

import json
from pathlib import Path

from pipeline.errors import PipelineError
from pipeline.paths import repo_root


class UnknownStyle(PipelineError):
    """There is no style folder of that name."""


def styles_root() -> Path:
    return repo_root() / "styles"


def load_style(name: str = "default") -> dict:
    """The parsed styles/<name>/style.json, plus the key `dir` (a Path to the style folder). The caption line box
    (captions.line_height_px high, its bottom at captions.bottom_px) must lie inside the safe area."""
    folder = styles_root() / name
    if not name or Path(name).name != name or not (folder / "style.json").is_file():
        raise UnknownStyle(f"unknown style {name!r}")
    style = json.loads((folder / "style.json").read_text(encoding="utf-8"))
    captions, safe = style["captions"], style["safe_area"]
    bottom, height = captions["bottom_px"], captions["line_height_px"]
    if bottom - height < safe["y0"] or bottom > safe["y1"]:
        raise PipelineError(
            f"style {name}: the caption line box (bottom_px {bottom}, line_height_px {height}) "
            "is not inside the safe area"
        )
    style["dir"] = folder
    return style


def font_path(style: dict, weight: str) -> Path:
    """The static font file for `weight` ("regular" or "bold") of a loaded style."""
    if weight not in ("regular", "bold"):
        raise PipelineError(f"unknown font weight {weight!r}; use 'regular' or 'bold'")
    return style["dir"] / style["fonts"][weight]

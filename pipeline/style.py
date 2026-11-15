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
    (captions.line_height_px high, its bottom at captions.bottom_px) must lie inside the safe area. The animation
    cards' zone (scenes.zone) must be a rectangle inside the safe area that ends above the top of the most caption
    lines there can be (captions.max_lines boxes over captions.bottom_px)."""
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
    if "scenes" in style:
        _check_zone(name, style["scenes"]["zone"], safe, captions)
    style["dir"] = folder
    return style


def _check_zone(name: str, zone: dict, safe: dict, captions: dict) -> None:
    x0, x1, y0, y1 = zone["x0"], zone["x1"], zone["y0"], zone["y1"]
    if not (safe["x0"] <= x0 < x1 <= safe["x1"] and safe["y0"] <= y0 < y1 <= safe["y1"]):
        raise PipelineError(
            f"style {name}: the animation zone (x0 {x0}, x1 {x1}, y0 {y0}, y1 {y1}) is not inside the safe area"
        )
    lines = captions["max_lines"]
    top = captions["bottom_px"] - lines * captions["line_height_px"]
    if y1 > top:
        raise PipelineError(
            f"style {name}: the animation zone (y1 {y1}) reaches the captions, whose {lines} lines start at {top}"
        )


def font_path(style: dict, weight: str) -> Path:
    """The static font file for `weight` ("regular" or "bold") of a loaded style."""
    if weight not in ("regular", "bold"):
        raise PipelineError(f"unknown font weight {weight!r}; use 'regular' or 'bold'")
    return style["dir"] / style["fonts"][weight]

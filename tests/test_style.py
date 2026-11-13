"""The style file: the caption settings are read, and a caption box that leaves the safe area is refused."""

import json

import pytest

from pipeline.errors import PipelineError
from pipeline.style import load_style, styles_root


def custom_style(tmp_path, monkeypatch, name="custom", **changes) -> str:
    """The default style.json with `changes` merged into its captions (and into its safe area, as `safe_area`),
    copied into a styles folder under tmp_path that load_style reads from. Returns the name of the style."""
    document = json.loads((styles_root() / "default" / "style.json").read_text(encoding="utf-8"))
    document["safe_area"].update(changes.pop("safe_area", {}))
    document["captions"].update(changes)
    folder = tmp_path / "styles" / name
    folder.mkdir(parents=True)
    (folder / "style.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr("pipeline.style.styles_root", lambda: tmp_path / "styles")
    return name


def refusal(name: str, bottom: int, height: int) -> str:
    return (
        f"style {name}: the caption line box (bottom_px {bottom}, line_height_px {height}) "
        "is not inside the safe area"
    )


def test_the_default_style_puts_the_caption_box_on_the_safe_area_bottom():
    captions, safe = load_style()["captions"], load_style()["safe_area"]
    assert captions["bottom_px"] == safe["y1"] == 1248


def test_the_default_style_has_no_face_settings():
    assert not {"default_top_px", "face_margin_px"} & set(load_style()["captions"])


def test_a_style_whose_caption_box_leaves_the_safe_area_is_refused(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, bottom_px=1300)
    with pytest.raises(PipelineError) as refused:
        load_style(name)
    assert refused.value.message == refusal(name, 1300, 90)


def test_a_style_whose_caption_box_is_one_pixel_below_the_safe_area_is_refused(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, bottom_px=1249)
    with pytest.raises(PipelineError, match="not inside the safe area"):
        load_style(name)


def test_a_style_whose_caption_box_starts_above_the_safe_area_is_refused(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, bottom_px=358)  # the box would start at 268, the safe area at 269
    with pytest.raises(PipelineError) as refused:
        load_style(name)
    assert refused.value.message == refusal(name, 358, 90)


def test_a_taller_line_box_can_push_the_top_out_of_the_safe_area(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, line_height_px=1000)  # 1248 - 1000 = 248, above 269
    with pytest.raises(PipelineError) as refused:
        load_style(name)
    assert refused.value.message == refusal(name, 1248, 1000)


def test_a_caption_box_that_fits_the_safe_area_exactly_is_accepted(tmp_path, monkeypatch):
    top = custom_style(tmp_path, monkeypatch, name="top", bottom_px=359)  # 359 - 90 = 269
    bottom = custom_style(tmp_path, monkeypatch, name="bottom", bottom_px=1248)
    assert load_style(top)["captions"]["bottom_px"] == 359
    assert load_style(bottom)["captions"]["bottom_px"] == 1248


def test_the_safe_area_the_box_is_checked_against_is_the_style_s_own(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, bottom_px=1100, safe_area={"y1": 1000})
    with pytest.raises(PipelineError) as refused:
        load_style(name)
    assert refused.value.message == refusal(name, 1100, 90)

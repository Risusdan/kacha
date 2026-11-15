"""The style file: the caption settings are read, and a caption box that leaves the safe area is refused."""

import json

import pytest

from pipeline.errors import PipelineError
from pipeline.style import load_style, styles_root


def custom_style(tmp_path, monkeypatch, name="custom", without_scenes=False, **changes) -> str:
    """The default style.json with `changes` merged into its captions (and into its safe area, as `safe_area`, and
    its animation zone, as `zone`), copied into a styles folder under tmp_path that load_style reads from; without
    the animation cards' tokens if `without_scenes`. Returns the name of the style."""
    document = json.loads((styles_root() / "default" / "style.json").read_text(encoding="utf-8"))
    document["safe_area"].update(changes.pop("safe_area", {}))
    document["scenes"]["zone"].update(changes.pop("zone", {}))
    if without_scenes:
        del document["scenes"]
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
    # 359 - 90 = 269. Captions this high leave no room for an animation zone above them.
    top = custom_style(tmp_path, monkeypatch, name="top", without_scenes=True, bottom_px=359)
    bottom = custom_style(tmp_path, monkeypatch, name="bottom", bottom_px=1248)
    assert load_style(top)["captions"]["bottom_px"] == 359
    assert load_style(bottom)["captions"]["bottom_px"] == 1248


def test_the_safe_area_the_box_is_checked_against_is_the_style_s_own(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, bottom_px=1100, safe_area={"y1": 1000})
    with pytest.raises(PipelineError) as refused:
        load_style(name)
    assert refused.value.message == refusal(name, 1100, 90)


# ---- the animation cards' tokens ----------------------------------------------------------------------------------


def zone_refusal(name: str, x0: int, x1: int, y0: int, y1: int) -> str:
    return f"style {name}: the animation zone (x0 {x0}, x1 {x1}, y0 {y0}, y1 {y1}) is not inside the safe area"


def test_the_default_style_has_the_tokens_of_the_animation_cards():
    scenes = load_style()["scenes"]
    assert scenes["zone"] == {"x0": 65, "x1": 1015, "y0": 269, "y1": 1040}
    assert scenes["colors"] == {"card": "#FFFFFF", "text": "#111111", "accent": "#FFD400", "muted": "#5C5C5C"}
    assert scenes["radius_px"] == 32


@pytest.mark.parametrize("change", [{"x0": 64}, {"x1": 1016}, {"y0": 268}, {"x0": 600, "x1": 600},
                                    {"y0": 900, "y1": 800}])
def test_a_zone_outside_the_safe_area_or_empty_is_refused(tmp_path, monkeypatch, change):
    name = custom_style(tmp_path, monkeypatch, zone=change)
    zone = {"x0": 65, "x1": 1015, "y0": 269, "y1": 1040, **change}
    with pytest.raises(PipelineError) as refused:
        load_style(name)
    assert refused.value.message == zone_refusal(name, **zone)


def test_a_zone_reaching_the_top_of_two_caption_lines_is_refused(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, zone={"y1": 1069})  # two lines of 90 px over 1248 start at 1068
    with pytest.raises(PipelineError) as refused:
        load_style(name)
    assert refused.value.message == (
        f"style {name}: the animation zone (y1 1069) reaches the captions, whose 2 lines start at 1068"
    )


def test_a_zone_ending_where_two_caption_lines_start_is_accepted(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, zone={"y1": 1068})
    assert load_style(name)["scenes"]["zone"]["y1"] == 1068


def test_the_caption_band_the_zone_is_checked_against_is_the_style_s_own(tmp_path, monkeypatch):
    name = custom_style(tmp_path, monkeypatch, line_height_px=110)  # 1248 - 2 x 110 = 1028, above y1 1040
    with pytest.raises(PipelineError, match=r"the animation zone \(y1 1040\) reaches the captions, whose 2 lines "
                                            r"start at 1028"):
        load_style(name)

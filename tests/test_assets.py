import hashlib
import http.client
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fontTools.ttLib import TTFont
from helpers import caption_dictionary, meijia_transcribed_project, require_assets, static_font, yunet_model

from pipeline import assets
from pipeline.assets import doctor, fetch, hf_model_present, setup_fonts
from pipeline.cli import main, registered_commands
from pipeline.errors import PipelineError
from pipeline.style import load_style
from pipeline.version import kacha_version


def test_fetch_rejects_bad_checksum(tmp_path):
    with pytest.raises(PipelineError, match="sha256"):
        fetch("https://example.invalid/f", "0" * 64, tmp_path / "f", download=lambda u: b"wrong")
    assert not (tmp_path / "f").exists()


def test_fetch_writes_verified_download(tmp_path):
    digest = hashlib.sha256(b"payload").hexdigest()
    dest = fetch("https://example.invalid/f", digest, tmp_path / "sub" / "f", download=lambda u: b"payload")
    assert dest == tmp_path / "sub" / "f" and dest.read_bytes() == b"payload"
    assert [p.name for p in dest.parent.iterdir()] == ["f"]


def test_fetch_skips_download_when_file_already_matches(tmp_path):
    digest = hashlib.sha256(b"payload").hexdigest()
    dest = tmp_path / "f"
    dest.write_bytes(b"payload")

    def no_network(url):
        raise AssertionError("downloaded although the file is already correct")

    assert fetch("https://example.invalid/f", digest, dest, download=no_network) == dest


def test_fetch_replaces_a_corrupt_file(tmp_path):
    digest = hashlib.sha256(b"payload").hexdigest()
    dest = tmp_path / "f"
    dest.write_bytes(b"half a download")
    fetch("https://example.invalid/f", digest, dest, download=lambda u: b"payload")
    assert dest.read_bytes() == b"payload"


def test_download_cut_short_is_a_pipeline_error(tmp_path, monkeypatch):
    class CutShort:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda url, timeout: CutShort())
    url = "https://example.invalid/f"
    with pytest.raises(PipelineError) as error:
        fetch(url, "0" * 64, tmp_path / "f")
    assert url in str(error.value)
    assert not (tmp_path / "f").exists()


# ---- setup_fonts, with the font tools and the download stubbed (no real font, no network) ---------


class _FakeFont:
    """Stands in for a TTFont: it can be saved to a file and closed."""

    def __init__(self, opened: list):
        self.closed = False
        opened.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def save(self, path):
        path.write_bytes(b"static font")


@pytest.fixture
def font_stubs(monkeypatch):
    """The download path of every fetch call, the fake fonts opened, and no way to make a temporary folder."""
    fetched, opened = [], []

    def fake_fetch(url, sha256, dest, download=None):
        fetched.append(dest)
        dest.write_bytes(b"variable font")
        return dest

    def no_temp_folder(*args, **kwargs):
        raise AssertionError("setup_fonts made a temporary folder")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", no_temp_folder)
    monkeypatch.setattr(tempfile, "mkdtemp", no_temp_folder)
    monkeypatch.setattr(assets, "fetch", fake_fetch)
    monkeypatch.setattr(assets, "TTFont", lambda *args, **kwargs: _FakeFont(opened))
    monkeypatch.setattr(assets.instancer, "instantiateVariableFont", lambda *args, **kwargs: _FakeFont(opened))
    monkeypatch.setattr(assets, "_static_font_ok", lambda path, weight: path.is_file())
    return SimpleNamespace(fetched=fetched, opened=opened)


def test_setup_fonts_leaves_only_the_two_static_fonts(tmp_path, font_stubs):
    dest = tmp_path / "fonts"
    before = set(tmp_path.rglob("*"))
    paths = setup_fonts(dest)
    assert [path.name for path in paths] == ["NotoSansTC-Regular.ttf", "NotoSansTC-Bold.ttf"]
    assert sorted(entry.name for entry in dest.iterdir()) == ["NotoSansTC-Bold.ttf", "NotoSansTC-Regular.ttf"]
    assert set(tmp_path.rglob("*")) - before == {dest, *paths}  # no other file or folder, anywhere under tmp_path


def test_setup_fonts_names_come_from_the_style(tmp_path, font_stubs, monkeypatch):
    style = {"dir": tmp_path / "style", "fonts": {"regular": "fonts/Other-Regular.ttf", "bold": "fonts/Other-Bold.ttf"}}
    monkeypatch.setattr(assets, "load_style", lambda *args: style)
    weights = []

    def instance(font, axes, **kwargs):
        weights.append(axes["wght"])
        return _FakeFont(font_stubs.opened)

    monkeypatch.setattr(assets.instancer, "instantiateVariableFont", instance)
    dest = tmp_path / "fonts"
    paths = setup_fonts(dest)
    assert [path.name for path in paths] == ["Other-Regular.ttf", "Other-Bold.ttf"]
    assert sorted(entry.name for entry in dest.iterdir()) == ["Other-Bold.ttf", "Other-Regular.ttf"]
    assert weights == [400, 700]


def test_setup_fonts_downloads_inside_dest_and_closes_every_font(tmp_path, font_stubs):
    dest = tmp_path / "fonts"
    setup_fonts(dest)
    assert [path.parent for path in font_stubs.fetched] == [dest]
    assert font_stubs.opened and all(font.closed for font in font_stubs.opened)


def test_setup_fonts_removes_the_download_when_instancing_fails(tmp_path, font_stubs, monkeypatch):
    def fails(*args, **kwargs):
        raise RuntimeError("cannot instance")

    monkeypatch.setattr(assets.instancer, "instantiateVariableFont", fails)
    dest = tmp_path / "fonts"
    with pytest.raises(RuntimeError, match="cannot instance"):
        setup_fonts(dest)
    assert font_stubs.fetched and list(dest.iterdir()) == []
    assert all(font.closed for font in font_stubs.opened)


def test_static_fonts_have_family_and_weight():  # needs `uv run pipeline setup` to have run
    for weight, cls in (("regular", 400), ("bold", 700)):
        f = TTFont(static_font(weight))
        assert f["name"].getDebugName(1) == "Noto Sans TC" and f["OS/2"].usWeightClass == cls


def test_yunet_model_is_pinned_and_present():  # needs `uv run pipeline setup` to have run
    model = yunet_model()
    assert hashlib.sha256(model.read_bytes()).hexdigest() == assets.YUNET_SHA256


def test_caption_dictionary_is_pinned_and_present():  # needs `uv run pipeline setup` to have run
    path = caption_dictionary()
    assert path == assets.dictionary_path() == assets.models_dir() / assets.DICT_FILENAME
    assert hashlib.sha256(path.read_bytes()).hexdigest() == assets.DICT_SHA256
    assert assets.dictionary_ok() is True
    assert "237dc6625e5c65d7a2714ffdfa5238dba5cae7d4" in assets.DICT_URL


def test_dictionary_ok_needs_the_file_and_its_sha256(tmp_path, monkeypatch):
    path = tmp_path / "jieba-dict.txt.big"
    monkeypatch.setattr(assets, "dictionary_path", lambda: path)
    assert assets.dictionary_ok() is False  # no file
    path.write_bytes(b"cut short")
    assert assets.dictionary_ok() is False  # a file with another hash
    monkeypatch.setattr(assets, "DICT_SHA256", hashlib.sha256(b"cut short").hexdigest())
    assert assets.dictionary_ok() is True


def test_setup_dictionary_checks_the_sha256(tmp_path):
    models = tmp_path / "models"
    with pytest.raises(PipelineError, match="sha256"):
        assets.setup_dictionary(models, download=lambda url: b"not the dictionary")
    assert not models.exists()  # nothing was written, not even the folder


def test_setup_dictionary_puts_the_pinned_download_in_the_folder(tmp_path, monkeypatch):
    payload = "上傳 3 v\n".encode()
    monkeypatch.setattr(assets, "DICT_SHA256", hashlib.sha256(payload).hexdigest())
    asked = []
    path = assets.setup_dictionary(tmp_path / "models", download=lambda url: asked.append(url) or payload)
    assert path == tmp_path / "models" / "jieba-dict.txt.big" and path.read_bytes() == payload
    assert asked == [assets.DICT_URL]
    assets.setup_dictionary(tmp_path / "models", download=lambda url: pytest.fail("downloaded a file that is there"))


@pytest.fixture
def no_fonts(tmp_path, monkeypatch):
    """The default style with no fonts/ folder: what a machine where `pipeline setup` has not run looks like.
    The real styles/ folder is not touched."""
    folder = tmp_path / "no_fonts" / "default"
    folder.mkdir(parents=True)
    (folder / "style.json").write_text((load_style()["dir"] / "style.json").read_text(encoding="utf-8"),
                                       encoding="utf-8")
    monkeypatch.setattr("pipeline.style.styles_root", lambda: folder.parent)


def test_font_helper_says_to_run_pipeline_setup_when_a_font_is_missing(no_fonts):
    for weight in ("regular", "bold"):
        with pytest.raises(pytest.fail.Exception, match="pipeline setup"):
            static_font(weight)


def test_flow_helpers_need_the_fonts_before_any_work(tmp_path, no_fonts):
    with pytest.raises(pytest.fail.Exception, match="pipeline setup"):
        meijia_transcribed_project(tmp_path)


def test_style_values_match_spec():
    s = load_style()
    assert s["safe_area"] == {"x0": 65, "x1": 1015, "y0": 269, "y1": 1248}
    assert s["captions"]["max_chars_per_line"] == 16 and s["captions"]["max_lines"] == 2


def test_load_style_unknown_name_raises(tmp_path):
    for name in ("nope", "../default", ""):
        with pytest.raises(PipelineError, match="style"):
            load_style(name)


HF_MODELS = ("models--Qwen--Qwen3-ASR-1.7B", "models--Qwen--Qwen3-ASR-0.6B", "models--Qwen--Qwen3-ForcedAligner-0.6B")


def fake_hf_model(hub: Path, folder_name: str) -> Path:
    """A finished download in the fake cache `hub`: a snapshot with a file in it, and a finished blob."""
    folder = hub / folder_name
    (folder / "blobs").mkdir(parents=True)
    (folder / "blobs" / "0a1b2c").write_bytes(b"weights")
    revision = folder / "snapshots" / "5eb144"
    revision.mkdir(parents=True)
    (revision / "config.json").write_text("{}", encoding="utf-8")
    return folder


def test_hf_model_missing_when_folder_empty(tmp_path):
    (tmp_path / HF_MODELS[0]).mkdir()
    assert hf_model_present(tmp_path, "Qwen/Qwen3-ASR-1.7B") is False
    assert hf_model_present(tmp_path / "no_hub_at_all", "Qwen/Qwen3-ASR-1.7B") is False


def test_hf_model_missing_when_snapshots_empty(tmp_path):
    folder = tmp_path / HF_MODELS[0]
    (folder / "snapshots").mkdir(parents=True)
    assert hf_model_present(tmp_path, "Qwen/Qwen3-ASR-1.7B") is False
    (folder / "snapshots" / "5eb144").mkdir()  # a revision folder with no file in it
    assert hf_model_present(tmp_path, "Qwen/Qwen3-ASR-1.7B") is False


def test_hf_model_missing_while_a_blob_is_incomplete(tmp_path):
    folder = fake_hf_model(tmp_path, HF_MODELS[0])
    assert hf_model_present(tmp_path, "Qwen/Qwen3-ASR-1.7B") is True
    (folder / "blobs" / "3d4e5f.incomplete").write_bytes(b"half")
    assert hf_model_present(tmp_path, "Qwen/Qwen3-ASR-1.7B") is False


def test_hf_model_present_when_complete(tmp_path):
    fake_hf_model(tmp_path, HF_MODELS[0])
    assert hf_model_present(tmp_path, "Qwen/Qwen3-ASR-1.7B") is True
    assert hf_model_present(tmp_path, "Qwen/Qwen3-ASR-0.6B") is False  # a different model is not this one


def remotion_present(monkeypatch):
    """Report node, the Remotion modules and their browser as there, so a baseline needs only this module's items."""
    items = {"node": "/opt/node/bin/node", "remotion": True, "remotion_browser": True}
    monkeypatch.setattr("pipeline.scene_render.doctor_items", lambda: (items, []))


def test_doctor_asr_models_follow_hf_home(tmp_path, monkeypatch):
    hf = tmp_path / "hf"
    fake_hf_model(hf / "hub", HF_MODELS[0])
    (hf / "hub" / HF_MODELS[1]).mkdir()  # a folder with nothing in it is not a model
    monkeypatch.setenv("HF_HOME", str(hf))
    report = doctor()
    assert report["asr_models"] == {"1.7b": True, "0.6b": False, "aligner": False}
    assert report["ok"] is False


def test_doctor_missing_aligner_makes_ok_false(tmp_path, monkeypatch):
    require_assets()  # the baseline needs the real fonts and face model
    monkeypatch.setattr(assets, "_free_gb", lambda: 100.0)
    remotion_present(monkeypatch)
    with_aligner, without_aligner = tmp_path / "with_aligner", tmp_path / "without_aligner"
    for hf, names in ((with_aligner, HF_MODELS), (without_aligner, HF_MODELS[:2])):
        for name in names:
            fake_hf_model(hf / "hub", name)
    monkeypatch.setenv("HF_HOME", str(with_aligner))
    baseline = doctor()
    assert baseline["asr_models"] == {"1.7b": True, "0.6b": True, "aligner": True}
    assert baseline["ok"] is True, baseline  # everything is there, so the next report differs by the aligner only
    monkeypatch.setenv("HF_HOME", str(without_aligner))
    report = doctor()
    assert report["ok"] is False
    assert report["asr_models"] == {**baseline["asr_models"], "aligner": False}
    assert {k: v for k, v in report.items() if k not in ("ok", "asr_models")} == {
        k: v for k, v in baseline.items() if k not in ("ok", "asr_models")
    }


def test_doctor_reports_the_version():
    assert doctor()["version"] == kacha_version()


def test_doctor_without_ffmpeg_reports_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("REELS_FFMPEG_DIR", str(tmp_path))
    report = doctor()
    assert report["ffmpeg"] is None and not report["libass"] and not report["h264_videotoolbox"]
    assert report["ok"] is False


def test_doctor_warns_below_15_gb_free(monkeypatch):
    usage = SimpleNamespace(total=100 * 2**30, used=95 * 2**30, free=5 * 2**30)
    monkeypatch.setattr(assets.shutil, "disk_usage", lambda path: usage)
    report = doctor()
    assert report["free_gb"] == 5.0 and any("15 GB" in w for w in report["warnings"])


def test_doctor_uses_nearest_existing_parent_of_studio(tmp_path, monkeypatch):
    monkeypatch.setenv("REELS_STUDIO", str(tmp_path / "not" / "yet" / "there"))
    assert isinstance(doctor()["free_gb"], float)


def test_doctor_reports_every_item_the_cli_documents():
    report = doctor()
    assert {"ok", "ffmpeg", "libass", "h264_videotoolbox", "hevc_videotoolbox", "fonts", "yunet",
            "caption_dictionary", "asr_cli", "asr_models", "node", "remotion", "remotion_browser", "free_gb",
            "warnings"} <= set(report)


def test_doctor_without_the_caption_dictionary_is_not_ok(tmp_path, monkeypatch):
    require_assets()  # the baseline needs the real fonts, face model and dictionary
    monkeypatch.setattr(assets, "_free_gb", lambda: 100.0)
    remotion_present(monkeypatch)
    for name in HF_MODELS:
        fake_hf_model(tmp_path / "hf" / "hub", name)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    baseline = doctor()
    assert baseline["caption_dictionary"] is True
    assert baseline["ok"] is True, baseline  # everything is there, so the next report differs by the dictionary only
    monkeypatch.setattr(assets, "dictionary_path", lambda: tmp_path / "absent.txt.big")
    report = doctor()
    assert report["caption_dictionary"] is False and report["ok"] is False
    assert report["warnings"] == ["fonts, face model or caption dictionary missing: run `uv run pipeline setup`"]
    assert {k: v for k, v in report.items() if k not in ("ok", "caption_dictionary", "warnings")} == {
        k: v for k, v in baseline.items() if k not in ("ok", "caption_dictionary", "warnings")
    }


def test_doctor_reports_missing_face_model(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "yunet_path", lambda: tmp_path / "absent.onnx")
    report = doctor()
    assert report["yunet"] is False and report["ok"] is False
    assert any("pipeline setup" in w for w in report["warnings"])


def test_cli_registers_setup_and_doctor(capsys):
    assert {"setup", "doctor"} <= set(registered_commands())
    main(["doctor"])
    assert "ffmpeg" in json.loads(capsys.readouterr().out)

"""Downloaded assets (static fonts, the YuNet face model, the caption dictionary) and the environment check
behind `doctor` (its Node.js and Remotion items come from scene_render.doctor_items)."""

import hashlib
import http.client
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from pipeline.errors import PipelineError
from pipeline.media import ffmpeg_bin
from pipeline.paths import repo_root, studio_root
from pipeline.style import font_path, load_style
from pipeline.version import kacha_version

# Noto Sans TC variable font, SIL Open Font License 1.1 (google/fonts, ofl/notosanstc).
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf"
FONT_SHA256 = "864727d210d54f2537bbe23b3a839436c3992af72de9322af5270897246bd44f"
FONT_FAMILY = "Noto Sans TC"
VARIABLE_DOWNLOAD = "NotoSansTC[wght].ttf.download"  # the variable font while the static ones are cut; not kept
STYLE_WEIGHTS = {"regular": 400, "bold": 700}  # the font keys of style.json -> wght (their file names are the style's)

# YuNet face detector (2023mar), MIT licence (opencv_zoo, models/face_detection_yunet/LICENSE).
# The .onnx is stored with Git LFS there, so the URL is the LFS media URL, pinned to the commit
# that last changed the file.
YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
YUNET_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    "f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/" + YUNET_FILENAME
)
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

# jieba's Traditional Chinese dictionary, which caption line breaking reads. MIT licence (the same
# repository as jieba). It is not in the PyPI package, so the URL is pinned to the commit that last
# changed the file.
DICT_FILENAME = "jieba-dict.txt.big"
DICT_URL = (
    "https://raw.githubusercontent.com/fxsjy/jieba/237dc6625e5c65d7a2714ffdfa5238dba5cae7d4/extra_dict/dict.txt.big"
)
DICT_SHA256 = "b16011275c42955ccd81fc1adecc93a59dbb7926af69d93fc95d4943d40f6aad"

MIN_FREE_GB = 15


def _urlopen(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.read()
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        raise PipelineError(f"download failed for {url}: {error}") from error


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, sha256: str, dest: Path, download: Callable[[str], bytes] = _urlopen) -> Path:
    """Put the file at `url` on `dest`, checked against `sha256`.

    A `dest` that already has that hash is left alone (nothing is downloaded). A download whose
    hash differs raises PipelineError and writes nothing.
    """
    if dest.is_file() and _file_sha256(dest) == sha256:
        return dest
    data = download(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise PipelineError(f"sha256 mismatch for {url}: expected {sha256}, got {actual}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    try:
        partial.write_bytes(data)
        os.replace(partial, dest)
    finally:
        partial.unlink(missing_ok=True)
    return dest


def _static_font_ok(path: Path, weight: int) -> bool:
    try:
        with TTFont(path, lazy=True) as font:
            return font["name"].getDebugName(1) == FONT_FAMILY and font["OS/2"].usWeightClass == weight
    except Exception:  # a file that cannot be parsed as a font is not a usable font
        return False


def _font_targets(dest: Path) -> list[tuple[Path, int]]:
    """(file in `dest`, wght) for each font of the default style: the file names are the ones in its style.json."""
    style = load_style()
    return [(dest / font_path(style, key).name, wght) for key, wght in STYLE_WEIGHTS.items()]


def setup_fonts(dest: Path, download: Callable[[str], bytes] = _urlopen) -> list[Path]:
    """Cut static Regular (400) and Bold (700) fonts from the variable font into `dest`.

    The files are the ones the default style's style.json names for `regular` and `bold`.

    libass picks fonts by family name and silently falls back to a system font when it is given the
    variable file, so the pipeline renders with static instances. Fonts already present and valid
    are kept without downloading anything. The variable font is downloaded to a named file in `dest`,
    which is removed when the static fonts are cut or when that fails; no temporary folder is made.
    """
    targets = _font_targets(dest)
    if all(_static_font_ok(path, weight) for path, weight in targets):
        return [path for path, _ in targets]
    dest.mkdir(parents=True, exist_ok=True)
    variable_path = dest / VARIABLE_DOWNLOAD
    try:
        variable = fetch(FONT_URL, FONT_SHA256, variable_path, download)
        for path, weight in targets:
            with TTFont(variable) as master:
                font = instancer.instantiateVariableFont(master, {"wght": weight}, updateFontNames=True)
                partial = path.with_name(path.name + ".part")
                try:
                    font.save(partial)
                    os.replace(partial, path)
                finally:
                    font.close()
                    partial.unlink(missing_ok=True)
            if not _static_font_ok(path, weight):
                raise PipelineError(f"{path.name} does not report family {FONT_FAMILY!r} with weight {weight}")
    finally:
        variable_path.unlink(missing_ok=True)
    return [path for path, _ in targets]


def setup_models(dest: Path, download: Callable[[str], bytes] = _urlopen) -> Path:
    """Download the YuNet model into `dest` (skipped when it is already there and correct)."""
    return fetch(YUNET_URL, YUNET_SHA256, dest / YUNET_FILENAME, download)


def setup_dictionary(dest: Path, download: Callable[[str], bytes] = _urlopen) -> Path:
    """Download the caption dictionary into `dest` (skipped when it is already there and correct)."""
    return fetch(DICT_URL, DICT_SHA256, dest / DICT_FILENAME, download)


def models_dir() -> Path:
    return repo_root() / "models"


def yunet_path() -> Path:
    return models_dir() / YUNET_FILENAME


def yunet_ok() -> bool:
    """Whether the face model is in place: the file exists and its sha256 is YUNET_SHA256."""
    path = yunet_path()
    return path.is_file() and _file_sha256(path) == YUNET_SHA256


def dictionary_path() -> Path:
    return models_dir() / DICT_FILENAME


def dictionary_ok() -> bool:
    """Whether the caption dictionary is in place: the file exists and its sha256 is DICT_SHA256."""
    path = dictionary_path()
    return path.is_file() and _file_sha256(path) == DICT_SHA256


def fonts_dir(style_name: str = "default") -> Path:
    return load_style(style_name)["dir"] / "fonts"


def _ffmpeg_text(*args: str) -> str:
    done = subprocess.run([ffmpeg_bin(), "-hide_banner", *args], capture_output=True, text=True, timeout=60)
    return done.stdout


def _hf_hub_dir() -> Path:
    hf_home = os.environ.get("HF_HOME")
    return (Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface") / "hub"


def hf_model_present(hub: Path, repo_id: str) -> bool:
    """Whether the Hugging Face cache folder `hub` holds a finished download of `repo_id` ("org/name").

    A download counts when some folder under models--org--name/snapshots/ has a file in it and
    models--org--name/blobs/ has no file ending in `.incomplete` (a download that was cut short).
    """
    folder = hub / ("models--" + repo_id.replace("/", "--"))
    snapshots, blobs = folder / "snapshots", folder / "blobs"
    has_files = snapshots.is_dir() and any(
        revision.is_dir() and any(entry.is_file() for entry in revision.rglob("*")) for revision in snapshots.iterdir()
    )
    cut_short = blobs.is_dir() and any(blob.name.endswith(".incomplete") for blob in blobs.iterdir())
    return has_files and not cut_short


def _free_gb() -> float:
    folder = studio_root()
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return round(shutil.disk_usage(folder).free / 2**30, 1)


def doctor() -> dict:
    """Check the machine against what the pipeline needs. `ok` is false when a required item is missing."""
    warnings: list[str] = []
    try:
        ffmpeg = ffmpeg_bin()
        encoders = _ffmpeg_text("-encoders")
        filters = _ffmpeg_text("-filters")
    except (PipelineError, OSError, subprocess.SubprocessError) as error:
        ffmpeg, encoders, filters = None, "", ""
        warnings.append(str(error))
    libass = re.search(r"^\s*\S+\s+ass\s", filters, re.MULTILINE) is not None
    h264 = re.search(r"\bh264_videotoolbox\b", encoders) is not None
    hevc = re.search(r"\bhevc_videotoolbox\b", encoders) is not None

    style = load_style()
    fonts = all(_static_font_ok(font_path(style, key), wght) for key, wght in STYLE_WEIGHTS.items())
    yunet = yunet_ok()
    caption_dictionary = dictionary_ok()
    if not (fonts and yunet and caption_dictionary):
        warnings.append("fonts, face model or caption dictionary missing: run `uv run pipeline setup`")

    asr_cli = (Path(sys.executable).parent / "mlx-qwen3-asr").exists()
    hub = _hf_hub_dir()
    asr_models = {
        "1.7b": hf_model_present(hub, "Qwen/Qwen3-ASR-1.7B"),
        "0.6b": hf_model_present(hub, "Qwen/Qwen3-ASR-0.6B"),
        "aligner": hf_model_present(hub, "Qwen/Qwen3-ForcedAligner-0.6B"),  # word timestamps need it
    }

    from pipeline import scene_render  # its imports lead back to this module

    remotion, remotion_warnings = scene_render.doctor_items()
    warnings.extend(remotion_warnings)

    free_gb = _free_gb()
    if free_gb < MIN_FREE_GB:
        warnings.append(f"only {free_gb} GB free; keep at least {MIN_FREE_GB} GB free before downloads or renders")

    required = [ffmpeg is not None, libass, h264, hevc, fonts, yunet, caption_dictionary, asr_cli, *asr_models.values(),
                remotion["node"] is not None, remotion["remotion"], remotion["remotion_browser"]]
    return {
        "ok": all(required),
        "version": kacha_version(),
        "ffmpeg": ffmpeg,
        "libass": libass,
        "h264_videotoolbox": h264,
        "hevc_videotoolbox": hevc,
        "fonts": fonts,
        "yunet": yunet,
        "caption_dictionary": caption_dictionary,
        "asr_cli": asr_cli,
        "asr_models": asr_models,
        **remotion,
        "free_gb": free_gb,
        "warnings": warnings,
    }

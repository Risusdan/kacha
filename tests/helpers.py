"""Small test-only helpers shared across test modules; later tasks add to this file."""

import json
import math
import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from pipeline.align import build_transcript
from pipeline.asr import MODELS, AsrResult, AsrToken, load_asr
from pipeline.assets import dictionary_path, yunet_path
from pipeline.audio import HOP_S, Envelope
from pipeline.cli import main
from pipeline.edit import Segment, build_segments, require_valid, validate_project
from pipeline.media import ffmpeg_bin, run_ffmpeg
from pipeline.paths import studio_root
from pipeline.prep import prep
from pipeline.project import (
    Project,
    _edit_files,
    approve_gate,
    create_project,
    file_sha256,
    load_project,
    load_state,
    save_state,
)
from pipeline.script import parse_script
from pipeline.storyboard import build_storyboard
from pipeline.style import font_path, load_style
from pipeline.textnorm import Key, split_units, unit_alts, unit_key
from pipeline.trim import FPS, build_trims

MEIJIA = Path(__file__).resolve().parent / "fixtures" / "meijia"


def project_with(clip: Path, script: Path | None = None) -> Project:
    """A project made from `clip`: a copy of it plus a script go into a fresh folder under the temp
    studio's inbox/, then create_project reads them. The script is a copy of `script`, or one short
    line when none is given. Safe to call more than once per test."""
    token = uuid.uuid4().hex[:12]
    folder = studio_root() / "inbox" / token
    folder.mkdir(parents=True)
    video = folder / clip.name
    shutil.copyfile(clip, video)
    script_copy = folder / "script.md"
    if script is None:
        script_copy.write_text("今天我們來聊短影音剪輯。\n", encoding="utf-8")
    else:
        shutil.copyfile(script, script_copy)
    return create_project(video, script_copy, slug=f"t{token}")


def keys(text: str) -> list[Key]:
    """The key of every comparison unit of `text`: what the aligner reads from an ASR transcript."""
    return [unit_key(u) for u in split_units(text)]


def alts_of(text: str) -> list[list[tuple[Key, ...]]]:
    """The match alternatives of every comparison unit of `text`: what the aligner reads from a script."""
    return [unit_alts(u) for u in split_units(text)]


def fake_asr(
    asr: str | list[tuple[str, float, float]], times: dict[str, tuple[float, float]] | None = None
) -> AsrResult:
    """An AsrResult without an engine. A string gives every unit 0.2 s, one after another from 0 s; a
    list of (text, start, end) shares each item's time equally among its units, as load_asr does with
    a segment. `times` then sets the time of every unit whose (normalised) text is a key."""
    segments = [(asr, 0.0, 0.0)] if isinstance(asr, str) else asr
    tokens: list[AsrToken] = []
    for text, start, end in segments:
        units = split_units(text)
        for i, unit in enumerate(units):
            if isinstance(asr, str):
                lo, hi = round(0.2 * len(tokens), 6), round(0.2 * (len(tokens) + 1), 6)
            else:
                lo = start + (end - start) * i / len(units)
                hi = start + (end - start) * (i + 1) / len(units)
            lo, hi = (times or {}).get(unit.text, (lo, hi))
            tokens.append(AsrToken(unit.text, unit.kind, unit_key(unit), lo, hi))
    return AsrResult(MODELS["1.7b"], "".join(text for text, _, _ in segments), tokens)


def transcript_of(
    script: str, asr: str | list[tuple[str, float, float]], times: dict[str, tuple[float, float]] | None = None
) -> dict:
    """The transcript build_transcript makes from the script text `script` and a fake ASR result (see
    fake_asr)."""
    sentences, markers = parse_script(script)
    result = fake_asr(asr, times)
    audio_s = max((t.end for t in result.tokens), default=0.0)
    return build_transcript(sentences, markers, result, audio_s, fallback_used=False)


def fixture_transcript() -> dict:
    """The transcript of the Meijia fixture: its script.md and the recorded 1.7B result."""
    sentences, markers = parse_script((MEIJIA / "script.md").read_text(encoding="utf-8"))
    result = load_asr(MEIJIA / "asr-qwen3-1.7b.json", MODELS["1.7b"])
    audio_s = sf.info(str(MEIJIA / "fixture.wav")).duration
    return build_transcript(sentences, markers, result, audio_s, fallback_used=False)


def real_hf_home(environ: Mapping[str, str], home: Path) -> Path:
    """Where Hugging Face keeps its cache for the environment `environ` and the home folder `home`:
    $HF_HOME, else $XDG_CACHE_HOME/huggingface, else <home>/.cache/huggingface. An empty variable
    counts as unset. conftest calls it with the real environment before HOME is faked."""
    if environ.get("HF_HOME"):
        return Path(environ["HF_HOME"])
    if environ.get("XDG_CACHE_HOME"):
        return Path(environ["XDG_CACHE_HOME"]) / "huggingface"
    return home / ".cache" / "huggingface"


def yunet_model() -> Path:
    """The YuNet model that `pipeline setup` downloaded. A missing model fails the test (it never
    skips it), so a machine without the model cannot pass the face tests by accident."""
    path = yunet_path()
    if not path.is_file():
        pytest.fail(f"YuNet face model not found at {path}: run `uv run pipeline setup` first")
    return path


def caption_dictionary() -> Path:
    """The caption dictionary that `pipeline setup` downloaded. A missing dictionary fails the test (it
    never skips it), as yunet_model does, so a machine without it cannot pass the caption tests by accident."""
    path = dictionary_path()
    if not path.is_file():
        pytest.fail(f"caption dictionary not found at {path}: run `uv run pipeline setup` first")
    return path


def static_font(weight: str = "bold") -> Path:
    """The static font ("regular" or "bold") of the default style, cut by `pipeline setup`. A missing font
    fails the test (it never skips it) with a message that says to run setup, as yunet_model does."""
    path = font_path(load_style(), weight)
    if not path.is_file():
        pytest.fail(f"font {path} not found: run `uv run pipeline setup` first")
    return path


def require_assets() -> None:
    """Fail, with a message that says to run `pipeline setup`, unless the face model, the caption dictionary
    and both static fonts are there: what a test needs that runs the whole flow."""
    yunet_model()
    caption_dictionary()
    for weight in ("regular", "bold"):
        static_font(weight)


def write_raw(
    dest_dir: Path, segments: list[tuple[str, float, float]], chunks: list[dict] | None = None, stem: str = "raw"
) -> Path:
    """A raw `mlx-qwen3-asr -f json` result, `dest_dir/<stem>.json`, with the keys mlx-qwen3-asr 0.4.4
    writes for `--language Chinese --timestamps`: text, language, finish_reason, truncated, segments
    (one {text, start, end} per aligner token) and chunks. Without `chunks` there is one chunk that
    covers the audio and stopped normally. A given chunk is a dict of keys that override a normal
    chunk, so `[{"truncated": True}]` is a chunk that hit the token limit, as the engine writes it
    (finish_reason "length", and the top-level truncated flag set)."""
    text = "".join(word for word, _, _ in segments)
    normal = {
        "text": text,
        "start": 0.0,
        "end": max((end for _, _, end in segments), default=0.0),
        "language": "Chinese",
        "finish_reason": "eos",
        "truncated": False,
        "generated_tokens": len(segments),
        "max_new_tokens": 512,
    }
    items = [
        {**normal, "chunk_index": index, **({"finish_reason": "length"} if given.get("truncated") else {}), **given}
        for index, given in enumerate([{}] if chunks is None else chunks)
    ]
    truncated = any(item["truncated"] for item in items)
    data = {"text": text, "language": "Chinese", "finish_reason": "length" if truncated else "eos", "truncated": truncated}
    if segments:  # the engine leaves the key out when there are no segments
        data["segments"] = [{"text": word, "start": start, "end": end} for word, start, end in segments]
    data["chunks"] = items
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{stem}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def synth_wav(dest_dir: Path, parts: list[tuple[str, float, float]], sr: int = 48000, channels: int = 1,
              name: str = "synth.wav") -> Path:
    """`dest_dir/<name>`: the parts one after another, each (kind, seconds, dBFS). A "tone" is a 500 Hz
    sine (five whole cycles in 10 ms, so every 10 ms window has the same level), "noise" is seeded
    white noise, "silence" is digital zero (its level is ignored). The level is the RMS in dBFS. Every
    channel carries the same signal. 16-bit PCM."""
    rng = np.random.default_rng(7)
    pieces = []
    for kind, seconds, level in parts:
        n = round(seconds * sr)
        rms = 10 ** (level / 20)
        if kind == "tone":
            wave = rms * np.sqrt(2) * np.sin(2 * np.pi * 500 * (np.arange(n) / sr))
        elif kind == "noise":
            wave = rms * rng.standard_normal(n)
        elif kind == "silence":
            wave = np.zeros(n)
        else:
            raise ValueError(f"unknown part kind {kind!r}")
        pieces.append(wave)
    mono = np.concatenate(pieces) if pieces else np.zeros(0)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    sf.write(str(path), np.tile(mono[:, None], (1, channels)), sr, subtype="PCM_16")
    return path


QUIET_DB, LOUD_DB = -80.0, -20.0


def flat_envelope(seconds: float, loud: Sequence[tuple[float, float]] = ()) -> Envelope:
    """An envelope of `seconds` that is quiet (-80 dB) everywhere except the `loud` spans, which are
    speech (-20 dB). With no loud span the envelope is flat: build_trims warns of few pauses and
    flags every cut as needing listening, since no point lies 20 dB under the speech level."""
    db = np.full(round(seconds / HOP_S), QUIET_DB)
    for start, end in loud:
        db[round(start / HOP_S):round(end / HOP_S)] = LOUD_DB
    return Envelope(db, HOP_S)


def transcript_and_trims(
    script: str,
    asr: str | list[tuple[str, float, float]],
    loud: Sequence[tuple[float, float]] = (),
    pad_s: float = 1.0,
) -> tuple[dict, dict]:
    """(transcript, trim.json document) for `script` and a fake ASR result (see fake_asr). The trims
    come from build_trims on an envelope that is quiet everywhere but the `loud` spans (see
    flat_envelope). After the speech comes `pad_s` of quiet and then a stretch of speech, so that the
    file has both quiet and speech as footage does (the silence threshold needs both): noise floor
    -80 dB, threshold -74 dB."""
    transcript = transcript_of(script, asr)
    end = transcript["audio_s"]
    reference = (end + pad_s, end + pad_s + max(1.0, end / 3))
    return transcript, build_trims(transcript, flat_envelope(reference[1], [*loud, reference]))


def trims_for(
    script: str,
    asr: str | list[tuple[str, float, float]],
    loud: Sequence[tuple[float, float]] = (),
    pad_s: float = 1.0,
) -> dict:
    """The trim.json document of transcript_and_trims."""
    return transcript_and_trims(script, asr, loud, pad_s)[1]


def default_edit(transcript: dict, trims: dict) -> dict:
    """An edit.json for a transcript and its trims: every trim that suggests a cut is accepted, every
    other one rejected, no extra cut; every spoken sentence is listed as a main claim; line captions."""
    return {
        "version": 1,
        "style": "default",
        "cuts": {
            "accept": [t["id"] for t in trims["trims"] if t["suggest"] == "cut"],
            "reject": [t["id"] for t in trims["trims"] if t["suggest"] != "cut"],
            "extra": [],
        },
        "sentences": [
            {"id": s["id"], "sem": "claim", "weight": "main"} for s in transcript["sentences"] if s["spoken"]
        ],
        "captions": {"mode": "line"},
    }


def line_of(text: str) -> list:
    """The lines break_lines makes of the one script sentence `text`: its units come from parse_script,
    the font is the bold static font of the default style."""
    from pipeline.captions import break_lines

    sentences, _ = parse_script(text)
    assert len(sentences) == 1, "line_of takes one sentence"
    sentence = sentences[0]
    units = [{"text": u.text, "span": list(u.span)} for u in sentence.units]
    style = load_style()
    return break_lines(sentence.text, units, static_font("bold"), style)


def event(text: str, start: float, end: float):
    """A caption Event with one piece per character of `text`, each a unit; the pieces start evenly
    from `start` to `end`."""
    from pipeline.captions import Event, Line, Piece

    n = len(text)
    return Event(start, end, Line([Piece(c, i) for i, c in enumerate(text)]),
                 [start + (end - start) * i / n for i in range(n)])


def timed_sentence(sid: str, text: str, times: Sequence[tuple[float, float]], spoken: bool = True) -> dict:
    """A transcript.json sentence made by hand: one unit per split_units unit of `text`, with the
    given (start, end) times in source seconds."""
    units = split_units(text)
    assert len(units) == len(times)
    return {
        "id": sid, "text": text, "display_source": "script", "spoken": spoken,
        "start": times[0][0], "end": max(end for _, end in times), "ratio": 1.0, "needs_listen": False,
        "reasons": [],
        "units": [{"text": text[u.span[0]:u.span[1]], "span": list(u.span), "start": lo, "end": hi, "src": "asr"}
                  for u, (lo, hi) in zip(units, times)],
    }


def edited_project_from_documents(
    tmp_path: Path, transcript: dict, trims: dict, edit: dict | None = None, size: tuple[int, int] = (1080, 1920)
) -> Project:
    """A project in the temp studio whose work/ holds what the storyboard step reads: prep ran on a
    gray-counter clip (frame N is gray 16 + 4 * (N % 50)) of `size` (width, height) that is the
    transcript's audio plus two seconds long, and transcript.json, trim.json and edit.json are written
    from the given documents (`edit` is default_edit(transcript, trims) when not given)."""
    from media_fixtures import make_clip

    from pipeline.prep import prep

    require_assets()
    seconds = math.ceil(transcript["audio_s"]) + 2
    p = project_with(make_clip(tmp_path / "clip", w=size[0], h=size[1], seconds=seconds, gray_counter=True))
    prep(p)
    for name, document in (("transcript.json", transcript), ("trim.json", trims)):
        (p.work / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    edit = default_edit(transcript, trims) if edit is None else edit
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def edited_project_from_script(tmp_path: Path, script: str, asr: str | None = None) -> Project:
    """edited_project_from_documents for the script text `script` and a fake ASR result that says
    `asr` (the script itself when not given), at 0.2 s per unit from 0 s (see transcript_of)."""
    transcript, trims = transcript_and_trims(script, script if asr is None else asr)
    return edited_project_from_documents(tmp_path, transcript, trims)


# ---- the Meijia project step by step, and the checks of a render ------------------------------


def meijia_video(tmp_path: Path, **clip_options) -> Path:
    """The Meijia video, in `tmp_path/clip`: a gray-counter clip as long as fixture.wav, with fixture.wav
    as its sound. `clip_options` go to make_clip (hdr=True, video_delay=0.5 ...); the sound starts as
    late as the video does, so that the speech stays with its picture."""
    from media_fixtures import make_clip, with_audio

    seconds = round(sf.info(str(MEIJIA / "fixture.wav")).duration, 2)
    picture = make_clip(tmp_path / "clip", seconds=seconds, audio=None, gray_counter=True, **clip_options)
    delay = clip_options.get("video_delay", 0.0)
    return with_audio(picture, MEIJIA / "fixture.wav", tmp_path / "clip", audio_delay=delay)


def meijia_transcribed_project(tmp_path: Path, **clip_options) -> Project:
    """A project made from the Meijia fixture, with prep and transcribe done. The video is the one
    meijia_video makes; the script is the Meijia script.md. transcribe reads the recorded 1.7B result
    (--asr-json), so no engine runs. `clip_options` go to meijia_video."""
    require_assets()
    p = project_with(meijia_video(tmp_path, **clip_options), script=MEIJIA / "script.md")
    prep(p)
    assert main(["transcribe", p.name, "--asr-json", str(MEIJIA / "asr-qwen3-1.7b.json")]) == 0
    return p


def load_t_tr(p: Project) -> tuple[dict, dict]:
    """(transcript, trims): the documents work/transcript.json and work/trim.json of `p`."""
    transcript = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))
    trims = json.loads((p.work / "trim.json").read_text(encoding="utf-8"))
    return transcript, trims


def trim_project(p: Project) -> Project:
    """`p` with the trim step done."""
    assert main(["trim", p.name]) == 0
    return p


def write_default_edit(p: Project) -> Project:
    """`p` with the edit.json that default_edit makes for its transcript and trims: every suggested cut
    is accepted, no extra cut."""
    edit = default_edit(*load_t_tr(p))
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def validated_storyboard(p: Project, **kwargs) -> dict:
    """build_storyboard after a validate of `p`. The storyboard refuses an edit.json that validate has not
    passed on the edit.json, transcript.json and trim.json as they are now, and tests that are not about
    that rewrite one of them after the fixture wrote it. `kwargs` go to build_storyboard."""
    validate_project(p)
    return build_storyboard(p, **kwargs)


def approve_project(p: Project) -> Project:
    """`p` with the storyboard built and gate 1 approved, through validate_project, the real
    build_storyboard and approve_gate."""
    validated_storyboard(p)
    approve_gate(p, "gate1")
    return p


def force_gate1_open(p: Project) -> Project:
    """Make state.json say that prep, transcribe, trim and the storyboard ran on, and gate 1 was approved
    for, the files as they are now, without running them again. For tests that need the render to see an
    edit.json or transcript.json that the storyboard cannot be built from."""
    state = load_state(p)
    for step in ("prep", "transcribe", "trim", "storyboard"):
        for entry in state["steps"][step]["inputs"].values():
            stat = Path(entry["path"]).stat()
            entry.update(sha256=file_sha256(entry["path"]), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    inputs = state["steps"]["storyboard"]["inputs"]
    state["gates"]["gate1"]["storyboard_inputs"] = {name: entry["sha256"] for name, entry in inputs.items()}
    save_state(p, state)
    return p


def meijia_approved_project(tmp_path: Path, **clip_options) -> Project:
    """The Meijia project (see meijia_transcribed_project) taken through trim, the default edit.json
    and approval of gate 1: what render needs."""
    return approve_project(write_default_edit(trim_project(meijia_transcribed_project(tmp_path, **clip_options))))


def approved_project_in(studio: Path) -> Project:
    """The approved Meijia project, built in `studio`, which REELS_STUDIO must already point at (a
    studio is found through that variable only). The clips are made next to it, in studio's parent."""
    assert studio_root() == studio, f"REELS_STUDIO must point at {studio}, not {studio_root()}"
    return meijia_approved_project(studio.parent)


def segments_of(p: Project) -> list[Segment]:
    """The segments the render keeps for the edit.json of `p` (edit.build_segments)."""
    edit, transcript, trims = require_valid(p)
    return build_segments(edit, transcript, trims)


def sample_frame_pairs(segments: Sequence[Segment]) -> list[tuple[int, int]]:
    """(frame number in the reel, frame number in the source) for the first, the middle and the last
    frame of every segment."""
    pairs, reel = [], 0
    for segment in segments:
        first, count = round(segment.start * FPS), round((segment.end - segment.start) * FPS)
        pairs += [(reel + offset, first + offset) for offset in sorted({0, count // 2, count - 1})]
        reel += count
    return pairs


@lru_cache(maxsize=4)
def _corner_luma(video: str, mtime_ns: int) -> np.ndarray:
    """The mean luma (raw 0-255 Y values) of the top-left 64x64 pixels of every frame of `video`."""
    done = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", video, "-vf", "crop=64:64:0:0,extractplanes=y",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(done.stdout, dtype=np.uint8).reshape(-1, 64 * 64).mean(axis=1)


def corner_luma(video: Path) -> np.ndarray:
    return _corner_luma(str(video), video.stat().st_mtime_ns)


def gray_index(video: Path, k: int, reference: Path | None = None) -> int:
    """The counter value (N % 50) of the gray-counter frame that frame number `k` of `video` shows, read
    from the mean luma of its top-left 64x64 pixels (a caption never reaches that corner). A gray-counter
    frame N has luma 16 + 4 * (N % 50). When `video` went through a tone mapping that changed the grays,
    `reference` is the video that holds the unchanged picture (the first 50 of its frames are matched)."""
    luma = float(corner_luma(video)[k])
    if reference is None:
        return round((luma - 16) / 4)
    table = corner_luma(reference)[:50]
    return int(np.argmin(np.abs(table - luma)))


# ---- loudness ----------------------------------------------------------------------------------


def loudness(path: Path) -> tuple[float, float]:
    """(integrated loudness in LUFS, true peak in dBTP) of the sound of `path`, as ffmpeg's ebur128 measures them.
    The log is written next to `path`."""
    log = path.with_name(path.name + ".ebur128.log")
    run_ffmpeg(["-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true:framelog=quiet",
                "-f", "null", "-"], log)
    text = log.read_text(errors="replace")
    integrated = float(re.findall(r"\bI:\s+(-?[\d.]+) LUFS", text)[-1])
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", text)[-1]
    return integrated, float(peak)


# ---- the Meijia stages, built once per session and copied for each test ------------------------


@dataclass(frozen=True)
class Template:
    """A stage of the Meijia project (default options: no clip_options), built once per session under `root` as a
    test builds it under its tmp_path (clip/, studio/inbox/<token>/ and studio/projects/<name>/), then made
    read-only by freeze_template. `modes` holds the mode of every folder and file under the TEMPLATE_PARTS of
    `root` as the build left it, by path relative to `root`; `rooted` holds the sha256 of every file there that
    holds the path of `root`, by the same relative path."""

    root: Path
    name: str
    modes: dict[str, int]
    rooted: dict[str, str]


TEMPLATE_PARTS = ("clip", "studio")  # what a copy takes of a template's root; home/ is the test's own


def _root_marker(root: Path) -> bytes:
    """The bytes a file holds where it names something under `root`: its path and a separator."""
    return (str(root) + os.sep).encode("utf-8")


def _template_paths(root: Path) -> list[Path]:
    """Every folder and file under the TEMPLATE_PARTS of `root`, the parts themselves included."""
    return [path for part in TEMPLATE_PARTS for path in (root / part, *sorted((root / part).rglob("*")))]


def freeze_template(root: Path, p: Project) -> Template:
    """The Template of project `p`, just built under `root`: the modes of its folders and files are noted, the
    files that hold the path of `root` are found, and then every folder and file is made read-only, so that
    nothing can write into the template while the session runs (thaw_template undoes it)."""
    assert root == root.resolve(), f"{root} is not a resolved path"
    marker = _root_marker(root)
    modes, rooted = {}, {}
    for path in _template_paths(root):
        info = path.lstat()
        assert stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), f"{path} is neither a folder nor a file"
        rel = path.relative_to(root).as_posix()
        modes[rel] = stat.S_IMODE(info.st_mode)
        if stat.S_ISREG(info.st_mode) and marker in path.read_bytes():
            rooted[rel] = file_sha256(path)
    for rel, mode in modes.items():
        os.chmod(root / rel, mode & ~0o222)
    return Template(root, p.name, modes, rooted)


def thaw_template(template: Template) -> None:
    """Give the folders and files of `template` back the modes the build left them, as the session ends."""
    for rel, mode in template.modes.items():
        os.chmod(template.root / rel, mode)


def copy_template(template: Template, root: Path) -> Project:
    """A project of its own under `root` that stands where `template` stands, for a test whose tmp_path is `root`
    (REELS_STUDIO must point at root/studio, and the project must not be there yet). The TEMPLATE_PARTS are copied
    with the modification times of their files (so a step skips on the recorded size and mtime_ns without reading a
    file again, as in the template) and with the modes the build gave them. Every file that holds the path of the template's root then names `root`
    instead, and wherever state.json records the content of such a file (the sha256, size and mtime_ns of a step
    input, the sha256 of an approval or of a validate), it records the new content: so the copy holds no path of
    the template, and is what building the stage under `root` would have made."""
    root = root.resolve()
    assert studio_root() == root / "studio", f"REELS_STUDIO must point at {root / 'studio'}, not {studio_root()}"
    folder = root / "studio" / "projects" / template.name
    assert not folder.exists(), f"{folder} exists already: a test gets one stage of the Meijia project, not two"
    for part in TEMPLATE_PARTS:
        shutil.copytree(template.root / part, root / part, copy_function=shutil.copy2, dirs_exist_ok=True)
    for rel, mode in template.modes.items():
        os.chmod(root / rel, mode)
    old, new = _root_marker(template.root), _root_marker(root)
    rewritten = {}  # path in the copy -> (sha256 of the template's file, sha256 of the copy's)
    for rel, sha256 in template.rooted.items():
        path = root / rel
        path.write_bytes(path.read_bytes().replace(old, new))
        rewritten[str(path)] = (sha256, file_sha256(path))
    p = load_project(template.name)
    _record_rewritten(p, rewritten)
    return p


def _record_rewritten(p: Project, rewritten: dict[str, tuple[str, str]]) -> None:
    """Make state.json of the copy `p` record the new content of each file copy_template rewrote (`rewritten`:
    path -> (sha256 before, sha256 after)): each step input record of it gets the new sha256, size and mtime_ns,
    and the approval of a gate and edit_checked, which keep a sha256 by input name, get the new sha256. Each
    record must have held the content of the template's file."""

    def renewed(path: str, sha256: str) -> str:
        if path not in rewritten:
            return sha256
        before, after = rewritten[path]
        assert sha256 == before, f"state.json of the template records another content of {path}"
        return after

    state = load_state(p)
    assert state["source"]["video"] not in rewritten and state["source"]["script"] not in rewritten
    for record in state["steps"].values():
        for entry in record.get("inputs", {}).values():
            if entry["path"] in rewritten:
                info = Path(entry["path"]).stat()
                entry.update(sha256=renewed(entry["path"], entry["sha256"]), size=info.st_size,
                             mtime_ns=info.st_mtime_ns)
    storyboard = (state["steps"].get("storyboard") or {}).get("inputs") or {}
    for gate in state["gates"].values():
        recorded = gate.get("storyboard_inputs") or {}
        for name, sha256 in recorded.items():
            recorded[name] = renewed(storyboard[name]["path"], sha256)
    if "edit_checked" in state:
        checked = state["edit_checked"]
        for name, path in _edit_files(p).items():
            checked[name] = renewed(str(path), checked[name])
    save_state(p, state)

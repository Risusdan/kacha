"""Gate 1 storyboard: the page the user reviews before approving the edit, and storyboard.md.

review/gate1/index.html is one light-mode file with no external reference: the thumbnails and the
clips are embedded as data URIs, so it opens on a phone as it is. From the top it shows the title, the
project name, a summary (source length, new length, how many pieces are cut, how many cut points the reel
has and how many of its shots are punched in, how many decisions, how many other places to listen to), the
warnings, the two lists of review_items (要你決定, then
其他要聽的地方), a table with one row per script sentence and a table with one row per rough-cut
candidate. No width is fixed: at 640 px wide or less each table row becomes a card and every cell
shows its column name (its data-label). review/gate1/storyboard.md holds the summary, the warnings,
the two lists as numbered Markdown lists with the page's numbers and the two tables (the sentence table
without thumbnails), for Claude to quote in the conversation.

A list item shows its number, where it is in the cut reel (m:ss.s), its kind (a cut point's label
instead), what was said in 「」 (— when nothing was) and, for a candidate, whether edit.json cuts it now.
An item about a sentence whose captions show the recognised text also shows the caption lines and the
script's sentence. Each item has one clip, the one review_items gives it, kept inside the audio. An
item about a candidate, or about a sentence of any kind (recognised text, a cut point, a poor match with
the script), that has a note in edit.json's notes shows the note after its details; storyboard.md has it
as the last indented line of the item. A note is only Claude's: an item without one shows none, whatever
the review page would suggest for it.

A clip is played by the one script at the end of the page: a play button holds the clip as a data URI in
data-clip, and the script decodes it and plays it with Web Audio. The <audio> element does not play in
the file preview of a phone, where the page is sometimes opened. A phone in silent mode plays nothing
through Web Audio, so each button is followed by SILENT_HINT.

The pieces cut are the accepted candidates that are in the reel (review_items.trims_in_reel) and the
cuts.extra ranges.

A sentence row is one of three kinds. A kept sentence shows its caption lines (what build_events would
put on screen, lines joined with " / "), the time it starts in the cut reel, its sem tag and weight, the
span in the reel and scale of each shot punched in that starts in it (punch.framings:
0:02.8 到 0:05.1 放大 1.1 倍; nothing when no such shot starts in it), each scene that starts in it as its card's
name, its texts and its span in the reel (數字卡：兩小時／一天多撐（0:12.0 到 0:14.5）, joined by 、; nothing when none
does), and a thumbnail. A sentence that cuts.extra removes is struck through, with the reason of the range, and has
a thumbnail but no time. A sentence the speaker did not say shows only "沒講到". A kept sentence with a
line that starts or ends inside a long word says so, and adds LONG_WORD_WARNING to the warnings. A row
that list items are about names their numbers. A sentence row has no clip.

After the two lists, a reel with a scene that shows has the section 動畫的樣子: a sentence (PREVIEWS_ABOUT), then
one preview of each scene that shows, in the order they show, each with its card's name, texts and span in the reel
under it, as the 動畫 cell has them. A preview is a PREVIEW_W x PREVIEW_H JPEG of the reel at the frame in the middle of
the scene (reel_frame_source): the source frame there, framed as the shot it is in frames it (preview_crop), with the
still of the card (scene_render.render_stills: the card at rest where it goes, card_place.placed_scenes) laid over it;
no captions. The cards go where the faces of work/faces.json leave room, so faces.json and the card sources are
inputs of the step when a scene shows, and neither is read when none does. Stills that cannot be made (no Node, a
character the fonts cannot draw, Remotion failing) are a warning, STILLS_FAILED and the reason, and the page has no
such section; a storyboard made without its stills is made again by the next storyboard command rather than skipped.
A preview ffmpeg cannot make refuses the storyboard, as a thumbnail does. storyboard.md has no previews.

The summary counts the scenes that show (動畫 N 段). A scene that does not show, or shows for less than
scenes.MIN_SCENE_FRAMES, is a warning: its scene_warning (English, with its index) in the result for Claude, a
sentence in Chinese that names it by its card and texts on the page and in storyboard.md.

A candidate row shows what is cut (kind, reason, recognised text), whether edit.json accepts it,
"不在成品裡" when it is not in the reel, the numbers of the list items about it, and a clip of the
candidate with TRIM_PAD_S around it.

The thumbnails and clips are cut with ffmpeg into named files under work/gate1-media/. Each file is
unlinked as soon as it is embedded. The page and the Markdown are built in memory first and written
after review/gate1 was replaced, so a failure leaves the old folder as it was.
"""

import base64
import contextlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import soundfile as sf

from pipeline import punch
from pipeline.captions import LONG_WORD_WARNING, Line, build_events
from pipeline.card_place import placed_scenes
from pipeline.edit import (
    FPS,
    Segment,
    build_segments,
    kept_sentences,
    load_document,
    map_time,
    require_valid,
    total_duration,
)
from pipeline.errors import PipelineError
from pipeline.media import probe as probe_video
from pipeline.media import run_ffmpeg
from pipeline.project import Project, edit_stage, run_step
from pipeline.punch import Shot, base_window, framings, window
from pipeline.scenes import MIN_SCENE_FRAMES, Layout, Placed, scene_label, scene_layout, scene_spans, scene_warning
from pipeline.review_items import TRIM_PAD_S, Item, review_items, trims_in_reel
from pipeline.safe_delete import safe_rmtree
from pipeline.scene_render import render_stills, source_inputs, still_paths
from pipeline.style import font_path, load_style
from pipeline.trim import FEW_PAUSES_WARNING
from pipeline.words import caption_inputs

THUMB_W = 270  # width of a thumbnail in pixels; the page shows it 90 px wide (three times for a sharp phone screen)
PREVIEW_W, PREVIEW_H = 540, 960  # a card preview in pixels; the page shows it at most 270 px wide
PREVIEWS_ABOUT = "樣張是卡片停住時的樣子，背景是那段動畫中間的畫面，字幕沒有畫上去。"
STILLS_FAILED = "card previews not made: "  # and the reason
PREVIEWS_HEADING = "<h2>動畫的樣子</h2>"
AUDIO_BITRATE = "64k"
MEDIA_DIR = "gate1-media"  # under work/
DASH = "—"
OLDER_FILES_REFUSAL = (
    "edit.json was checked against an older transcript.json or trim.json: "
    "re-check every trim and sentence id, then run validate"
)
NOT_VALIDATED_REFUSAL = "edit.json has not been validated: run validate"
BOUNDS_REFUSAL = "sentence bounds in trim.json are out of order: run trim again"
PROBE_MISSING = "probe.json not found: run prep first"
SILENT_HINT = "沒有聲音的話，請關掉靜音模式"

_WARNING_TEXT = {
    FEW_PAUSES_WARNING: "停頓很少，切點是否在靜音裡的判斷不可靠",
    LONG_WORD_WARNING: "有詞太長，字幕從詞的中間斷開",
}
_ASPECT_WARNING = re.compile(r"aspect \d+:\d+ will be center-cropped to 9:16")  # prep's, for any width and height
_ASPECT_TEXT = "畫面不是 9:16，會置中裁切成 9:16"
_ITEM_TEXT = {  # the kind of a list item; a cut point shows its label instead
    "asr_text": "字幕用辨識文字", "reworded": "改詞重講", "extra": "腳本外的話",
    "trim_cut_point": "剪接處不在靜音裡", "low_match": "吻合度低",
}
_KIND_TEXT = {"filler": "贅字", "repeat": "重講", "extra": "腳本外的話", "pause": "停頓"}
_RETAKE_TEXT = {"cut": "NG 段", "list": "改詞重講"}  # a retake by its suggest: a reworded take is only listed
_WEIGHT_TEXT = {"main": "主要", "sub": "次要"}
_LONG_WORD_MARK = "有詞太長，從中間斷開"
_NOT_IN_REEL = "不在成品裡"
_NO_DECISIONS = "沒有要你決定的事。"
_NO_LISTEN = "沒有其他要聽的地方。"
_SENTENCE_COLUMNS = ("列", "縮圖", "新時間", "字幕", "語意", "推近", "動畫", "標記")
_TRIM_COLUMNS = ("編號", "種類", "原因", "辨識文字", "處理", "標記")
_NEWLINES = re.compile(r"[\r\n\x0b\x0c\x85\u2028\u2029]+")


def _warning_text(code: str) -> str:
    """The Chinese text of a warning code; a code without one is shown as it is."""
    return _ASPECT_TEXT if _ASPECT_WARNING.fullmatch(code) else _WARNING_TEXT.get(code, code)


def clock(seconds: float) -> str:
    """`seconds` as m:ss.s (minutes, two digits of seconds, one tenth), rounded to the tenth."""
    tenths = max(0, int(seconds * 10 + 0.5))
    return f"{tenths // 600}:{tenths // 10 % 60:02d}.{tenths % 10}"


def _see(numbers: list[int]) -> str:
    """The mark that points a table row at the list items about it: 見第 2 項, 見第 2、5 項."""
    return f"見第 {'、'.join(str(number) for number in numbers)} 項"


# ---- what the page shows ----------------------------------------------------------------------


@dataclass
class _Clip:
    span: tuple[float, float]  # source times
    uri: str = ""  # data URI, filled in by _Media


@dataclass
class _ListItem:
    number: int
    new_time: str  # where it is in the cut reel, m:ss.s
    what: str  # the kind in Chinese, or a cut point's label
    text: str  # what was said; DASH when nothing was
    now: str  # for a candidate, what edit.json does with it now ("目前：剪掉"); empty for the other kinds
    details: list[str]  # the lines under the item: the captions and the script of a recognised sentence
    clip: _Clip
    note: str = ""  # Claude's note about the candidate or the sentence in edit.json, if it has one


@dataclass
class _Sentence:
    id: str
    state: str  # "kept", "removed" (by cuts.extra) or "unspoken"
    text: str  # the sentence as it is written or was recognised
    lines: list[str] = field(default_factory=list)  # the caption lines of a kept sentence
    long_word: bool = False  # a caption line starts or ends inside a long word
    new_time: str = ""  # where a kept sentence starts in the cut reel, m:ss.s
    sem: str = ""
    weight: str = ""
    note: str = ""  # the reason of the cuts.extra range that removes the sentence
    framing: str = ""  # each shot punched in that starts in the sentence (_framing_text); empty when none does
    animation: str = ""  # each scene that starts in the sentence (_animation_texts); empty when none does
    items: list[int] = field(default_factory=list)  # the numbers of the list items about it
    thumb_at: float | None = None  # source time of the thumbnail
    thumb: str = ""  # data URI, filled in by _Media


@dataclass
class _Trim:
    id: str
    kind: str  # the label shown
    reason: str
    text: str
    cut: bool  # edit.json accepts it
    in_reel: bool
    items: list[int]  # the numbers of the list items about it
    clip: _Clip


@dataclass
class _Preview:
    placed: Placed
    label: str  # the card's name, its texts and its span in the reel (_scene_text)
    at: float  # source time of the frame in the middle of the scene
    crop: tuple[int, int, int, int]  # x, y, w, h of what the reel shows of the picture then (preview_crop)
    still: Path | None = None  # the still of the card, filled in by _draw_stills
    uri: str = ""  # data URI, filled in by _Media


@dataclass
class _Plan:
    project: str
    video: Path
    source_s: float
    duration_s: float
    cuts: int
    cut_points: int  # where the reel jumps: one less than its shots
    punched: int  # the shots punched in (a scale over 1)
    scenes: int  # the scenes that show
    warnings: list[tuple[str, str]]  # (the code as the pipeline spells it, the text the page and storyboard.md show)
    decisions: list[_ListItem]
    listen: list[_ListItem]
    sentences: list[_Sentence]
    trims: list[_Trim]
    style: dict
    previews: list[_Preview]  # emptied by _draw_stills when the stills cannot be made


def _document(path: Path, step: str, keys: tuple[str, ...]) -> dict:
    """A JSON document that pipeline step `step` wrote, with the top-level `keys` in it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PipelineError(f"{path} not found: run {step} first") from None
    except (OSError, ValueError) as error:
        raise PipelineError(f"cannot read {path}: {error}") from error
    missing = [key for key in keys if not isinstance(data, dict) or key not in data]
    if missing:
        raise PipelineError(f"cannot read {path}: no {', '.join(missing)} (run {step} again)")
    return data


def _check_bounds_order(transcript: dict, trims: dict) -> None:
    """Refuse trim.json bounds that do not run forward: along the spoken sentences, in script order, neither
    cut_in nor cut_out may go back, and no sentence may end its reel before it starts it. Neighbouring
    sentences may overlap (a cut_in may lie before the cut_out of the sentence before it). The cut points of
    the lists (review_items._cut_edges) and the segments of the cut reel (build_segments) read the bounds as
    such a sequence."""
    previous: tuple[float, float] | None = None
    for sentence in transcript["sentences"]:
        if not sentence["spoken"]:
            continue
        bound = trims["bounds"][sentence["id"]]
        cut_in, cut_out = bound["cut_in"], bound["cut_out"]
        if cut_out < cut_in or (previous is not None and (cut_in < previous[0] or cut_out < previous[1])):
            raise PipelineError(BOUNDS_REFUSAL)
        previous = (cut_in, cut_out)


def removal_notes(edit: dict, transcript: dict) -> dict[str, str]:
    """Sentence id -> the reason of the cuts.extra range it lies in (the first when two overlap)."""
    index = {sentence["id"]: i for i, sentence in enumerate(transcript["sentences"])}
    notes: dict[str, str] = {}
    for item in edit["cuts"].get("extra", []):
        for sid, i in index.items():
            if index[item["from"]] <= i <= index[item["to"]]:
                notes.setdefault(sid, item["reason"])
    return notes


def _caption_lines(edit: dict, transcript: dict, segments: list[Segment], style: dict) -> dict[str, list[Line]]:
    """Kept sentence id -> the lines the render shows for it. build_events works on a whole transcript;
    given only one sentence it returns the events of that sentence's lines."""
    font = font_path(style, "bold")
    sentences = {sentence["id"]: sentence for sentence in transcript["sentences"]}
    lines = {}
    for sid in kept_sentences(edit, transcript):
        alone = {**transcript, "sentences": [sentences[sid]]}
        lines[sid] = [event.line for event in build_events(alone, edit, segments, font, style)]
    return lines


def _about(items: list[Item], key: str) -> dict[str, list[int]]:
    """Sentence or candidate id (`key` "sentence" or "trim") -> the numbers of the items about it."""
    numbers: dict[str, list[int]] = {}
    for item in items:
        if getattr(item, key) is not None:
            numbers.setdefault(getattr(item, key), []).append(item.number)
    return numbers


def _list_items(items: list[Item], transcript: dict, segments: list[Segment], captions: dict[str, list[Line]],
                notes: dict[str, str]) -> list[_ListItem]:
    sentences = {sentence["id"]: sentence for sentence in transcript["sentences"]}
    shown = []
    for item in items:
        details = []
        if item.kind == "asr_text":
            details = ["字幕：" + " / ".join(line.text for line in captions[item.sentence]),
                       "腳本：" + sentences[item.sentence]["script_text"]]
        about = item.trim if item.trim is not None else item.sentence  # a candidate's note, else its sentence's
        shown.append(_ListItem(
            item.number, clock(map_time(item.time, segments)),
            item.label if item.kind == "cut_point" else _ITEM_TEXT[item.kind],
            item.text or DASH,
            "" if item.cut is None else ("目前：剪掉" if item.cut else "目前：保留"),
            details, _Clip(item.clip), notes.get(about, ""),
        ))
    return shown


def _framing_text(shots: list[tuple[float, float, float]], segments: list[Segment]) -> str:
    """The shots punched in that start in a sentence (punch.framings), in order, each as its span in the reel and
    its scale as edit.json has it (0:02.8 到 0:05.1 放大 1.1 倍), joined by 、. A shot can start in the middle of a
    sentence and run on into the next ones, so its span is not the sentence's."""
    return "、".join(f"{clock(map_time(start, segments))} 到 {clock(map_time(end, segments))} 放大 {scale} 倍"
                    for start, end, scale in shots)


def _scene_text(item: Layout | Placed) -> str:
    """A scene that shows as its card's name, its texts and its span in the reel: 數字卡：兩小時／一天多撐（0:12.0 到 0:14.5）."""
    name, texts = scene_label(item.scene)
    return f"{name}：{texts}（{clock(item.start / FPS)} 到 {clock((item.start + item.frames) / FPS)}）"


def _animation_texts(layout: list[Layout]) -> dict[str, str]:
    """Sentence id -> the scenes that show and start in it, in edit.json order, each as _scene_text, joined by 、."""
    found: dict[str, list[str]] = {}
    for item in layout:
        if item.gone is None and item.frames > 0:
            found.setdefault(item.scene["at"]["s"], []).append(_scene_text(item))
    return {sid: "、".join(texts) for sid, texts in found.items()}


def reel_frame_source(shots: list[Shot], frame: int) -> tuple[Shot, float]:
    """The shot that reel frame `frame` is in and the source time of the frame it shows there. The reel is the shots
    one after the other, each as many frames as render's select keeps of it, from round(start * FPS) to the frame
    before round(end * FPS); a frame past the end is the last one."""
    first = 0
    for shot in shots:
        begin = round(shot.start * FPS)
        count = round(shot.end * FPS) - begin
        if frame < first + count:
            return shot, (begin + max(0, frame - first)) / FPS
        first += count
    last = shots[-1]
    return last, (round(last.end * FPS) - 1) / FPS


def _nearest_even(value: float) -> int:
    return round(value / 2) * 2


def preview_crop(shot: Shot, picture: tuple[int, int]) -> tuple[int, int, int, int]:
    """(x, y, w, h) in pixels of the picture, (w, h) as ffmpeg delivers it, of what the reel shows in `shot`: the base
    window (punch.base_window) zoomed about the shot's anchor (punch.window), each value to the nearest even pixel as
    render's zoompan has the corner, and kept inside the base window."""
    bx, by, bw, bh = base_window(*picture)
    left, top, size, _ = window((shot.ax, shot.ay), shot.scale)
    w, h = _nearest_even(bw * size), _nearest_even(bh * size)
    return bx + min(_nearest_even(left * bw), bw - w), by + min(_nearest_even(top * bh), bh - h), w, h


def _previews(p: Project, edit: dict, transcript: dict, trims: dict, style: dict, video: Path) -> list[_Preview]:
    """A preview of each scene that shows, in the order they show, with its card placed as render places it: from
    the face boxes of faces.json and the shots of the picture of `video` as ffmpeg delivers it (media.probe). When no
    scene shows, nothing is read and there is none."""
    if not scene_spans(edit, transcript, trims):
        return []
    faces = load_document(p.work / "faces.json", "prep", ("boxes",))
    info = probe_video(video)
    picture = (info.display_w, info.display_h)
    shots = punch.shots(edit, transcript, trims, faces, {"display_w": picture[0], "display_h": picture[1]})
    found = []
    for item in sorted(placed_scenes(edit, transcript, trims, style, faces.get("boxes", []), shots, picture),
                       key=lambda item: item.start):
        shot, at = reel_frame_source(shots, item.start + item.frames // 2)
        found.append(_Preview(item, _scene_text(item), at, preview_crop(shot, picture)))
    return found


def still_failure(error: PipelineError | OSError) -> str:
    """Why stills could not be made, in English: a PipelineError's message, or what an OSError says."""
    return error.message if isinstance(error, PipelineError) else str(error)


def _draw_stills(p: Project, plan: _Plan) -> None:
    """Give each preview the still of its card (render_stills). Stills that cannot be made leave the plan without
    previews and add STILLS_FAILED to its warnings."""
    if not plan.previews:
        return
    try:
        stills = render_stills(p, [preview.placed for preview in plan.previews], plan.style)
    except (PipelineError, OSError) as error:
        reason = still_failure(error)
        plan.warnings.append((f"{STILLS_FAILED}{reason}", f"動畫卡片的樣張做不出來：{reason}"))
        plan.previews = []
        return
    by_index = {item.index: still for item, still in stills}
    for preview in plan.previews:
        preview.still = by_index[preview.placed.index]


def _scene_warnings(layout: list[Layout]) -> list[tuple[str, str]]:
    """(scene_warning, the same in Chinese) for each scene that has a warning."""
    found = []
    for item in layout:
        code = scene_warning(item)
        if code is None:
            continue
        name, texts = scene_label(item.scene)
        if item.gone is not None:
            text = f"動畫「{name}：{texts}」所在的句子整段移除了，這段動畫不會出現。"
        elif item.frames == 0:
            text = f"動畫「{name}：{texts}」的時間都被剪掉了，這段動畫不會出現。"
        else:
            text = f"動畫「{name}：{texts}」剪完只剩 {item.frames / FPS:.1f} 秒，不到 {MIN_SCENE_FRAMES / FPS:g} 秒。"
        found.append((code, text))
    return found


def _sentence_rows(edit: dict, transcript: dict, segments: list[Segment], captions: dict[str, list[Line]],
                   items: list[Item], framed: dict[str, list[tuple[float, float, float]]],
                   animations: dict[str, str]) -> list[_Sentence]:
    tags = {item["id"]: item for item in edit["sentences"]}
    notes = removal_notes(edit, transcript)
    about = _about(items, "sentence")
    rows = []
    for sentence in transcript["sentences"]:
        sid = sentence["id"]
        if not sentence["spoken"]:
            rows.append(_Sentence(sid, "unspoken", sentence["text"], items=about.get(sid, [])))
            continue
        middle = (sentence["start"] + sentence["end"]) / 2
        if sid not in captions:
            rows.append(_Sentence(sid, "removed", sentence["text"], note=notes.get(sid, ""),
                                  items=about.get(sid, []), thumb_at=middle))
            continue
        rows.append(_Sentence(
            sid, "kept", sentence["text"],
            lines=[line.text for line in captions[sid]],
            long_word=any(line.split_word for line in captions[sid]),
            new_time=clock(map_time(sentence["start"], segments)),
            sem=tags[sid]["sem"], weight=tags[sid]["weight"],
            framing=_framing_text(framed.get(sid, []), segments),
            animation=animations.get(sid, ""),
            items=about.get(sid, []),
            thumb_at=middle,
        ))
    return rows


def _trim_kind(trim: dict) -> str:
    if trim["kind"] == "retake":
        return _RETAKE_TEXT.get(trim["suggest"], _RETAKE_TEXT["cut"])
    return _KIND_TEXT.get(trim["kind"], trim["kind"])


def _trim_rows(trims: dict, accepted: set[str], inside: set[str], items: list[Item]) -> list[_Trim]:
    about = _about(items, "trim")
    return [
        _Trim(
            trim["id"], _trim_kind(trim), trim["reason"], trim["text"], trim["id"] in accepted,
            trim["id"] in inside, about.get(trim["id"], []),
            _Clip((trim["start"] - TRIM_PAD_S, trim["end"] + TRIM_PAD_S)),
        )
        for trim in trims["trims"]
    ]


def _plan(p: Project) -> _Plan:
    """Read and check the documents and decide what the page shows, everything but the media."""
    edit, transcript, trims = require_valid(p)
    style = load_style(edit["style"])
    probe = _document(p.work / "probe.json", "prep", ("video_source", "display_w", "display_h", "duration_s"))
    segments = build_segments(edit, transcript, trims)
    captions = _caption_lines(edit, transcript, segments, style)
    decisions, listen = review_items(edit, transcript, trims, segments)
    inside = trims_in_reel(edit, transcript, trims, segments)
    accepted = set(edit["cuts"]["accept"])
    long_word = any(line.split_word for lines in captions.values() for line in lines)
    notes = edit.get("notes", {})
    framed = framings(edit, transcript, trims)
    layout = scene_layout(edit, transcript, trims)
    video = Path(probe["video_source"])
    codes = list(probe.get("warnings") or []) + list(trims.get("warnings", [])) + (
        [LONG_WORD_WARNING] if long_word else [])
    return _Plan(
        project=p.name,
        video=video,
        source_s=float(probe["duration_s"]),
        duration_s=total_duration(segments),
        cuts=len(accepted & inside) + len(edit["cuts"].get("extra", [])),
        cut_points=max(0, len(segments) - 1),
        punched=sum(len(found) for found in framed.values()),
        scenes=sum(1 for item in layout if item.gone is None and item.frames > 0),
        warnings=[(code, _warning_text(code)) for code in codes] + _scene_warnings(layout),
        decisions=_list_items(decisions, transcript, segments, captions, notes),
        listen=_list_items(listen, transcript, segments, captions, notes),
        sentences=_sentence_rows(edit, transcript, segments, captions, decisions + listen, framed,
                                 _animation_texts(layout)),
        trims=_trim_rows(trims, accepted, inside, decisions + listen),
        style=style,
        previews=_previews(p, edit, transcript, trims, style, video),
    )


# ---- thumbnails and clips ---------------------------------------------------------------------


class _Media:
    """Cuts the thumbnails and clips with ffmpeg. Every one goes into a named file under
    work/gate1-media/, is read back as a data URI and the file is unlinked."""

    QUIET = ["-y", "-hide_banner", "-loglevel", "error"]

    def __init__(self, p: Project, video: Path):
        self.video = video
        self.audio = p.work / "audio48k.wav"
        self.folder = p.work / MEDIA_DIR
        self.log = p.work / "storyboard.log"
        self.count = 0
        try:
            self.audio_s = sf.info(str(self.audio)).duration
        except (OSError, RuntimeError) as error:
            raise PipelineError(f"cannot read {self.audio}: {error}") from error

    def fill(self, plan: _Plan) -> None:
        try:
            for item in plan.decisions + plan.listen:
                item.clip.uri = self._clip(*item.clip.span)
            for sentence in plan.sentences:
                if sentence.thumb_at is not None:
                    sentence.thumb = self._thumbnail(sentence.thumb_at)
            for trim in plan.trims:
                trim.clip.uri = self._clip(*trim.clip.span)
            for preview in plan.previews:
                preview.uri = self._preview(preview)
        finally:
            with contextlib.suppress(OSError):  # only an empty folder is removed
                self.folder.rmdir()

    def _embed(self, name: str, args: list[str], mime: str, what: str) -> str:
        """Run ffmpeg with `args` to make the file `name`; its content as a data URI of type `mime`."""
        self.folder.mkdir(exist_ok=True)
        out = self.folder / name
        try:
            run_ffmpeg([*self.QUIET, *args, str(out)], self.log)
            if not out.is_file() or out.stat().st_size == 0:
                raise PipelineError(f"ffmpeg made no {what}", log=str(self.log))
            return f"data:{mime};base64," + base64.b64encode(out.read_bytes()).decode("ascii")
        finally:
            out.unlink(missing_ok=True)

    def _thumbnail(self, t: float) -> str:
        """The frame of the video at source time `t`, THUMB_W wide, as a JPEG."""
        self.count += 1
        args = ["-ss", f"{t:.3f}", "-i", str(self.video), "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2",
                "-q:v", "4"]
        return self._embed(f"thumb-{self.count}.jpg", args, "image/jpeg", f"frame at {t:.2f}s of {self.video}")

    def _preview(self, preview: _Preview) -> str:
        """The frame of the video at the preview's source time, cropped as the reel frames it, scaled to PREVIEW_W x
        PREVIEW_H, with the still of the card scaled to the same size laid over it, as a JPEG."""
        self.count += 1
        x, y, w, h = preview.crop
        graph = (f"[0:v]crop={w}:{h}:{x}:{y},scale={PREVIEW_W}:{PREVIEW_H},setsar=1[frame];"
                 f"[1:v]scale={PREVIEW_W}:{PREVIEW_H}[card];[frame][card]overlay=format=auto")
        args = ["-ss", f"{preview.at:.3f}", "-i", str(self.video), "-i", str(preview.still),
                "-filter_complex", graph, "-frames:v", "1", "-q:v", "4"]
        return self._embed(f"preview-{self.count}.jpg", args, "image/jpeg",
                           f"card preview at {preview.at:.2f}s of {self.video}")

    def _clip(self, start: float, end: float) -> str:
        """The audio from `start` to `end` (source seconds, kept inside the audio) as mono AAC in an m4a."""
        self.count += 1
        lo, hi = max(0.0, start), min(self.audio_s, end)
        if hi <= lo:
            raise PipelineError(f"no audio between {start:.2f}s and {end:.2f}s ({self.audio_s:.2f}s long)")
        args = ["-ss", f"{lo:.3f}", "-t", f"{hi - lo:.3f}", "-i", str(self.audio), "-ac", "1", "-c:a", "aac",
                "-b:a", AUDIO_BITRATE]
        return self._embed(f"clip-{self.count}.m4a", args, "audio/mp4", f"clip at {lo:.2f}s of {self.audio}")


# ---- the page ---------------------------------------------------------------------------------

_CSS = """\
:root { color-scheme: light; }
html { background: #fff; }
body { margin: 0; padding: 16px; background: #fff; color: #1b1b1b;
  font: 16px/1.5 system-ui, "Noto Sans TC", "Microsoft JhengHei", sans-serif; }
h1 { font-size: 1.4rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 28px 0 8px; }
.project { margin: 0 0 12px; color: #666; font-size: 0.9rem; }
.summary dl { display: flex; flex-wrap: wrap; gap: 8px; margin: 0; }
.summary dl > div { flex: 1 1 96px; border: 1px solid #d6d6d6; border-radius: 8px; padding: 8px 12px; }
.summary dt { color: #666; font-size: 0.85rem; }
.summary dd { margin: 0; font-size: 1.25rem; font-weight: 600; }
.warnings { margin: 12px 0 0; padding: 8px 12px 8px 32px; background: #fff6e0; border-radius: 8px; color: #7a4b00; }
ol.items { list-style: none; margin: 0; padding: 0; }
ol.items li { border: 1px solid #d6d6d6; border-radius: 8px; padding: 8px 12px; margin: 0 0 8px; }
ol.items p { margin: 0; overflow-wrap: anywhere; }
ol.items .num { font-weight: 700; }
ol.items .what { font-weight: 600; }
ol.items .time, ol.items .detail { color: #555; }
ol.items .now { color: #a85400; font-weight: 600; }
ol.items .note { margin-top: 4px; padding: 4px 8px; background: #fff6e0; border-radius: 6px; color: #7a4b00; }
.none { color: #555; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; border: 1px solid #e2e2e2; border-radius: 8px; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 8px 10px; text-align: left; vertical-align: top; border-bottom: 1px solid #e8e8e8;
  overflow-wrap: anywhere; }
th { background: #f5f5f5; font-size: 0.85rem; white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
td.id, td.time { white-space: nowrap; }
img { display: block; width: 90px; height: auto; border-radius: 4px; }
.previews { display: flex; flex-wrap: wrap; gap: 12px; }
.previews figure { flex: 0 1 270px; max-width: 270px; margin: 0; }
.previews img { width: 100%; border: 1px solid #d6d6d6; border-radius: 8px; }
.previews figcaption { margin-top: 4px; font-size: 0.9rem; overflow-wrap: anywhere; }
.play { margin-top: 6px; padding: 8px 16px; min-height: 40px; font: inherit; color: #1b1b1b; background: #f5f5f5;
  border: 1px solid #b8b8b8; border-radius: 8px; cursor: pointer; }
.hint { margin-left: 8px; color: #666; font-size: 0.8rem; }
del { color: #b00020; }
tr.removed td, tr.unspoken td { background: #f4f4f4; color: #666; }
tr.cut td:first-child { box-shadow: inset 4px 0 0 #b00020; }
tr.kept td:first-child { box-shadow: inset 4px 0 0 #2e7d32; }
.tag { font-weight: 700; }
@media (max-width: 640px) {
  table, thead, tbody, tr, td { display: block; }
  thead { display: none; }
  tr { border-bottom: 1px solid #d6d6d6; padding: 6px 0; }
  tr:last-child { border-bottom: 0; }
  td { border-bottom: 0; padding: 4px 12px; }
  td:empty { display: none; }
  td::before { content: attr(data-label); display: block; color: #666; font-size: 0.8rem; }
  tr.cut td:first-child, tr.kept td:first-child { box-shadow: none; }
  tr.cut { box-shadow: inset 4px 0 0 #b00020; }
  tr.kept { box-shadow: inset 4px 0 0 #2e7d32; }
  .previews { flex-direction: column; }
}
"""


# The page's one script: a click on a play button decodes the clip in data-clip (the part after the first
# comma, base64) and plays it through a BufferSource; the button reads ■ 停止 meanwhile and a click stops
# it; a clip that does not decode leaves 播不出來. One AudioContext, made and resumed inside the click.
_PLAYER_JS = """\
(function () {
  "use strict";
  var PLAY = "▶ 播放", STOP = "■ 停止", FAILED = "播不出來";
  var context = null;
  var now = null;  // {button, source}: the clip that is playing, or still being decoded
  var turn = 0;  // counts clicks, so that a clip decoded after it was stopped is not started

  function stop() {
    turn += 1;
    if (!now) return;
    var was = now;
    now = null;
    was.button.textContent = PLAY;
    if (was.source) {
      was.source.onended = null;
      try { was.source.stop(); } catch (error) { /* it had already ended */ }
    }
  }

  function bytes(uri) {
    var text = atob(uri.slice(uri.indexOf(",") + 1));
    var data = new Uint8Array(text.length);
    for (var i = 0; i < text.length; i += 1) data[i] = text.charCodeAt(i);
    return data.buffer;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("button.play");
    if (!button) return;
    var again = now !== null && now.button === button;
    stop();
    if (again) return;
    var mine = { button: button, source: null }, mineTurn = turn;
    now = mine;
    button.textContent = STOP;
    function fail() {
      if (turn !== mineTurn) return;
      now = null;
      button.textContent = FAILED;
    }
    try {
      if (!context) context = new (window.AudioContext || window.webkitAudioContext)();
      context.resume();
      context.decodeAudioData(bytes(button.getAttribute("data-clip")), function (buffer) {
        if (turn !== mineTurn) return;
        var source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        source.onended = function () {
          if (now === mine) {
            now = null;
            button.textContent = PLAY;
          }
        };
        mine.source = source;
        source.start(0);
      }, fail);
    } catch (error) {
      fail();
    }
  });
})();
"""


def _esc(text: object) -> str:
    return html.escape(str(text))


def _audio(uri: str) -> str:
    """The play button of a clip, which the script at the end of the page plays, and the hint after it."""
    return (f'<button type="button" class="play" data-clip="{uri}">▶ 播放</button>'
            f'<span class="hint">{SILENT_HINT}</span>')


def _td(column: str, content: str, css_class: str = "") -> str:
    """A table cell labelled with its column, which a phone shows in front of it."""
    attribute = f' class="{css_class}"' if css_class else ""
    return f'<td{attribute} data-label="{column}">{content}</td>'


def _item_html(item: _ListItem) -> str:
    now = f' <span class="now">{item.now}</span>' if item.now else ""
    details = "".join(f'\n<p class="detail">{_esc(line)}</p>' for line in item.details)
    if item.note:
        details += f'\n<p class="note">{_esc(item.note)}</p>'
    return (
        f'<li data-item="{item.number}"><p class="line"><span class="num">{item.number}</span> '
        f'<span class="time">{item.new_time}</span> <span class="what">{_esc(item.what)}</span>'
        f"「{_esc(item.text)}」{now}</p>{details}\n{_audio(item.clip.uri)}</li>"
    )


def _list_html(items: list[_ListItem], empty: str) -> str:
    if not items:
        return f'<p class="none">{empty}</p>'
    return '<ol class="items">\n' + "\n".join(_item_html(item) for item in items) + "\n</ol>"


def _sentence_html(s: _Sentence) -> str:
    row_class = "" if s.state == "kept" else f' class="{s.state}"'
    if s.state == "kept":
        shown = _esc(" / ".join(s.lines)) or DASH
        meaning = f"{_esc(s.sem)} · {_esc(_WEIGHT_TEXT.get(s.weight, s.weight))}"
    else:
        shown = f"<del>{_esc(s.text)}</del>" if s.state == "removed" else _esc(s.text)
        meaning = DASH
    marks = []
    if s.state == "unspoken":
        marks.append('<span class="tag">沒講到</span>')
    elif s.state == "removed":
        marks.append('<span class="tag">移除</span>' + (f"（{_esc(s.note)}）" if s.note else ""))
    if s.long_word:
        marks.append(_LONG_WORD_MARK)
    if s.items:
        marks.append(_see(s.items))
    thumb = f'<img src="{s.thumb}" alt="">' if s.thumb else ""
    cells = [
        _td("列", _esc(s.id), "id"),
        _td("縮圖", thumb, "thumb"),
        _td("新時間", _esc(s.new_time) or DASH, "time"),
        _td("字幕", shown, "captions"),
        _td("語意", meaning),
        _td("推近", _esc(s.framing)),
        _td("動畫", _esc(s.animation)),
        _td("標記", "；".join(marks), "marks"),
    ]
    return f'<tr data-sentence="{_esc(s.id)}"{row_class}>' + "".join(cells) + "</tr>"


def _trim_marks(t: _Trim) -> list[str]:
    return ([] if t.in_reel else [_NOT_IN_REEL]) + ([_see(t.items)] if t.items else [])


def _trim_html(t: _Trim) -> str:
    text = DASH if not t.text else (f"<del>{_esc(t.text)}</del>" if t.cut else _esc(t.text))
    cells = [
        _td("編號", _esc(t.id), "id"),
        _td("種類", _esc(t.kind)),
        _td("原因", _esc(t.reason)),
        _td("辨識文字", text),
        _td("處理", "剪掉" if t.cut else "保留"),
        _td("標記", "；".join(_trim_marks(t)) + _audio(t.clip.uri), "marks"),
    ]
    return f'<tr data-trim="{_esc(t.id)}" class="{"cut" if t.cut else "kept"}">' + "".join(cells) + "</tr>"


def _table(css_class: str, headers: tuple[str, ...], rows: list[str]) -> str:
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = "\n".join(rows)
    return (f'<div class="scroll">\n<table class="{css_class}">\n<thead><tr>{head}</tr></thead>\n'
            f"<tbody>\n{body}\n</tbody>\n</table>\n</div>")


def _figures(plan: _Plan) -> list[tuple[str, str]]:
    """The summary: (label, value) for the source length, the new length, the pieces cut, the cut points and
    the shots punched in, the scenes, the decisions and the other places to listen to."""
    return [("原長", clock(plan.source_s)), ("新長", clock(plan.duration_s)), ("剪掉", f"{plan.cuts} 段"),
            ("推近", f"剪接點 {plan.cut_points} 個，放大 {plan.punched} 段"), ("動畫", f"{plan.scenes} 段"),
            ("要你決定", f"{len(plan.decisions)} 項"), ("其他要聽", f"{len(plan.listen)} 處")]


def _previews_html(previews: list[_Preview]) -> str:
    """The section 動畫的樣子, or nothing when there is no preview."""
    if not previews:
        return ""
    figures = "".join(
        f'<figure><img src="{preview.uri}" alt="{_esc(preview.label)}" width="{PREVIEW_W}" height="{PREVIEW_H}">'
        f"<figcaption>{_esc(preview.label)}</figcaption></figure>" for preview in previews)
    return f'{PREVIEWS_HEADING}\n<p>{PREVIEWS_ABOUT}</p>\n<div class="previews">{figures}</div>\n'


def _html(plan: _Plan) -> str:
    summary = "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in _figures(plan))
    warnings = ""
    if plan.warnings:
        items = "".join(f"<li>{_esc(text)}</li>" for _, text in plan.warnings)
        warnings = f'\n<ul class="warnings">{items}</ul>'
    sentences = _table("sentences", _SENTENCE_COLUMNS, [_sentence_html(s) for s in plan.sentences])
    if plan.trims:
        trims = _table("trims", _TRIM_COLUMNS, [_trim_html(t) for t in plan.trims])
    else:
        trims = "<p>沒有剪輯候選。</p>"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        f"<title>關卡 1 分鏡稿 {_esc(plan.project)}</title>\n<style>\n{_CSS}</style>\n</head>\n<body>\n"
        f'<h1>關卡 1 分鏡稿</h1>\n<p class="project">{_esc(plan.project)}</p>\n'
        f'<section class="summary">\n<dl>{summary}</dl>{warnings}\n</section>\n'
        f"<h2>要你決定</h2>\n{_list_html(plan.decisions, _NO_DECISIONS)}\n"
        f"<h2>其他要聽的地方</h2>\n{_list_html(plan.listen, _NO_LISTEN)}\n{_previews_html(plan.previews)}"
        f"<h2>句子</h2>\n{sentences}\n<h2>剪輯候選</h2>\n{trims}\n"
        f"<script>\n{_PLAYER_JS}</script>\n</body>\n</html>\n"
    )


# ---- storyboard.md ----------------------------------------------------------------------------


def _inline(text: str) -> str:
    """`text` on one line: line breaks become spaces."""
    return _NEWLINES.sub(" ", text).strip()


def _cell(text: str) -> str:
    """`text` for a Markdown table cell: on one line, with a pipe escaped."""
    return _inline(text).replace("|", "\\|")


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _list_md(items: list[_ListItem], empty: str) -> list[str]:
    """A numbered list with the page's numbers and the page's text, the detail lines as sub-items."""
    if not items:
        return [empty]
    lines = []
    for item in items:
        now = f" {item.now}" if item.now else ""
        lines.append(f"{item.number}. {item.new_time} {_inline(item.what)}「{_inline(item.text)}」{now}")
        lines += [f"   - {_inline(line)}" for line in item.details]
        if item.note:
            lines.append(f"   - {_inline(item.note)}")
    return lines


def _sentence_md(s: _Sentence) -> str:
    if s.state == "kept":
        shown = " / ".join(_cell(line) for line in s.lines)
        meaning = f"{_cell(s.sem)} · {_WEIGHT_TEXT.get(s.weight, s.weight)}"
        marks = []
    elif s.state == "removed":
        shown, meaning = f"~~{_cell(s.text)}~~", ""
        marks = ["移除" + (f"（{_cell(s.note)}）" if s.note else "")]
    else:
        shown, meaning, marks = _cell(s.text), "", ["沒講到"]
    if s.long_word:
        marks.append(_LONG_WORD_MARK)
    if s.items:
        marks.append(_see(s.items))
    return _row([s.id, s.new_time, shown, meaning, s.framing, _cell(s.animation), "；".join(marks)])


def _trim_md(t: _Trim) -> str:
    text = f"~~{_cell(t.text)}~~" if t.cut and t.text else _cell(t.text)
    return _row([t.id, _cell(t.kind), _cell(t.reason), text, "剪掉" if t.cut else "保留", "；".join(_trim_marks(t))])


def _markdown(plan: _Plan) -> str:
    columns = [column for column in _SENTENCE_COLUMNS if column != "縮圖"]
    lines = [
        f"# 關卡 1 分鏡稿：{plan.project}", "",
        *(f"- {label}：{value}" for label, value in _figures(plan)),
        *(f"- 警告：{text}" for _, text in plan.warnings),
        "", "## 要你決定", "", *_list_md(plan.decisions, _NO_DECISIONS),
        "", "## 其他要聽的地方", "", *_list_md(plan.listen, _NO_LISTEN),
        "", "## 句子", "",
        _row(columns), _row(["---"] * len(columns)),
        *(_sentence_md(s) for s in plan.sentences),
        "", "## 剪輯候選", "",
    ]
    if plan.trims:
        lines += [_row(list(_TRIM_COLUMNS)), _row(["---"] * len(_TRIM_COLUMNS)), *(_trim_md(t) for t in plan.trims)]
    else:
        lines.append("沒有剪輯候選。")
    return "\n".join(lines) + "\n"


# ---- the step ---------------------------------------------------------------------------------


def _write(p: Project, page: str, markdown: str) -> tuple[Path, Path]:
    """Replace review/gate1 with a folder that holds the two files."""
    folder = p.review / "gate1"
    if folder.exists() or folder.is_symlink():
        safe_rmtree(folder, allowed_root=p.dir)
    folder.mkdir(parents=True)
    page_path, md_path = folder / "index.html", folder / "storyboard.md"
    page_path.write_text(page, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return page_path, md_path


def _result(plan: _Plan, page_path: Path, md_path: Path) -> dict:
    return {
        "html": str(page_path),
        "md": str(md_path),
        "duration_s": round(plan.duration_s, 3),
        "source_s": round(plan.source_s, 3),
        "cuts": plan.cuts,
        "decisions": len(plan.decisions),
        "listen_points": len(plan.listen),
        "warnings": [code for code, _ in plan.warnings],
    }


def build_storyboard(p: Project, force: bool = True) -> dict:
    """Write review/gate1/index.html and review/gate1/storyboard.md, replacing the folder of an earlier
    run. Returns html, md (paths), duration_s (the cut reel), source_s, cuts (the accepted candidates in
    the reel plus the cuts.extra ranges), decisions and listen_points (how many items the two lists of
    review_items hold), warnings (the codes of probe.json and of trim.json, then LONG_WORD_WARNING when a
    caption line cuts a long word, the scene warnings, and STILLS_FAILED with its reason when the stills of the
    previews cannot be made) and skipped.

    The step is recorded with the edit.json it was made from under the input name "edit", which
    approve checks. A call from code rebuilds every time; the storyboard command passes its --force
    flag, so that it skips the work when no input has changed. An edit.json that does not pass
    raises EditInvalid before anything is touched. So does an edit.json that validate has not passed on
    the edit.json, transcript.json and trim.json that are there now, with a PipelineError (see edit_stage):
    the trim and sentence ids in it mean what the last two say. Bounds in trim.json that run backwards
    (see _check_bounds_order), a missing probe.json and a missing caption dictionary are PipelineErrors too,
    also before anything is touched.

    The caption word list and the dictionary (words.caption_inputs) are inputs of the step as well: the
    lines on the page are broken where they say a word ends, so a change to either makes the page stale
    and an approval of it void. So are faces.json and the card sources (scene_render.source_inputs) when a scene
    shows: the previews place the cards by the faces and draw them with that code. A storyboard is not skipped when
    the still of a preview is missing from work/scenes (it could not be made, or was removed), or when a scene shows
    and the page has no previews (the stills were made since): it is made again, so that the result says what the
    page says."""
    edit, transcript, trims = require_valid(p)
    stage = edit_stage(p)
    if stage == "edit":
        raise PipelineError(OLDER_FILES_REFUSAL)
    if stage == "validate":
        raise PipelineError(NOT_VALIDATED_REFUSAL)
    _check_bounds_order(transcript, trims)
    if not (p.work / "probe.json").is_file():  # run_step would only say that an input is not found
        raise PipelineError(PROBE_MISSING)
    style = load_style(edit["style"])
    probe = _document(p.work / "probe.json", "prep", ("video_source",))
    placed = [preview.placed for preview in
              _previews(p, edit, transcript, trims, style, Path(probe["video_source"]))]
    page_path, md_path = p.review / "gate1" / "index.html", p.review / "gate1" / "storyboard.md"
    inputs = {
        "edit": p.edit_path,
        "transcript": p.work / "transcript.json",
        "trim": p.work / "trim.json",
        "style": style["dir"] / "style.json",
        "audio": p.work / "audio48k.wav",
        "probe": p.work / "probe.json",
        **caption_inputs(),  # the caption lines on the page are broken where these say a word ends
        **({"faces": p.work / "faces.json", **source_inputs()} if placed else {}),  # where the cards go, how they look
    }
    # A page without its previews is made again: the stills may have been made since (by the review page).
    unmade = any(not still.is_file() for _, still in still_paths(p, placed, style)) or (
        bool(placed) and page_path.is_file() and PREVIEWS_HEADING not in page_path.read_text(encoding="utf-8"))

    def run() -> dict:
        # Read the documents again: run_step hashed the inputs just before, so what the page shows is
        # at least as new as the hash that approve compares.
        plan = _plan(p)
        _draw_stills(p, plan)
        _Media(p, plan.video).fill(plan)
        written = _write(p, _html(plan), _markdown(plan))
        return _result(plan, *written)

    done = run_step(p, "storyboard", inputs, [page_path, md_path], run, force=force or unmade)
    if done["skipped"]:
        done = {**_result(_plan(p), page_path, md_path), "skipped": True}
    return done

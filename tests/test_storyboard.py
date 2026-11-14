"""Gate 1 storyboard: the page, storyboard.md, the step record and the storyboard command.

edited_project is the Meijia project (conftest) with the edit.json helpers.default_edit makes for it:
every suggested cut accepted. Its video is a gray counter (frame N is gray 16 + 4 * (N % 50)), so a
thumbnail shows which frame it was taken from. Its review lists hold no decision and three places to
listen to: the head cut point (on s01), the repeat t05 whose cut point is in speech and the tail cut point
(both on s04). Tests that need a sentence the speaker skipped or one that is listed change the project's
transcript.json or build their own project with helpers.edited_project_from_documents."""

import base64
import json
import re
import shutil
import subprocess
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path

import cv2
import numpy as np
import pytest
import soundfile as sf
from helpers import (
    default_edit,
    edited_project_from_documents,
    edited_project_from_script,
    timed_sentence,
    transcript_and_trims,
    validated_storyboard,
)

from pipeline import storyboard as storyboard_module
from pipeline import words
from pipeline.assets import dictionary_path
from pipeline.captions import LONG_WORD_WARNING
from pipeline.cli import main
from pipeline.edit import build_segments, map_time, total_duration, validate_project
from pipeline.errors import PipelineError
from pipeline.media import ffprobe_bin
from pipeline.project import file_sha256, load_state, save_state
from pipeline.review_items import review_items
from pipeline.storyboard import build_storyboard
from pipeline.textnorm import split_units

FPS = 30
THREE = "第一句話。第二句話。第三句話。"


# ---- helpers ----------------------------------------------------------------------------------


def read_json(p, name: str) -> dict:
    return json.loads((p.work / name).read_text(encoding="utf-8"))


def write_json(p, name: str, document: dict) -> None:
    (p.work / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page(p) -> str:
    return Path(validated_storyboard(p)["html"]).read_text(encoding="utf-8")


def set_cut_listen(p, **flags: bool) -> None:
    """Set trim.json bounds[sid].needs_listen of every sentence: False, except the given ones. The Meijia
    fixture has it on s01 and s04, which these tests set apart so that they test their own subject."""
    trims = read_json(p, "trim.json")
    for sid, bound in trims["bounds"].items():
        bound["needs_listen"] = flags.get(sid, False)
    write_json(p, "trim.json", trims)


def set_trim_listen(p, *tids: str) -> None:
    """Set trim.json needs_listen of every candidate: False, except the given ones. The Meijia fixture has it
    on t05."""
    trims = read_json(p, "trim.json")
    for trim in trims["trims"]:
        trim["needs_listen"] = trim["id"] in tids
    write_json(p, "trim.json", trims)


def remove_range(p, first: str, last: str, reason: str = "off topic") -> None:
    """Add the cuts.extra range first..last to edit.json and take its sentences off the sentence list."""
    ids = [s["id"] for s in read_json(p, "transcript.json")["sentences"]]
    gone = set(ids[ids.index(first):ids.index(last) + 1])
    edit = read_json(p, "edit.json")
    edit["cuts"]["extra"] = [*edit["cuts"].get("extra", []), {"from": first, "to": last, "reason": reason}]
    edit["sentences"] = [s for s in edit["sentences"] if s["id"] not in gone]
    write_json(p, "edit.json", edit)


def clip_seconds(tmp_path: Path, row: str) -> list[float]:
    """The durations of the audio clips embedded in `row`, in order."""
    return [probe_clip(tmp_path, data)["duration"] for data in embedded(row, "audio/mp4")]


def span_seconds(p, start: float, end: float) -> float:
    """The length of the audio from `start` to `end`, kept inside the audio of the project."""
    return min(sf.info(str(p.work / "audio48k.wav")).duration, end) - max(0.0, start)


def around(p, t: float, pad: float) -> float:
    """The length of the audio from t - pad to t + pad, kept inside the audio of the project."""
    return span_seconds(p, t - pad, t + pad)


def segments_of(p) -> list:
    transcript, trims, edit = read_json(p, "transcript.json"), read_json(p, "trim.json"), read_json(p, "edit.json")
    return build_segments(edit, transcript, trims)


def new_time(p, t: float) -> str:
    """Source time `t` as the page shows it: m:ss.s in the cut reel of the project as it is now."""
    return storyboard_module.clock(map_time(t, segments_of(p)))


def items_of(p) -> tuple[list, list]:
    """(decisions, listen items) of review_items for the project as it is now."""
    transcript, trims, edit = read_json(p, "transcript.json"), read_json(p, "trim.json"), read_json(p, "edit.json")
    return review_items(edit, transcript, trims, build_segments(edit, transcript, trims))


def sentence_row(html: str, sid: str) -> str:
    found = re.search(rf'<tr data-sentence="{sid}"[^>]*>.*?</tr>', html, re.S)
    assert found, f"no row for sentence {sid}"
    return found.group(0)


def trim_row(html: str, tid: str) -> str:
    found = re.search(rf'<tr data-trim="{tid}"[^>]*>.*?</tr>', html, re.S)
    assert found, f"no row for trim {tid}"
    return found.group(0)


PLAYER = re.compile(r'<button type="button" class="play"[^>]*>[^<]*</button><span class="hint">[^<]*</span>')


def plain(fragment: str) -> str:
    """The text of an HTML fragment: the player (its button and hint) left out, the tags dropped, the
    character references read."""
    return unescape(re.sub(r"<[^>]+>", "", PLAYER.sub("", fragment)))


def cell(row: str, column: str) -> str:
    """The text of the cell of `row` in the column `column` (its data-label)."""
    found = re.search(rf'<td[^>]* data-label="{column}">(.*?)</td>', row, re.S)
    assert found, f"no {column} cell"
    return plain(found.group(1))


def marks(row: str) -> str:
    return cell(row, "標記")


def item_numbers(html: str) -> list[int]:
    return [int(number) for number in re.findall(r'<li data-item="(\d+)">', html)]


def item_li(html: str, number: int) -> str:
    found = re.search(rf'<li data-item="{number}">.*?</li>', html, re.S)
    assert found, f"no item {number}"
    return found.group(0)


def item_lines(html: str, number: int) -> list[str]:
    """The lines of text of list item `number`: the item line, then its detail lines."""
    return [line for line in plain(item_li(html, number)).splitlines() if line.strip()]


def section(md: str, heading: str) -> list[str]:
    """The non-empty lines of storyboard.md under `## heading`, up to the next heading."""
    lines = md.splitlines()
    start = lines.index(f"## {heading}") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines))
    return [line for line in lines[start:end] if line.strip()]


def embedded(row: str, mime: str) -> list[bytes]:
    """What the data URIs of type `mime` in `row` hold."""
    return [base64.b64decode(data) for data in re.findall(rf"data:{mime};base64,([A-Za-z0-9+/=]+)", row)]


def probe_clip(tmp_path: Path, data: bytes) -> dict:
    """ffprobe's codec, channels, bit rate and duration of an embedded audio clip."""
    clip = tmp_path / "clip.m4a"
    clip.write_bytes(data)
    done = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_name,channels,bit_rate:format=duration",
         "-of", "json", str(clip)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(done.stdout)
    return {**info["streams"][0], "duration": float(info["format"]["duration"])}


def gray_frame(data: bytes) -> tuple[int, float]:
    """(the frame number mod 50 that the gray level of a thumbnail says it was taken from, the width of
    the thumbnail). A JPEG is full range: video luma 16..235 is stored as 0..255."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    gray = float(image.mean())
    return round(gray * 219 / 255 / 4) % 50, image.shape[1]


def cells(line: str) -> list[str]:
    """The cells of a Markdown table row; an escaped pipe does not end a cell."""
    return re.split(r"(?<!\\)\|", line.strip())[1:-1]


def table_rows(md: str, first_cell: str) -> list[str]:
    return [line for line in md.splitlines() if line.startswith(f"| {first_cell}")]


class Balanced(HTMLParser):
    """Checks with html.parser that every tag that is opened is closed in order."""

    VOID = {"meta", "img", "br", "link", "hr", "input", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.problems: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.problems.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'}")


# ---- the page ---------------------------------------------------------------------------------


def test_html_is_light_and_self_contained(edited_project):
    html = (Path(validated_storyboard(edited_project)["html"])).read_text(encoding="utf-8")
    assert '<html lang="zh-Hant">' in html and 'content="light"' in html
    assert not re.search(r'(src|href)="https?://', html)
    assert "data:image/jpeg;base64," in html and "data:audio/mp4;base64," in html


def test_html_has_no_external_reference_of_any_kind(edited_project):
    html = page(edited_project)
    assert not re.search(r"https?://|url\(|@import|<link|<script\b[^>]*\bsrc\b", html)


def test_html_is_well_formed_and_made_for_a_phone(edited_project):
    html = page(edited_project)
    parser = Balanced()
    parser.feed(html)
    parser.close()
    assert parser.problems == [] and parser.stack == []
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert html.startswith("<!DOCTYPE html>")
    assert parser.tags.count("button") >= 1
    assert html.count('class="play"') == html.count(
        '<button type="button" class="play" data-clip="data:audio/mp4;base64,')
    assert html.count('<div class="scroll">') == html.count("<table") == 2  # each table scrolls on its own


# ---- the player and the notes -----------------------------------------------------------------

# The player script run by node against a made-up page: a click handler, a fake AudioContext and fake
# buttons. argv: the script file, a data URI of a clip. It prints what the buttons and the context did.
PLAYER_HARNESS = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[2], "utf8");
const uri = process.argv[3];
let contexts = 0, handler = null, mode = "ok", held = null;
const log = [], sources = [];
class FakeContext {
  constructor() { contexts += 1; this.destination = { name: "speakers" }; }
  resume() { log.push("resume"); return Promise.resolve(); }
  decodeAudioData(data, ok, bad) {
    log.push(["decode", data.byteLength, Buffer.from(data).subarray(4, 8).toString("latin1")]);
    if (mode === "ok") ok({ duration: 1 });
    else if (mode === "hold") held = () => ok({ duration: 1 });
    else bad(new Error("cannot decode"));
  }
  createBufferSource() {
    const s = { buffer: null, onended: null, started: 0, stopped: 0,
      connect(to) { this.to = to; }, start() { this.started += 1; }, stop() { this.stopped += 1; } };
    sources.push(s);
    return s;
  }
}
const window = { AudioContext: FakeContext };
const document = { addEventListener(type, fn) { if (type === "click") handler = fn; } };
const button = () => ({ textContent: "▶ 播放", getAttribute: (n) => (n === "data-clip" ? uri : null),
                        closest() { return this; } });
new Function("window", "document", source)(window, document);
const click = (el) => handler({ target: el });
const a = button(), b = button(), out = {};
click({ closest: () => null });
click({});
out.ignored = [contexts, sources.length];
click(a);
out.playing = [a.textContent, sources[0].started, sources[0].to && sources[0].to.name, sources[0].buffer !== null];
out.decoded = log.filter((entry) => Array.isArray(entry));
click(a);
out.stopped = [a.textContent, sources[0].stopped];
click(a);
sources[1].onended();
out.ended = a.textContent;
click(a);
click(b);
out.switched = [a.textContent, sources[2].stopped, b.textContent, sources[3].started];
click(b);
mode = "fail";
click(a);
out.failed = a.textContent;
mode = "hold";
click(a);
click(a);
held();
out.cancelled = [a.textContent, sources.length];
out.contexts = contexts;
out.resumes = log.filter((entry) => entry === "resume").length;
console.log(JSON.stringify(out));
"""


def player_script(html: str) -> str:
    return re.search(r"<script>(.*?)</script>", html, re.S).group(1)


def test_the_page_has_no_audio_element_and_one_player_script(edited_project):
    html = page(edited_project)
    assert "<audio" not in html and html.count("<script>") == 1 and "decodeAudioData" in html
    assert html.index("<script>") > html.index("<h2>剪輯候選</h2>") and html.endswith("</script>\n</body>\n</html>\n")
    assert not re.search(r"fetch\(|XMLHttpRequest|WebSocket|sendBeacon|importScripts|import\(", html)


def test_every_play_button_has_the_silent_mode_hint(edited_project):
    html = page(edited_project)
    hint = storyboard_module.SILENT_HINT
    assert hint == "沒有聲音的話，請關掉靜音模式"
    assert html.count('class="play"') == html.count(hint) > 0
    button = ('<button type="button" class="play" data-clip="data:audio/mp4;base64,[A-Za-z0-9+/=]+">▶ 播放</button>'
              f'<span class="hint">{hint}</span>')
    assert len(re.findall(button, html)) == html.count('class="play"')


def test_the_player_decodes_the_clip_in_the_page_and_plays_it_through_web_audio(edited_project, tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    html = page(edited_project)
    (tmp_path / "player.js").write_text(player_script(html), encoding="utf-8")
    (tmp_path / "harness.js").write_text(PLAYER_HARNESS, encoding="utf-8")
    uri = re.search(r'data-clip="(data:audio/mp4;base64,[A-Za-z0-9+/=]+)"', html).group(1)
    done = subprocess.run(
        ["node", str(tmp_path / "harness.js"), str(tmp_path / "player.js"), uri],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    out = json.loads(done.stdout)
    size = len(base64.b64decode(uri.split(",", 1)[1]))
    assert out["ignored"] == [0, 0]  # a click that is not on a play button does nothing
    assert out["playing"] == ["■ 停止", 1, "speakers", True]  # decoded, played through the context's output
    assert out["decoded"] == [["decode", size, "ftyp"]]  # the bytes after the first comma, decoded from base64
    assert out["stopped"] == ["▶ 播放", 1]  # a click on a playing button stops it
    assert out["ended"] == "▶ 播放"  # a clip that ends puts its button back
    assert out["switched"] == ["▶ 播放", 1, "■ 停止", 1]  # a second button takes over from the first
    assert out["failed"] == "播不出來"  # a clip that does not decode
    assert out["cancelled"] == ["▶ 播放", 4]  # stopped while still decoding: it never starts
    assert out["contexts"] == 1 and out["resumes"] >= 1  # one AudioContext, resumed by the click


NOTE_S03 = "建議聽一下這一句。因為字幕用的是辨識出來的字，可能有錯字。"
NOTE_T05 = "建議保留這一段。因為 <b>這是強調</b> & 不是重講。"


def test_a_note_in_edit_json_shows_under_its_item_on_the_page_and_in_the_markdown(edited_project):
    p = edited_project
    show_s03_as_recognised(p)  # item 1 is s03 with the recognised text; 2, 3, 4 are the head, t05 and the tail
    edit = read_json(p, "edit.json")
    edit["notes"] = {"s03": NOTE_S03, "t05": NOTE_T05}
    write_json(p, "edit.json", edit)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    first = item_li(html, 1)
    assert first.index("腳本：") < first.index(f'<p class="note">{NOTE_S03}</p>') < first.index('class="play"')
    third = item_li(html, 3)
    assert f'<p class="note">{escape(NOTE_T05)}</p>' in third and "<b>" not in html
    assert 'class="note"' not in item_li(html, 2) and 'class="note"' not in item_li(html, 4)
    assert html.count('class="note"') == 2
    assert item_lines(html, 1)[-1] == NOTE_S03 and item_lines(html, 3)[-1] == NOTE_T05
    decisions, listen = section(md, "要你決定"), section(md, "其他要聽的地方")
    assert decisions[-2:] == ["   - 腳本：這個方法可以省下 150 分鐘。", f"   - {NOTE_S03}"]
    assert [line for line in listen if line.startswith("   - ")] == [f"   - {NOTE_T05}"]
    assert listen[listen.index(f"   - {NOTE_T05}") - 1].startswith("3. ")


NOTE_LOW_MATCH = "建議聽一下這一句。因為講的和腳本差很多，<字幕> 用腳本的字。"
NOTE_CUT_POINT = "建議聽一下前後。因為切點太靠近字。"


def test_a_note_on_a_low_match_sentence_shows_under_its_item_on_the_page_and_in_the_markdown(edited_project):
    p = edited_project
    set_cut_listen(p)
    set_trim_listen(p)
    transcript = read_json(p, "transcript.json")
    transcript["sentences"][1].update(needs_listen=True, reasons=["low_match"])
    write_json(p, "transcript.json", transcript)
    edit = read_json(p, "edit.json")
    edit["notes"] = {"s02": NOTE_LOW_MATCH}
    write_json(p, "edit.json", edit)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert f'<p class="note">{escape(NOTE_LOW_MATCH)}</p>' in item_li(html, 1) and html.count('class="note"') == 1
    assert item_lines(html, 1)[-1] == NOTE_LOW_MATCH
    listen = section(md, "其他要聽的地方")
    assert listen[0].startswith("1. ") and "吻合度低" in listen[0]
    assert listen[1:] == [f"   - {NOTE_LOW_MATCH}"]


def test_a_note_on_a_sentence_shows_under_its_cut_point_item_too(edited_project):
    p = edited_project
    set_cut_listen(p, s01=True)
    set_trim_listen(p)
    edit = read_json(p, "edit.json")
    edit["notes"] = {"s01": NOTE_CUT_POINT}
    write_json(p, "edit.json", edit)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert item_lines(html, 1) == ["1 0:00.0 片頭切點「今天我們來聊短影音剪輯。」", NOTE_CUT_POINT]
    assert section(md, "其他要聽的地方")[-1] == f"   - {NOTE_CUT_POINT}"


def test_a_candidate_item_shows_the_note_of_its_candidate_not_the_one_of_its_sentence(edited_project):
    p = edited_project
    set_cut_listen(p)
    edit = read_json(p, "edit.json")
    sentence = next(t["sentence"] for t in read_json(p, "trim.json")["trims"] if t["id"] == "t05")
    edit["notes"] = {sentence: NOTE_S03, "t05": NOTE_T05}
    write_json(p, "edit.json", edit)
    html = Path(validated_storyboard(p)["html"]).read_text(encoding="utf-8")
    assert [line for line in plain(item_li(html, 1)).splitlines() if line.strip()][-1] == NOTE_T05
    assert html.count('class="note"') == 1


def test_an_item_without_a_note_shows_none(edited_project):
    result = validated_storyboard(edited_project)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert 'class="note"' not in html
    assert not [line for line in section(Path(result["md"]).read_text(encoding="utf-8"), "其他要聽的地方")
                if line.startswith("   - ")]


def test_rows_for_sentences_and_trims(edited_project):
    html = Path(validated_storyboard(edited_project)["html"]).read_text(encoding="utf-8")
    T = json.loads((edited_project.work / "transcript.json").read_text())
    TR = json.loads((edited_project.work / "trim.json").read_text())
    assert all(f'data-sentence="{s["id"]}"' in html for s in T["sentences"])
    assert all(f'data-trim="{t["id"]}"' in html for t in TR["trims"])
    assert html.count('class="cut"') == len([t for t in TR["trims"] if t["suggest"] == "cut"])


def test_sentence_rows_come_in_script_order_and_trims_in_time_order(edited_project):
    html = page(edited_project)
    assert re.findall(r'data-sentence="(s\d+)"', html) == ["s01", "s02", "s03", "s04"]
    assert re.findall(r'data-trim="(t\d+)"', html) == ["t01", "t02", "t03", "t04", "t05"]
    assert html.index('data-sentence="s04"') < html.index('data-trim="t01"')


def test_every_spoken_sentence_has_a_270_wide_thumbnail_of_its_midpoint(edited_project):
    p = edited_project
    html = page(p)
    for sentence in read_json(p, "transcript.json")["sentences"]:
        thumbnails = embedded(sentence_row(html, sentence["id"]), "image/jpeg")
        assert len(thumbnails) == 1, sentence["id"]
        frame, width = gray_frame(thumbnails[0])
        middle = round((sentence["start"] + sentence["end"]) / 2 * FPS)
        assert width == 270
        assert min((frame - middle) % 50, (middle - frame) % 50) <= 1, (sentence["id"], frame, middle % 50)


def test_new_time_is_the_mapped_sentence_start(edited_project):
    p = edited_project
    transcript, trims, edit = read_json(p, "transcript.json"), read_json(p, "trim.json"), read_json(p, "edit.json")
    segments = build_segments(edit, transcript, trims)
    html = page(p)
    for sentence in transcript["sentences"]:
        seconds = map_time(sentence["start"], segments)
        expected = f"{int(seconds // 60)}:{seconds % 60:04.1f}"
        assert f'<td class="time" data-label="新時間">{expected}</td>' in sentence_row(html, sentence["id"]), (
            sentence["id"], seconds)


def test_clock_is_minutes_seconds_and_tenths():
    assert storyboard_module.clock(0) == "0:00.0"
    assert storyboard_module.clock(7.34) == "0:07.3"
    assert storyboard_module.clock(75.26) == "1:15.3"
    assert storyboard_module.clock(59.96) == "1:00.0"  # the tenth carries into the minute
    assert storyboard_module.clock(600) == "10:00.0"


def test_sentence_row_shows_caption_lines_tag_and_weight(edited_project):
    p = edited_project
    edit = read_json(p, "edit.json")
    edit["sentences"][1].update(sem="steps", weight="sub")
    write_json(p, "edit.json", edit)
    html = page(p)
    s01, s02 = sentence_row(html, "s01"), sentence_row(html, "s02")
    assert "第一步是把停頓剪掉　讓節奏變快" in s02  # a comma inside a line is a wide space; the full stop goes
    assert "steps" in s02 and "次要" in s02
    assert "claim" in s01 and "主要" in s01


def test_caption_lines_are_joined_with_a_slash(tmp_path):
    long_sentence = "這是一個很長很長的句子，需要被切成兩行才能放進畫面裡面。"
    p = edited_project_from_script(tmp_path, long_sentence)
    row = sentence_row(page(p), "s01")
    shown = re.search(r"<td class=\"captions\" data-label=\"字幕\">(.*?)</td>", row, re.S).group(1)
    assert " / " in shown and shown.count(" / ") == 1


# ---- escaping ---------------------------------------------------------------------------------


def test_html_escapes_script_text(tmp_path):
    p = edited_project_from_script(tmp_path, "我們用 <b> & 「引號」來測試。")
    html = Path(validated_storyboard(p)["html"]).read_text(encoding="utf-8")
    assert "&lt;b&gt; &amp;" in html and "<b> &" not in html


def test_html_escapes_trim_and_edit_text(edited_project):
    p = edited_project
    trims = read_json(p, "trim.json")
    trims["trims"][1].update(text='<i>&"\'', reason="<u>x</u>")
    write_json(p, "trim.json", trims)
    edit = read_json(p, "edit.json")
    edit["cuts"]["extra"] = [{"from": "s03", "to": "s03", "reason": "<script>alert(1)</script>"}]
    edit["sentences"] = [s for s in edit["sentences"] if s["id"] != "s03"]
    write_json(p, "edit.json", edit)
    html = page(p)
    assert "&lt;i&gt;&amp;&quot;&#x27;" in html and "&lt;u&gt;x&lt;/u&gt;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert not re.search(r"<(i|u)>", html) and html.count("<script>") == 1  # the one script is the player


def test_md_escapes_pipes_and_newlines(edited_project):
    p = edited_project
    trims = read_json(p, "trim.json")
    trims["trims"][1].update(text="第一步|是\n停頓", reason="a|b\r\nc")
    write_json(p, "trim.json", trims)
    md = Path(validated_storyboard(p)["md"]).read_text(encoding="utf-8")
    rows = table_rows(md, "t")
    assert len(rows) == 5 and {len(cells(row)) for row in rows} == {6}
    assert "第一步\\|是 停頓" in md and "a\\|b c" in md


def test_md_escapes_a_pipe_in_a_caption(tmp_path):
    p = edited_project_from_script(tmp_path, "我們用 a|b 來測試。")
    md = Path(validated_storyboard(p)["md"]).read_text(encoding="utf-8")
    (row,) = table_rows(md, "s01")
    assert len(cells(row)) == 5 and "a\\|b" in row


# ---- warnings and the summary -----------------------------------------------------------------


def test_the_few_pauses_warning_is_shown_in_chinese(edited_project):
    p = edited_project
    trims = read_json(p, "trim.json")
    trims["warnings"] = ["few pauses: quiet-point detection unreliable"]
    write_json(p, "trim.json", trims)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert "停頓很少，切點是否在靜音裡的判斷不可靠" in html
    assert result["warnings"] == ["few pauses: quiet-point detection unreliable"]


def test_a_clean_project_shows_no_warning(edited_project):
    result = validated_storyboard(edited_project)
    assert result["warnings"] == []
    assert "class=\"warnings\"" not in Path(result["html"]).read_text(encoding="utf-8")


def test_storyboard_does_not_read_faces_json(edited_project):
    p = edited_project
    (p.work / "faces.json").unlink()
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert Path(result["md"]).is_file() and result["warnings"] == []
    assert "偵測不到臉" not in html and "下巴" not in html and "class=\"warnings\"" not in html


def test_storyboard_ignores_what_faces_json_holds(edited_project):
    p = edited_project
    (p.work / "faces.json").write_text("not json", encoding="utf-8")
    assert validated_storyboard(p)["warnings"] == []


def test_changing_faces_json_does_not_make_the_storyboard_stale(edited_project):
    p = edited_project
    validated_storyboard(p)
    faces = read_json(p, "faces.json")
    faces["boxes"] = [{"t": 2.0, "x0": 0.3, "y0": 0.6, "x1": 0.7, "y1": 0.97, "score": 0.9}]
    write_json(p, "faces.json", faces)
    assert build_storyboard(p, force=False)["skipped"] is True


def a_copy_of_the_caption_word_list(tmp_path, monkeypatch) -> Path:
    """A copy of caption_words.txt in `tmp_path` that words.WORDS_FILE points at for the rest of the test."""
    word_list = tmp_path / "caption_words.txt"
    shutil.copyfile(words.WORDS_FILE, word_list)
    monkeypatch.setattr(words, "WORDS_FILE", word_list)
    return word_list


def test_a_changed_caption_word_list_rebuilds_the_page(edited_project, tmp_path, monkeypatch):
    # break_lines reads the word list and the dictionary, so the page shows lines that depend on both
    p = edited_project
    word_list = a_copy_of_the_caption_word_list(tmp_path, monkeypatch)
    validated_storyboard(p)
    inputs = load_state(p)["steps"]["storyboard"]["inputs"]
    assert inputs["words"]["path"] == str(word_list)
    assert inputs["dictionary"]["path"] == str(dictionary_path())
    assert build_storyboard(p, force=False)["skipped"] is True
    with open(word_list, "a", encoding="utf-8") as stream:
        stream.write("分鏡腳本\n")
    assert build_storyboard(p, force=False)["skipped"] is False


def test_the_storyboard_without_the_caption_dictionary_says_run_setup(tmp_path, monkeypatch):
    # every caption here fits one line, so no line needs the dictionary: it is an input all the same
    transcript, trims = transcript_and_trims(THREE, asr="第一句話第二句話第三句話")
    p = edited_project_from_documents(tmp_path, transcript, trims)
    validate_project(p)
    monkeypatch.setattr(words, "dictionary_path", lambda: tmp_path / "absent.txt.big")
    with pytest.raises(PipelineError) as error:
        build_storyboard(p)
    assert error.value.message == "caption dictionary not found: run `uv run pipeline setup`"
    assert not (p.review / "gate1").exists()


def test_a_clip_that_is_not_9_16_says_so_in_chinese(tmp_path):
    transcript, trims = transcript_and_trims(THREE, asr="第一句話第二句話第三句話")
    p = edited_project_from_documents(tmp_path, transcript, trims, size=(1920, 1080))
    assert read_json(p, "probe.json")["warnings"] == ["aspect 16:9 will be center-cropped to 9:16"]
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert "<li>畫面不是 9:16，會置中裁切成 9:16</li>" in html and "center-cropped" not in html
    assert result["warnings"] == ["aspect 16:9 will be center-cropped to 9:16"]
    assert "- 警告：畫面不是 9:16，會置中裁切成 9:16" in Path(result["md"]).read_text(encoding="utf-8")


def test_any_other_probe_warning_is_passed_through_escaped(edited_project):
    p = edited_project
    probe = read_json(p, "probe.json")
    probe["warnings"] = ["odd <thing> & more"]
    write_json(p, "probe.json", probe)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert "<li>odd &lt;thing&gt; &amp; more</li>" in html and "<thing>" not in html
    assert result["warnings"] == ["odd <thing> & more"]
    assert "- 警告：odd <thing> & more" in Path(result["md"]).read_text(encoding="utf-8")


def test_result_summary(edited_project):
    p = edited_project
    transcript, trims, edit = read_json(p, "transcript.json"), read_json(p, "trim.json"), read_json(p, "edit.json")
    result = validated_storyboard(p)
    segments = build_segments(edit, transcript, trims)
    assert Path(result["html"]) == p.review / "gate1" / "index.html" and Path(result["html"]).is_file()
    assert Path(result["md"]) == p.review / "gate1" / "storyboard.md" and Path(result["md"]).is_file()
    assert result["duration_s"] == round(total_duration(segments), 3)
    assert result["source_s"] == read_json(p, "probe.json")["duration_s"]
    assert result["cuts"] == 5
    assert result["decisions"] == 0 and result["listen_points"] == 3  # the head, t05 and the tail
    assert result["warnings"] == []
    assert set(result) == {"html", "md", "duration_s", "source_s", "cuts", "decisions", "listen_points", "warnings",
                           "skipped"}
    json.dumps(result)  # the command prints it


def test_summary_counts_accepted_candidates_and_extra_ranges(edited_project):
    p = edited_project
    edit = read_json(p, "edit.json")
    edit["cuts"]["reject"] = edit["cuts"]["accept"][3:]
    edit["cuts"]["accept"] = edit["cuts"]["accept"][:3]
    edit["cuts"]["extra"] = [{"from": "s03", "to": "s03", "reason": "off topic"}]
    edit["sentences"] = [s for s in edit["sentences"] if s["id"] != "s03"]
    write_json(p, "edit.json", edit)
    result = validated_storyboard(p)
    assert result["cuts"] == 3 + 1
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert "<dd>4 段</dd>" in html
    assert f"<dd>{storyboard_module.clock(result['duration_s'])}</dd>" in html
    assert f"<dd>{storyboard_module.clock(result['source_s'])}</dd>" in html


# ---- candidates -------------------------------------------------------------------------------


def test_trim_rows_show_kind_reason_text_and_decision(edited_project):
    p = edited_project
    html = page(p)
    labels = {"filler": "贅字", "repeat": "重講", "retake": "NG 段", "pause": "停頓"}
    trims = read_json(p, "trim.json")["trims"]
    assert {t["kind"] for t in trims} == set(labels)  # the Meijia fixture has one of each
    for trim in trims:
        row = trim_row(html, trim["id"])
        assert row.startswith(f'<tr data-trim="{trim["id"]}" class="cut">') and "剪掉" in row
        kind_cell = re.findall(r"<td[^>]*>(.*?)</td>", row)[1]
        assert kind_cell == labels[trim["kind"]] and f'<td data-label="原因">{escape(trim["reason"])}</td>' in row
        assert (f"<del>{escape(trim['text'])}</del>" in row) == bool(trim["text"])


def test_rejected_trim_is_kept_and_not_struck_through(edited_project):
    p = edited_project
    edit = read_json(p, "edit.json")
    edit["cuts"]["accept"].remove("t03")
    edit["cuts"]["reject"].append("t03")
    write_json(p, "edit.json", edit)
    row = trim_row(page(p), "t03")
    assert 'class="kept"' in row and 'class="cut"' not in row
    assert "<del>" not in row and "嗯" in row


def test_reworded_retake_is_called_a_reworded_retake(edited_project):
    p = edited_project
    trims = read_json(p, "trim.json")
    trims["trims"][1]["suggest"] = "list"
    write_json(p, "trim.json", trims)
    edit = read_json(p, "edit.json")
    edit["cuts"]["accept"].remove("t02")
    edit["cuts"]["reject"].append("t02")
    write_json(p, "edit.json", edit)
    html = page(p)
    row = trim_row(html, "t02")
    assert cell(row, "種類") == "改詞重講" and "NG 段" not in row and 'class="kept"' in row
    assert marks(row) == "見第 1 項"  # a reworded take in the reel is a decision
    start = read_json(p, "trim.json")["trims"][1]["start"]
    assert item_lines(html, 1) == [f"1 {new_time(p, start)} 改詞重講「第一步是把停頓」 目前：保留"]


def test_only_an_accepted_trim_whose_cut_is_not_in_silence_is_a_listen_item(edited_project):
    p = edited_project
    set_cut_listen(p)
    set_trim_listen(p, "t01")  # the pause t01 has no text
    html = page(p)
    assert item_numbers(html) == [1]
    assert item_lines(html, 1) == [f"1 {new_time(p, 2.8)} 剪接處不在靜音裡「—」 目前：剪掉"]
    assert marks(trim_row(html, "t01")) == "見第 1 項" and marks(trim_row(html, "t05")) == ""
    assert "請聽" not in html


def test_every_trim_has_a_clip_with_a_second_on_each_side(edited_project, tmp_path):
    p = edited_project
    html = page(p)
    trims = {t["id"]: t for t in read_json(p, "trim.json")["trims"]}
    audio_s = sf.info(str(p.work / "audio48k.wav")).duration
    for tid, trim in trims.items():
        clips = embedded(trim_row(html, tid), "audio/mp4")
        assert len(clips) == 1, tid
        info = probe_clip(tmp_path, clips[0])
        expected = min(audio_s, trim["end"] + 1.0) - max(0.0, trim["start"] - 1.0)
        assert info["codec_name"] == "aac" and info["channels"] == 1, tid
        assert abs(info["duration"] - expected) < 0.12, (tid, info["duration"], expected)
        assert 40_000 <= int(info["bit_rate"]) <= 80_000, (tid, info["bit_rate"])


def test_no_trims_means_no_table_rows(tmp_path):
    transcript, trims = transcript_and_trims(THREE, asr="第一句話第二句話第三句話")
    trims["trims"] = []
    edit = default_edit(transcript, trims)
    p = edited_project_from_documents(tmp_path, transcript, trims, edit)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert "data-trim" not in html and "沒有剪輯候選" in html
    assert result["cuts"] == 0


# ---- the two lists ----------------------------------------------------------------------------

EXTRA_SCRIPT = "今天天氣很好。我們去公園散步。最後記得追蹤我。"
BETWEEN = [("今天天氣很好", 0.5, 2.0), ("對了我想先講一件事情", 3.0, 4.6), ("我們去公園散步", 6.0, 7.6),
           ("最後記得追蹤我", 8.6, 10.0)]  # extra speech between s01 and s02, owned by s02
INSIDE = [("今天天氣很好", 0.5, 2.0), ("我們去", 3.0, 3.6), ("對了我想先講一件事情", 3.6, 5.2), ("公園散步", 5.2, 6.0),
          ("最後記得追蹤我", 7.0, 8.4)]  # extra speech inside s02
RECOGNISED = [("今天天氣很好", 0.5, 2.0), ("我們去另外一個地方玩耍", 3.0, 5.0), ("最後記得追蹤我", 6.0, 7.4)]
# s02 matches its script so poorly that its captions show the recognised 我們去另外一個; 地方玩耍 is extra speech
CLEAN = [("第一句話", 0.5, 1.3), ("第二句話", 2.0, 2.8), ("第三句話", 3.5, 4.3)]  # THREE as written, with pauses
LONG_WORD = "今天天氣很好。我們來看 Supercalifragilisticexpialidocious 這個字。"  # 17 units wide: cut across two lines


def loud_documents(script: str, asr: list[tuple[str, float, float]]) -> tuple[dict, dict]:
    """(transcript, trims) for a fake ASR result in which every item is loud and the rest is quiet."""
    return transcript_and_trims(script, asr, loud=[(start, end) for _, start, end in asr])


def only_trim(trims: dict, kind: str) -> dict:
    (trim,) = [t for t in trims["trims"] if t["kind"] == kind]
    return trim


def show_s03_as_recognised(p) -> None:
    """Make the captions of s03 of the Meijia project show the recognised text: a decision."""
    transcript = read_json(p, "transcript.json")
    transcript["sentences"][2]["display_source"] = "asr"
    write_json(p, "transcript.json", transcript)


def test_page_has_no_fixed_minimum_width_and_turns_rows_into_cards_on_a_phone(edited_project):
    html = page(edited_project)
    assert "min-width" not in html and "@media (max-width: 640px)" in html
    assert all('data-label="' in td for td in re.findall(r"<td[^>]*>", html))
    phone = html[html.index("@media (max-width: 640px)"):html.index("</style>")]
    assert "table, thead, tbody, tr, td { display: block; }" in phone
    assert "thead { display: none; }" in phone and "td::before { content: attr(data-label);" in phone
    for table in re.findall(r"<table.*?</table>", html, re.S):  # every cell is labelled with its column
        headers = re.findall(r"<th>(.*?)</th>", table)
        rows = re.findall(r"<tr data-.*?</tr>", table, re.S)
        assert rows and all(re.findall(r'<td[^>]* data-label="([^"]*)"', row) == headers for row in rows)


def test_items_come_first_in_order_with_a_player_each(edited_project, tmp_path):
    p = edited_project
    html = page(p)
    order = ["<h1>", 'class="project"', 'class="summary"', "<h2>要你決定</h2>", "<h2>其他要聽的地方</h2>",
             "<h2>句子</h2>", "<h2>剪輯候選</h2>"]
    positions = [html.index(mark) for mark in order]
    assert positions == sorted(positions)
    # the Meijia project: no decision; the head cut point, t05 (whose cut point is in speech) and the tail
    assert item_numbers(html) == [1, 2, 3]
    assert "沒有要你決定的事。" in html and "沒有其他要聽的地方。" not in html
    decisions, listen = items_of(p)
    assert [item.number for item in decisions + listen] == [1, 2, 3]
    for item in listen:
        li = item_li(html, item.number)
        assert li.count('class="play"') == 1
        (clip,) = embedded(li, "audio/mp4")
        assert abs(probe_clip(tmp_path, clip)["duration"] - span_seconds(p, *item.clip)) < 0.12, item
    assert html.count('class="play"') == 3 + 5  # the items and the candidates; a sentence row has none


def test_md_lists_match_the_page_numbers(edited_project):
    p = edited_project
    show_s03_as_recognised(p)  # one decision, 1; the three places to listen to are 2 to 4
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert item_numbers(html) == [1, 2, 3, 4]
    expected = []
    for number in item_numbers(html):
        first, *details = item_lines(html, number)
        assert first.startswith(f"{number} ")
        expected += [f"{number}. {first[len(f'{number} '):]}", *(f"   - {line}" for line in details)]
    assert section(md, "要你決定") + section(md, "其他要聽的地方") == expected
    assert section(md, "要你決定")[1:] == ["   - 字幕：這個方法可以省下 150 分鐘",
                                         "   - 腳本：這個方法可以省下 150 分鐘。"]
    assert section(md, "其他要聽的地方")[0].startswith("2. ")
    order = ["- 原長：", "- 新長：", "- 剪掉：", "- 要你決定：", "- 其他要聽：", "## 要你決定", "## 其他要聽的地方",
             "## 句子", "## 剪輯候選"]
    positions = [md.index(mark) for mark in order]
    assert positions == sorted(positions)
    assert not re.search(r'data:|<button|class="play"', md)


def test_a_candidate_outside_the_reel_says_so_and_is_not_counted(edited_project):
    p = edited_project
    remove_range(p, "s01", "s01")  # the reel starts at s02: the pause t01 and the NG take t02 before it are out
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert result["cuts"] == 3 + 1 and "<dt>剪掉</dt><dd>4 段</dd>" in html and "- 剪掉：4 段" in md
    for tid in ("t01", "t02"):
        row = trim_row(html, tid)
        assert marks(row) == "不在成品裡" and 'class="cut"' in row  # still accepted, but nothing is cut there
        (md_row,) = table_rows(md, tid)
        assert cells(md_row)[-1].strip() == "不在成品裡"
    for tid in ("t03", "t04", "t05"):
        assert "不在成品裡" not in trim_row(html, tid)
    assert marks(trim_row(html, "t05")) == "見第 1 項"  # then the tail cut point; the head is in silence now


def test_result_counts_decisions_and_listen_points(edited_project):
    p = edited_project
    result = validated_storyboard(p)
    assert (result["decisions"], result["listen_points"]) == (0, 3)
    assert set(result) == {"html", "md", "duration_s", "source_s", "cuts", "decisions", "listen_points", "warnings",
                           "skipped"}
    show_s03_as_recognised(p)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert (result["decisions"], result["listen_points"]) == (1, 3)
    assert re.findall(r"<dt>(.*?)</dt>", html) == ["原長", "新長", "剪掉", "要你決定", "其他要聽"]
    assert "<dt>要你決定</dt><dd>1 項</dd>" in html and "<dt>其他要聽</dt><dd>3 處</dd>" in html
    assert "- 要你決定：1 項" in md and "- 其他要聽：3 處" in md
    assert "請聽" not in html + md


def test_a_recognised_sentence_item_shows_captions_and_script(tmp_path):
    transcript, trims = loud_documents(EXTRA_SCRIPT, RECOGNISED)
    s02, extra = transcript["sentences"][1], only_trim(trims, "extra")
    assert s02["display_source"] == "asr" and s02["text"] == "我們去另外一個"
    assert (extra["text"], extra["sentence"]) == ("地方玩耍", "s03")
    p = edited_project_from_documents(tmp_path, transcript, trims)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert (result["decisions"], result["listen_points"]) == (2, 0)
    first = f"{new_time(p, s02['start'])} 字幕用辨識文字「我們去另外一個」"
    second = f"{new_time(p, extra['start'])} 腳本外的話「地方玩耍」 目前：保留"
    assert item_lines(html, 1) == [f"1 {first}", "字幕：我們去另外一個", "腳本：我們去公園散步。"]
    assert item_lines(html, 2) == [f"2 {second}"]
    assert section(md, "要你決定") == [f"1. {first}", "   - 字幕：我們去另外一個", "   - 腳本：我們去公園散步。",
                                     f"2. {second}"]
    assert section(md, "其他要聽的地方") == ["沒有其他要聽的地方。"]
    (clip,) = embedded(item_li(html, 1), "audio/mp4")  # the sentence with 0.3 s on each side
    assert abs(probe_clip(tmp_path, clip)["duration"] - (s02["end"] - s02["start"] + 0.6)) < 0.12
    row = sentence_row(html, "s02")
    assert marks(row) == "見第 1 項" and 'class="play"' not in row
    row = trim_row(html, extra["id"])
    assert cell(row, "種類") == "腳本外的話" and marks(row) == "見第 2 項" and 'class="kept"' in row


def test_the_storyboard_shows_the_edited_caption(tmp_path):
    transcript, trims = loud_documents(EXTRA_SCRIPT, RECOGNISED)
    assert transcript["sentences"][1]["display_source"] == "asr"
    p = edited_project_from_documents(tmp_path, transcript, trims)
    edit = read_json(p, "edit.json")
    edit["captions"]["text"] = {"s02": "我們去公園散步"}
    write_json(p, "edit.json", edit)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert cell(sentence_row(html, "s02"), "字幕") == "我們去公園散步"
    (md_row,) = table_rows(md, "s02")
    assert "我們去公園散步" in md_row and "另外一個" not in md_row
    # the user has fixed what the recognition got wrong: only the extra speech is left to decide
    assert (result["decisions"], result["listen_points"]) == (1, 0)
    assert item_lines(html, 1)[0].endswith("腳本外的話「地方玩耍」 目前：保留")
    assert marks(sentence_row(html, "s02")) == "" and "字幕用辨識文字" not in html + md


def test_a_sentence_row_points_to_its_items(edited_project):
    p = edited_project
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    expected = {"s01": "見第 1 項", "s02": "", "s03": "", "s04": "見第 2、3 項"}  # head; t05 and the tail
    for sid, mark in expected.items():
        row = sentence_row(html, sid)
        assert marks(row) == mark and 'class="play"' not in row, sid
        (md_row,) = table_rows(md, sid)
        assert cells(md_row)[-1].strip() == mark, sid
    assert marks(trim_row(html, "t05")) == "見第 2 項" and marks(trim_row(html, "t04")) == ""
    (md_row,) = table_rows(md, "t05")
    assert cells(md_row)[-1].strip() == "見第 2 項"


def test_long_word_warning_and_row_mark(tmp_path):
    transcript, trims = transcript_and_trims(LONG_WORD, LONG_WORD)
    trims["warnings"] = ["few pauses: quiet-point detection unreliable"]
    p = edited_project_from_documents(tmp_path, transcript, trims)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert result["warnings"] == ["few pauses: quiet-point detection unreliable", LONG_WORD_WARNING]
    assert "<li>有詞太長，字幕從詞的中間斷開</li>" in html and "- 警告：有詞太長，字幕從詞的中間斷開" in md
    assert html.index('class="warnings"') < html.index("<h2>要你決定</h2>")
    assert marks(sentence_row(html, "s02")) == "有詞太長，從中間斷開"
    assert marks(sentence_row(html, "s01")) == ""
    (md_row,) = table_rows(md, "s02")
    assert cells(md_row)[-1].strip() == "有詞太長，從中間斷開"
    remove_range(p, "s02", "s02")  # a removed sentence shows no captions: no warning, no mark
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert result["warnings"] == ["few pauses: quiet-point detection unreliable"]
    assert "有詞太長" not in html and marks(sentence_row(html, "s02")) == "移除（off topic）"


def test_no_items_says_so(tmp_path):
    transcript, trims = loud_documents(THREE, CLEAN)  # said as written, every cut point in silence
    p = edited_project_from_documents(tmp_path, transcript, trims)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert (result["decisions"], result["listen_points"]) == (0, 0)
    assert "沒有要你決定的事。" in html and "沒有其他要聽的地方。" in html
    assert "<dd>0 項</dd>" in html and "<dd>0 處</dd>" in html
    assert "data-item" not in html and "見第" not in html + md
    assert section(md, "要你決定") == ["沒有要你決定的事。"]
    assert section(md, "其他要聽的地方") == ["沒有其他要聽的地方。"]


def test_a_low_match_sentence_with_script_captions_is_a_listen_item(edited_project, tmp_path):
    p = edited_project
    set_cut_listen(p)
    set_trim_listen(p)
    transcript = read_json(p, "transcript.json")
    s02 = transcript["sentences"][1]
    s02.update(needs_listen=True, reasons=["low_match", "extra_speech"])
    write_json(p, "transcript.json", transcript)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert (result["decisions"], result["listen_points"]) == (0, 1)
    assert item_lines(html, 1) == [f"1 {new_time(p, s02['start'])} 吻合度低「第一步是把停頓剪掉，讓節奏變快。」"]
    (clip,) = embedded(item_li(html, 1), "audio/mp4")
    assert abs(probe_clip(tmp_path, clip)["duration"] - (s02["end"] - s02["start"] + 0.6)) < 0.12
    row = sentence_row(html, "s02")
    assert marks(row) == "見第 1 項" and 'class="play"' not in row
    assert "有腳本外的話" not in html and "請聽" not in html


def test_extra_speech_is_a_decision_with_a_clip_of_the_speech(tmp_path):
    transcript, trims = loud_documents(EXTRA_SCRIPT, BETWEEN)
    extra = only_trim(trims, "extra")
    assert (extra["text"], extra["sentence"]) == ("對了我想先講一件事情", "s02")
    p = edited_project_from_documents(tmp_path, transcript, trims)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert result["decisions"] == 1
    assert item_lines(html, 1) == [f"1 {new_time(p, extra['start'])} 腳本外的話「對了我想先講一件事情」 目前：保留"]
    (clip,) = embedded(item_li(html, 1), "audio/mp4")  # the candidate with a second on each side
    assert abs(probe_clip(tmp_path, clip)["duration"] - (extra["end"] - extra["start"] + 2.0)) < 0.12
    row = trim_row(html, extra["id"])
    assert cell(row, "種類") == "腳本外的話" and marks(row) == "見第 1 項" and len(embedded(row, "audio/mp4")) == 1
    assert marks(sentence_row(html, "s02")) == "見第 1 項" and "有腳本外的話" not in html


def test_extra_speech_of_a_removed_sentence_keeps_its_row_and_is_not_in_the_reel(tmp_path):
    transcript, trims = loud_documents(EXTRA_SCRIPT, INSIDE)
    extra = only_trim(trims, "extra")
    edit = default_edit(transcript, trims)
    edit["cuts"]["extra"] = [{"from": "s02", "to": "s02", "reason": "off topic"}]
    edit["sentences"] = [s for s in edit["sentences"] if s["id"] != "s02"]
    p = edited_project_from_documents(tmp_path, transcript, trims, edit)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    row = trim_row(html, extra["id"])
    assert cell(row, "種類") == "腳本外的話" and marks(row) == "不在成品裡" and cell(row, "處理") == "保留"
    (clip,) = embedded(row, "audio/mp4")
    assert abs(probe_clip(tmp_path, clip)["duration"] - (extra["end"] - extra["start"] + 2.0)) < 0.12
    assert result["decisions"] == 0 and result["cuts"] == 2 + 1  # the two pauses next to s02 are in the reel


def test_list_items_escape_what_was_said_and_md_keeps_each_on_one_line(tmp_path):
    transcript, trims = loud_documents(EXTRA_SCRIPT, RECOGNISED)
    transcript["sentences"][1]["script_text"] = "我們去 <b> & 公園。"
    only_trim(trims, "extra")["text"] = '<i>&"\n|'
    p = edited_project_from_documents(tmp_path, transcript, trims)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert "腳本：我們去 &lt;b&gt; &amp; 公園。" in item_li(html, 1)
    assert "「&lt;i&gt;&amp;&quot;" in item_li(html, 2) and not re.search(r"<(b|i)>", html)
    assert "   - 腳本：我們去 <b> & 公園。" in section(md, "要你決定")
    assert section(md, "要你決定")[-1].endswith('腳本外的話「<i>&" |」 目前：保留')


@pytest.mark.parametrize("sid", ["s01", "s04"])
def test_a_list_item_clip_stays_inside_the_audio(edited_project, tmp_path, sid):
    p = edited_project
    set_cut_listen(p)
    set_trim_listen(p)
    transcript = read_json(p, "transcript.json")
    sentence = next(s for s in transcript["sentences"] if s["id"] == sid)
    sentence.update(needs_listen=True, reasons=["low_match"])
    write_json(p, "transcript.json", transcript)
    (clip,) = embedded(item_li(page(p), 1), "audio/mp4")
    expected = span_seconds(p, sentence["start"] - 0.3, sentence["end"] + 0.3)
    assert abs(probe_clip(tmp_path, clip)["duration"] - expected) < 0.12


def test_an_unspoken_sentence_says_not_spoken_and_nothing_else(tmp_path):
    transcript, trims = transcript_and_trims(THREE, asr="第一句話第三句話")
    skipped = transcript["sentences"][1]
    assert skipped["spoken"] is False
    skipped.update(needs_listen=True, reasons=["low_match"])  # the aligner flags it; the page must not list it
    p = edited_project_from_documents(tmp_path, transcript, trims)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    row = sentence_row(html, "s02")
    assert marks(row) == "沒講到" and "第二句話" in row
    assert 'class="play"' not in row and "<img" not in row and "<del>" not in row
    assert 'class="unspoken"' in row
    assert "第二句話" not in "".join(item_li(html, n) for n in item_numbers(html))
    assert "沒講到" not in sentence_row(html, "s01")


def test_a_removed_sentence_is_struck_through_with_its_reason(edited_project):
    p = edited_project
    set_cut_listen(p)
    transcript = read_json(p, "transcript.json")
    transcript["sentences"][2].update(needs_listen=True, reasons=["low_match"])
    write_json(p, "transcript.json", transcript)
    edit = read_json(p, "edit.json")
    edit["cuts"]["extra"] = [{"from": "s03", "to": "s03", "reason": "off topic"}]
    edit["sentences"] = [s for s in edit["sentences"] if s["id"] != "s03"]
    write_json(p, "edit.json", edit)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    row = sentence_row(html, "s03")
    assert row.startswith('<tr data-sentence="s03" class="removed">')
    assert "<del>這個方法可以省下 150 分鐘。</del>" in row
    assert marks(row) == "移除（off topic）" and 'class="play"' not in row
    assert len(embedded(row, "image/jpeg")) == 1  # the user can still see what goes
    assert "吻合度低" not in html and result["listen_points"] == 1  # only t05, whose cut point is in speech
    assert "<del>" not in sentence_row(html, "s02")


# ---- cut points that are not in silence ------------------------------------------------------


def test_head_and_tail_cut_points_that_are_not_in_silence_are_listen_items(edited_project, tmp_path):
    p = edited_project
    bounds = read_json(p, "trim.json")["bounds"]
    assert [bounds[sid]["needs_listen"] for sid in ("s01", "s02", "s03", "s04")] == [True, False, False, True]
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    head, tail = bounds["s01"]["cut_in"], bounds["s04"]["cut_out"]
    assert item_lines(html, 1) == [f"1 {new_time(p, head)} 片頭切點「今天我們來聊短影音剪輯。」"]
    assert item_lines(html, 3) == [f"3 {new_time(p, tail)} 片尾切點「最後記得追蹤我。」"]
    assert new_time(p, head) == "0:00.0" and new_time(p, tail) == storyboard_module.clock(result["duration_s"])
    # a second either side of the point; the other cut point of the sentence would give 2.0 s here
    for number, point in ((1, head), (3, tail)):
        (clip,) = embedded(item_li(html, number), "audio/mp4")
        assert abs(probe_clip(tmp_path, clip)["duration"] - around(p, point, 1.0)) < 0.12
        assert abs(around(p, point, 1.0) - 1.0) < 1e-9
    assert result["listen_points"] == 3 and "<dt>其他要聽</dt><dd>3 處</dd>" in html


def test_the_first_kept_sentence_with_quiet_cut_points_has_no_item(edited_project):
    p = edited_project
    set_cut_listen(p, s04=True)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert marks(sentence_row(html, "s01")) == ""
    assert marks(sentence_row(html, "s04")) == "見第 1、2 項"  # t05, then the tail
    assert "片尾切點" in item_li(html, 2) and "片頭切點" not in html
    assert result["listen_points"] == 2


def test_the_edges_of_a_range_at_the_head_or_the_tail_are_not_cut_points(edited_project):
    p = edited_project
    set_cut_listen(p, s01=True, s02=True, s04=True)
    remove_range(p, "s01", "s01")
    remove_range(p, "s04", "s04")
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    for sid in ("s01", "s04"):  # removed at the ends of the reel: no cut is made at their edges
        assert marks(sentence_row(html, sid)) == "移除（off topic）"
    # but the first kept sentence is the head of the reel; t05 lies after its end now
    assert item_lines(html, 1) == ["1 0:00.0 片頭切點「第一步是把停頓剪掉，讓節奏變快。」"]
    assert marks(sentence_row(html, "s02")) == "見第 1 項" and marks(sentence_row(html, "s03")) == ""
    assert result["listen_points"] == 1


def test_the_edges_of_a_range_between_kept_sentences_are_cut_points(edited_project, tmp_path):
    p = edited_project
    set_cut_listen(p, s02=True, s03=True)
    trims = read_json(p, "trim.json")
    # Cut times that tell each other apart by how far the audio reaches on either side: s02 gives
    # 1.5 s at cut_in (0.5 s) and 2.0 s at cut_out; s03 gives 1.4 s at cut_out (15.5 s) and 2.0 s at cut_in.
    trims["bounds"]["s02"]["cut_in"], trims["bounds"]["s03"]["cut_out"] = 0.5, 15.5
    write_json(p, "trim.json", trims)
    transcript = read_json(p, "transcript.json")
    transcript["sentences"][1].update(needs_listen=True, reasons=["low_match"])  # no item: s02 is removed
    write_json(p, "transcript.json", transcript)
    remove_range(p, "s02", "s03")
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert item_lines(html, 1) == [f"1 {new_time(p, 0.5)} 刪除段前切點「第一步是把停頓剪掉，讓節奏變快。」"]
    assert item_lines(html, 2) == [f"2 {new_time(p, 15.5)} 刪除段後切點「這個方法可以省下 150 分鐘。」"]
    assert clip_seconds(tmp_path, item_li(html, 1)) == pytest.approx([1.5], abs=0.12)
    assert clip_seconds(tmp_path, item_li(html, 2)) == pytest.approx([around(p, 15.5, 1.0)], abs=0.12)  # about 1.4 s
    expected = {"s01": "", "s02": "移除（off topic）；見第 1 項", "s03": "移除（off topic）；見第 2 項", "s04": ""}
    for sid, mark in expected.items():
        assert marks(sentence_row(html, sid)) == mark, sid
    assert "吻合度低" not in html and result["listen_points"] == 2


def test_a_range_with_one_sentence_has_an_item_at_each_of_its_edges(edited_project, tmp_path):
    p = edited_project
    set_cut_listen(p, s02=True)
    set_trim_listen(p)
    trims = read_json(p, "trim.json")
    trims["bounds"]["s02"]["cut_in"] = 0.5  # after the cut_in of s01 (0 s), so the bounds stay in order
    write_json(p, "trim.json", trims)  # the clip at the cut_in is 1.5 s long, the one at the cut_out (9.4 s) 2.0 s
    remove_range(p, "s02", "s02")
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert "刪除段前切點" in item_li(html, 1) and "刪除段後切點" in item_li(html, 2)
    assert [clip_seconds(tmp_path, item_li(html, n)) for n in (1, 2)] == [pytest.approx([1.5], abs=0.12),
                                                                          pytest.approx([2.0], abs=0.12)]
    assert marks(sentence_row(html, "s02")) == "移除（off topic）；見第 1、2 項"
    (md_row,) = table_rows(md, "s02")
    assert cells(md_row)[-1].strip() == "移除（off topic）；見第 1、2 項"


def test_a_cut_point_and_a_low_match_item_of_one_sentence_are_two_items(edited_project, tmp_path):
    p = edited_project
    set_cut_listen(p, s01=True)
    set_trim_listen(p)
    transcript = read_json(p, "transcript.json")
    sentence = transcript["sentences"][0]
    sentence.update(needs_listen=True, reasons=["low_match"])
    write_json(p, "transcript.json", transcript)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    # both at 0 s: the cut point comes first
    assert item_lines(html, 1) == ["1 0:00.0 片頭切點「今天我們來聊短影音剪輯。」"]
    assert item_lines(html, 2) == ["2 0:00.0 吻合度低「今天我們來聊短影音剪輯。」"]
    assert clip_seconds(tmp_path, item_li(html, 1)) == pytest.approx([1.0], abs=0.12)
    assert clip_seconds(tmp_path, item_li(html, 2)) == pytest.approx([sentence["end"] + 0.3], abs=0.12)
    assert marks(sentence_row(html, "s01")) == "見第 1、2 項" and result["listen_points"] == 2


def test_the_only_kept_sentence_has_a_head_and_a_tail_item(edited_project, tmp_path):
    p = edited_project
    set_cut_listen(p, s01=True, s04=True)
    edit = read_json(p, "edit.json")
    # With the pause t01 (2.6 s) accepted the reel would end there, before the cut_out of s01 (2.73 s)
    edit["cuts"]["reject"], edit["cuts"]["accept"] = [*edit["cuts"]["reject"], *edit["cuts"]["accept"]], []
    write_json(p, "edit.json", edit)
    remove_range(p, "s02", "s04")
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert item_lines(html, 1) == ["1 0:00.0 片頭切點「今天我們來聊短影音剪輯。」"]
    tail = read_json(p, "trim.json")["bounds"]["s01"]["cut_out"]
    assert item_lines(html, 2) == [f"2 {new_time(p, tail)} 片尾切點「今天我們來聊短影音剪輯。」"]
    assert [clip_seconds(tmp_path, item_li(html, n)) for n in (1, 2)] == [pytest.approx([1.0], abs=0.12),
                                                                          pytest.approx([2.0], abs=0.12)]
    assert marks(sentence_row(html, "s01")) == "見第 1、2 項"
    assert marks(sentence_row(html, "s04")) == "移除（off topic）"  # a range at the tail has no cut of its own
    assert result["listen_points"] == 2


# ---- where the reel really starts and ends ----------------------------------------------------


def even_sentence(sid: str, start: float, end: float) -> dict:
    """A transcript sentence made by hand whose four units are spread evenly over start..end."""
    text = "甲乙丙丁。"
    n = len(split_units(text))
    times = [(start + (end - start) * i / n, start + (end - start) * (i + 1) / n) for i in range(n)]
    return timed_sentence(sid, text, times)


def hand_items(sentences, bounds, extra=None, accept=()):
    """(the listen items, segments) of review_items for documents written by hand. `sentences` are
    (id, start, end), `bounds` maps an id to (cut_in, cut_out, needs_listen), `extra` is a (from, to) range
    or None, `accept` lists the (start, end) of the accepted candidates: pauses whose cut points are in
    silence, so that only cut points of the sentences become items."""
    ids = [sid for sid, _, _ in sentences]
    transcript = {"sentences": [even_sentence(*sentence) for sentence in sentences]}
    trims = {
        "trims": [{"id": f"t{n:02d}", "kind": "pause", "start": a, "end": b, "sentence": ids[0], "text": "",
                   "suggest": "cut", "length": 0, "needs_listen": False} for n, (a, b) in enumerate(accept, 1)],
        "bounds": {sid: {"cut_in": a, "cut_out": b, "needs_listen": listen} for sid, (a, b, listen) in bounds.items()},
    }
    gone = set()
    cuts = {"accept": [t["id"] for t in trims["trims"]], "reject": [], "extra": []}
    if extra is not None:
        first, last = extra
        gone = set(ids[ids.index(first):ids.index(last) + 1])
        cuts["extra"] = [{"from": first, "to": last, "reason": "off topic"}]
    edit = {"version": 1, "style": "default", "cuts": cuts, "captions": {"mode": "line"},
            "sentences": [{"id": sid, "sem": "claim", "weight": "main"} for sid in ids if sid not in gone]}
    segments = build_segments(edit, transcript, trims)
    decisions, listen = review_items(edit, transcript, trims, segments)
    assert decisions == []
    return listen, segments


def cut_points(items) -> list[tuple[str, str, tuple[float, float]]]:
    """(sentence, label, clip) of each cut point item."""
    assert all(item.kind == "cut_point" for item in items)
    return [(item.sentence, item.label, item.clip) for item in items]


def test_a_removed_sentence_whose_cut_out_starts_the_reel_is_the_head_cut_point():
    # s01 is removed, its cut_out (2.4 s) lies after the cut_in of s02 (2.2 s): the reel starts at 2.4 s
    items, segments = hand_items(
        [("s01", 0.5, 2.0), ("s02", 2.6, 6.0)],
        {"s01": (0.0, 2.4, True), "s02": (2.2, 6.3, False)},
        extra=("s01", "s01"),
    )
    assert [t for g in segments for t in (g.start, g.end)] == pytest.approx([2.4, 6.3])
    # on the first kept row; a second either side of 2.4 s, not of 2.2 s
    assert cut_points(items) == [("s02", "片頭切點在刪除的 s01 之後", pytest.approx((1.4, 3.4)))]


def test_a_removed_sentence_whose_cut_in_ends_the_reel_is_the_tail_cut_point():
    # s03 is removed, its cut_in (4.3 s) lies before the cut_out of s02 (4.5 s): the reel ends at 4.3 s
    items, segments = hand_items(
        [("s01", 0.5, 1.8), ("s02", 2.2, 4.0), ("s03", 4.7, 5.8)],
        {"s01": (0.0, 2.0, False), "s02": (2.1, 4.5, False), "s03": (4.3, 6.0, True)},
        extra=("s03", "s03"),
    )
    assert [t for g in segments for t in (g.start, g.end)] == pytest.approx([0.0, 4.3])
    # on the last kept row; a second either side of 4.3 s, not of 4.5 s
    assert cut_points(items) == [("s02", "片尾切點在刪除的 s03 之前", pytest.approx((3.3, 5.3)))]


def test_when_the_removed_cut_point_and_the_kept_one_are_at_the_same_frame_the_kept_sentence_owns_it():
    items, _ = hand_items(
        [("s01", 0.5, 2.0), ("s02", 2.6, 6.0)],
        {"s01": (0.0, 2.2, True), "s02": (2.2, 6.3, True)},
        extra=("s01", "s01"),
    )
    assert [(sid, label) for sid, label, _ in cut_points(items)] == [("s02", "片頭切點"), ("s02", "片尾切點")]


def test_a_candidate_that_starts_the_reel_leaves_no_head_cut_point():
    # an accepted candidate from 0 s to 1 s pushes the start of the reel to 1 s: no sentence has a cut point there
    items, segments = hand_items(
        [("s01", 0.5, 2.0), ("s02", 2.6, 6.0)],
        {"s01": (0.0, 2.4, True), "s02": (2.5, 6.3, False)},
        accept=[(0.0, 1.0)],
    )
    assert segments[0].start == pytest.approx(1.0)
    assert items == []


def test_a_candidate_that_ends_the_reel_leaves_no_tail_cut_point():
    # an accepted candidate from 5.5 s to 7 s ends the reel at 5.5 s: no sentence has a cut point there
    items, segments = hand_items(
        [("s01", 0.5, 2.0), ("s02", 2.6, 6.0)],
        {"s01": (0.0, 2.4, False), "s02": (2.5, 6.3, True)},
        accept=[(5.5, 7.0)],
    )
    assert segments[-1].end == pytest.approx(5.5)
    assert items == []


def test_dropped_sliver_head_tail_ownership():
    # build_segments drops a piece of fewer than MIN_SEGMENT_FRAMES (2) frames. An accepted candidate that
    # starts one frame after the first kept sentence's cut_in leaves a one-frame piece at the head, and one
    # that ends one frame before the last kept sentence's cut_out leaves one at the tail. Both pieces are
    # dropped, so the reel starts and ends at the edges of the candidates, and no sentence has a cut point there.
    sentences = [("s01", 0.5, 2.0), ("s02", 2.6, 6.0)]
    bounds = {"s01": (0.0, 2.4, True), "s02": (2.5, 6.3, True)}  # frames 0 and 72, 75 and 189
    items, segments = hand_items(sentences, bounds, accept=[(1 / FPS, 1.0), (5.0, 188 / FPS)])
    assert [t for g in segments for t in (g.start, g.end)] == pytest.approx([1.0, 5.0])  # (0, 1) and (188, 189) gone
    assert items == []
    # the same with pieces of exactly two frames: they stay, the reel starts at the cut_in of s01 and ends at
    # the cut_out of s02, and each of them owns its point
    items, segments = hand_items(sentences, bounds, accept=[(2 / FPS, 1.0), (5.0, 187 / FPS)])
    assert [t for g in segments for t in (g.start, g.end)] == pytest.approx([0.0, 2 / FPS, 1.0, 5.0, 187 / FPS, 6.3])
    assert cut_points(items) == [("s01", "片頭切點", pytest.approx((0.0, 1.0))),
                                 ("s02", "片尾切點", pytest.approx((5.3, 7.3)))]


def test_a_removed_head_sentence_that_starts_the_reel_names_it_on_the_first_kept_sentence(edited_project, tmp_path):
    p = edited_project
    set_cut_listen(p, s01=True)
    set_trim_listen(p)
    trims = read_json(p, "trim.json")
    trims["bounds"]["s01"]["cut_out"] = 5.9  # after the cut_in of s02 (5.57 s): the reel starts at 5.9 s
    write_json(p, "trim.json", trims)
    remove_range(p, "s01", "s01")
    transcript, edit = read_json(p, "transcript.json"), read_json(p, "edit.json")
    assert build_segments(edit, transcript, trims)[0].start == pytest.approx(5.9)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    line = "0:00.0 片頭切點在刪除的 s01 之後「第一步是把停頓剪掉，讓節奏變快。」"
    assert item_lines(html, 1) == [f"1 {line}"]
    assert clip_seconds(tmp_path, item_li(html, 1)) == pytest.approx([2.0], abs=0.12)  # 4.9 s to 6.9 s
    assert marks(sentence_row(html, "s01")) == "移除（off topic）" and 'class="play"' not in sentence_row(html, "s01")
    assert marks(sentence_row(html, "s02")) == "見第 1 項"
    assert result["listen_points"] == 1
    assert section(Path(result["md"]).read_text(encoding="utf-8"), "其他要聽的地方") == [f"1. {line}"]


def test_a_removed_tail_sentence_that_ends_the_reel_names_it_on_the_last_kept_sentence(edited_project, tmp_path):
    p = edited_project
    set_cut_listen(p, s04=True)
    trims = read_json(p, "trim.json")
    trims["bounds"]["s04"]["cut_in"] = 13.0  # before the cut_out of s03 (13.5 s): the reel ends at 13.0 s
    write_json(p, "trim.json", trims)
    remove_range(p, "s04", "s04")
    transcript, edit = read_json(p, "transcript.json"), read_json(p, "edit.json")
    assert build_segments(edit, transcript, trims)[-1].end == pytest.approx(13.0)
    result = validated_storyboard(p)
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert item_lines(html, 1) == [f"1 {new_time(p, 13.0)} 片尾切點在刪除的 s04 之前「這個方法可以省下 150 分鐘。」"]
    assert clip_seconds(tmp_path, item_li(html, 1)) == pytest.approx([2.0], abs=0.12)  # 12.0 s to 14.0 s
    assert marks(sentence_row(html, "s04")) == "移除（off topic）" and marks(sentence_row(html, "s03")) == "見第 1 項"
    assert result["listen_points"] == 1  # t05 lies after the end of the reel now


# ---- the folder, the step and the command -----------------------------------------------------


def test_invalid_edit_exit_2(edited_project, capsys):
    e = json.loads(edited_project.edit_path.read_text()); e["sentences"] = []
    edited_project.edit_path.write_text(json.dumps(e))
    assert main(["storyboard", edited_project.name]) == 2


def test_invalid_edit_reports_every_error_and_writes_nothing(edited_project, capsys):
    p = edited_project
    edit = read_json(p, "edit.json")
    edit["sentences"] = []
    edit["captions"]["mode"] = "karaoke"
    write_json(p, "edit.json", edit)
    assert main(["storyboard", p.name]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and len(out["errors"]) >= 2
    assert not (p.review / "gate1").exists() and "storyboard" not in load_state(p)["steps"]


def test_a_missing_edit_is_an_execution_error(trimmed_project, capsys):
    assert main(["storyboard", trimmed_project.name]) == 1
    assert "edit.json" in json.loads(capsys.readouterr().out)["error"]


OLDER_FILES_REFUSAL = (
    "edit.json was checked against an older transcript.json or trim.json: "
    "re-check every trim and sentence id, then run validate"
)
NOT_VALIDATED_REFUSAL = "edit.json has not been validated: run validate"


def refused(p, capsys, message):
    """The storyboard command exits 1 with `message`, and leaves no folder and no step record."""
    capsys.readouterr()
    assert main(["storyboard", p.name]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"] == message
    assert not (p.review / "gate1").exists() and "storyboard" not in load_state(p)["steps"]


def test_storyboard_refuses_edit_checked_against_older_trim(edited_project, capsys):
    p = edited_project
    assert main(["validate", p.name]) == 0
    trims = read_json(p, "trim.json")
    trims["warnings"] = ["few pauses: quiet-point detection unreliable"]
    write_json(p, "trim.json", trims)
    refused(p, capsys, OLDER_FILES_REFUSAL)
    with pytest.raises(PipelineError, match="older transcript.json or trim.json"):
        build_storyboard(p)  # the same from code
    assert main(["validate", p.name]) == 0  # the director checked the ids and validated again
    assert main(["storyboard", p.name]) == 0


def test_storyboard_refuses_edit_checked_against_an_older_transcript(edited_project, capsys):
    p = edited_project
    assert main(["validate", p.name]) == 0
    transcript = read_json(p, "transcript.json")
    transcript["note"] = "a later transcribe run"
    write_json(p, "transcript.json", transcript)
    refused(p, capsys, OLDER_FILES_REFUSAL)


def test_storyboard_refuses_an_edit_that_was_never_validated(edited_project, capsys):
    refused(edited_project, capsys, NOT_VALIDATED_REFUSAL)


def test_storyboard_refuses_an_edit_that_changed_since_validate(edited_project, capsys):
    p = edited_project
    assert main(["validate", p.name]) == 0
    edit = read_json(p, "edit.json")
    edit["cuts"]["accept"].remove("t03")
    edit["cuts"]["reject"].append("t03")
    write_json(p, "edit.json", edit)
    refused(p, capsys, NOT_VALIDATED_REFUSAL)


def test_storyboard_names_the_older_files_when_edit_changed_too(edited_project, capsys):
    p = edited_project
    assert main(["validate", p.name]) == 0
    edit = read_json(p, "edit.json")
    edit["cuts"]["accept"].remove("t03")
    edit["cuts"]["reject"].append("t03")
    write_json(p, "edit.json", edit)
    trims = read_json(p, "trim.json")
    trims["warnings"] = ["few pauses: quiet-point detection unreliable"]
    write_json(p, "trim.json", trims)
    refused(p, capsys, OLDER_FILES_REFUSAL)


def test_storyboard_refuses_a_phase1_project_without_edit_checked(edited_project, capsys):
    p = edited_project
    assert main(["validate", p.name]) == 0
    state = load_state(p)
    del state["edit_checked"]  # state.json as the first phase wrote it
    save_state(p, state)
    refused(p, capsys, NOT_VALIDATED_REFUSAL)


def test_an_invalid_edit_is_reported_before_the_missing_validate(edited_project, capsys):
    p = edited_project
    edit = read_json(p, "edit.json")
    edit["sentences"] = []
    write_json(p, "edit.json", edit)
    assert main(["storyboard", p.name]) == 2  # the errors of edit.json, not "has not been validated"


def test_out_of_order_bounds_are_refused(edited_project, capsys):
    p = edited_project
    trims = read_json(p, "trim.json")
    trims["bounds"]["s02"], trims["bounds"]["s03"] = trims["bounds"]["s03"], trims["bounds"]["s02"]
    write_json(p, "trim.json", trims)
    assert main(["validate", p.name]) == 0  # validate does not look at the order; edit_checked now matches
    refused(p, capsys, "sentence bounds in trim.json are out of order: run trim again")
    with pytest.raises(PipelineError, match="out of order"):
        build_storyboard(p)  # the same from code


def test_missing_probe_says_run_prep(edited_project, capsys):
    p = edited_project
    assert main(["validate", p.name]) == 0
    (p.work / "probe.json").unlink()
    refused(p, capsys, "probe.json not found: run prep first")  # storyboard's own message, not run_step's


def test_rerun_replaces_folder(edited_project):
    validated_storyboard(edited_project); (edited_project.review / "gate1" / "stale.txt").write_text("x")
    validated_storyboard(edited_project)
    assert not (edited_project.review / "gate1" / "stale.txt").exists()


def test_the_old_folder_goes_through_safe_rmtree_inside_the_project(edited_project, monkeypatch):
    p = edited_project
    validated_storyboard(p)
    calls = []
    real = storyboard_module.safe_rmtree

    def spy(path, *, allowed_root, **kwargs):
        calls.append((Path(path), Path(allowed_root)))
        real(path, allowed_root=allowed_root, **kwargs)

    monkeypatch.setattr(storyboard_module, "safe_rmtree", spy)
    validated_storyboard(p)
    assert calls == [(p.review / "gate1", p.dir)]


def test_the_folder_holds_exactly_the_page_and_the_markdown(edited_project):
    validated_storyboard(edited_project)
    assert sorted(f.name for f in (edited_project.review / "gate1").iterdir()) == ["index.html", "storyboard.md"]


def test_media_files_are_unlinked_after_they_are_embedded(edited_project):
    p = edited_project
    validated_storyboard(p)
    media = p.work / "gate1-media"
    assert not media.exists() or not any(media.iterdir())


def test_a_failing_ffmpeg_leaves_no_media_file_and_keeps_the_old_page(edited_project, monkeypatch):
    p = edited_project
    first = validated_storyboard(p)
    before = Path(first["html"]).read_bytes()
    calls = []

    def fail_on_the_third(args, log, cwd=None):
        calls.append(args)
        Path(args[-1]).write_bytes(b"partial")
        if len(calls) == 3:
            raise PipelineError("ffmpeg failed with exit code 1", log=str(log))

    monkeypatch.setattr(storyboard_module, "run_ffmpeg", fail_on_the_third)
    with pytest.raises(PipelineError, match="ffmpeg failed"):
        validated_storyboard(p)
    media = p.work / "gate1-media"
    assert len(calls) == 3 and (not media.exists() or not any(media.iterdir()))
    assert Path(first["html"]).read_bytes() == before


def test_step_record_names_its_inputs_and_outputs(edited_project):
    p = edited_project
    validated_storyboard(p)
    record = load_state(p)["steps"]["storyboard"]
    assert set(record["inputs"]) == {"edit", "transcript", "trim", "style", "audio", "probe", "words", "dictionary"}
    expected = {
        "edit": p.work / "edit.json",
        "transcript": p.work / "transcript.json",
        "trim": p.work / "trim.json",
        "probe": p.work / "probe.json",
        "audio": p.work / "audio48k.wav",
        "words": words.WORDS_FILE,
        "dictionary": dictionary_path(),
    }
    for key, path in expected.items():
        stat = path.stat()
        assert record["inputs"][key] == {
            "path": str(path),
            "sha256": file_sha256(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }, key
    style = Path(record["inputs"]["style"]["path"])
    assert style.is_absolute() and style.name == "style.json" and style.is_file()
    assert record["outputs"] == ["review/gate1/index.html", "review/gate1/storyboard.md"]


def validate(p, capsys):
    """Run the validate command, which must pass, and drop what it printed."""
    assert main(["validate", p.name]) == 0
    capsys.readouterr()


def test_approve_needs_a_storyboard_made_from_the_current_edit(edited_project, capsys):
    p = edited_project
    validate(p, capsys)
    assert main(["approve", p.name, "gate1"]) == 1
    assert main(["storyboard", p.name]) == 0
    assert main(["approve", p.name, "gate1"]) == 0
    assert set(load_state(p)["gates"]["gate1"]) == {"approved_at", "storyboard_inputs"}
    edit = read_json(p, "edit.json")
    edit["cuts"]["accept"].remove("t03")
    edit["cuts"]["reject"].append("t03")
    write_json(p, "edit.json", edit)
    capsys.readouterr()
    assert main(["approve", p.name, "gate1"]) == 1
    assert "storyboard" in json.loads(capsys.readouterr().out)["error"]
    validate(p, capsys)
    assert main(["storyboard", p.name]) == 0
    assert main(["approve", p.name, "gate1"]) == 0


def test_command_prints_the_summary_and_skips_when_nothing_changed(edited_project, capsys):
    p = edited_project
    validate(p, capsys)
    assert main(["storyboard", p.name]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ok"] is True and first["skipped"] is False and first["cuts"] == 5
    assert first["warnings"] == [] and Path(first["html"]).is_file()

    (p.review / "gate1" / "index.html").write_text("marker", encoding="utf-8")
    assert main(["storyboard", p.name]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["skipped"] is True
    assert {k: v for k, v in second.items() if k != "skipped"} == {k: v for k, v in first.items() if k != "skipped"}
    assert (p.review / "gate1" / "index.html").read_text(encoding="utf-8") == "marker"

    assert main(["storyboard", p.name, "--force"]) == 0
    forced = json.loads(capsys.readouterr().out)
    assert forced["skipped"] is False
    assert (p.review / "gate1" / "index.html").read_text(encoding="utf-8") != "marker"


def test_a_changed_edit_rebuilds_the_page(edited_project, capsys):
    p = edited_project
    validate(p, capsys)
    assert main(["storyboard", p.name]) == 0
    first = json.loads(capsys.readouterr().out)
    edit = read_json(p, "edit.json")
    edit["cuts"]["accept"].remove("t03")
    edit["cuts"]["reject"].append("t03")
    write_json(p, "edit.json", edit)
    validate(p, capsys)
    assert main(["storyboard", p.name]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["skipped"] is False and second["cuts"] == first["cuts"] - 1
    assert second["duration_s"] > first["duration_s"]


def test_a_changed_probe_rebuilds_the_page(edited_project, capsys):
    p = edited_project
    validate(p, capsys)
    assert main(["storyboard", p.name]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is False
    assert main(["storyboard", p.name]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is True
    probe = read_json(p, "probe.json")  # nothing but the warnings of probe.json changes
    probe["warnings"] = ["aspect 4:3 will be center-cropped to 9:16"]
    write_json(p, "probe.json", probe)
    assert main(["storyboard", p.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is False and out["warnings"][0] == "aspect 4:3 will be center-cropped to 9:16"
    assert "畫面不是 9:16，會置中裁切成 9:16" in Path(out["html"]).read_text(encoding="utf-8")


def test_md_lists_ids(edited_project):
    md = Path(validated_storyboard(edited_project)["md"]).read_text(encoding="utf-8")
    assert "s01" in md and "t01" in md


def test_md_has_the_columns_and_one_row_per_sentence_and_trim(edited_project):
    p = edited_project
    trims = read_json(p, "trim.json")
    trims["trims"][4]["needs_listen"] = True
    trims["warnings"] = ["few pauses: quiet-point detection unreliable"]
    write_json(p, "trim.json", trims)
    result = validated_storyboard(p)
    md = Path(result["md"]).read_text(encoding="utf-8")
    assert "| 列 | 新時間 | 字幕 | 語意 | 標記 |" in md
    assert "| 編號 | 種類 | 原因 | 辨識文字 | 處理 | 標記 |" in md
    assert len(table_rows(md, "s")) == 4 and len(table_rows(md, "t")) == 5
    assert all(len(cells(row)) == 5 for row in table_rows(md, "s"))
    (s04,) = table_rows(md, "s04")
    assert "最後記得追蹤我" in s04 and "claim" in s04
    (t05,) = table_rows(md, "t05")
    assert "重講" in t05 and "~~最後~~" in t05 and cells(t05)[-1].strip() == "見第 2 項" and "請聽" not in md
    assert "原長" in md and "新長" in md and "停頓很少，切點是否在靜音裡的判斷不可靠" in md
    assert not re.search(r'data:|<img|<button|class="play"', md)


def test_md_marks_unspoken_and_removed_sentences(tmp_path):
    transcript, trims = transcript_and_trims(THREE, asr="第一句話第三句話")
    edit = default_edit(transcript, trims)
    edit["cuts"]["extra"] = [{"from": "s03", "to": "s03", "reason": "off topic"}]
    edit["sentences"] = [s for s in edit["sentences"] if s["id"] != "s03"]
    p = edited_project_from_documents(tmp_path, transcript, trims, edit)
    md = Path(validated_storyboard(p)["md"]).read_text(encoding="utf-8")
    (s02,) = table_rows(md, "s02")
    (s03,) = table_rows(md, "s03")
    assert "沒講到" in s02 and "請聽" not in s02
    assert "移除" in s03 and "off topic" in s03 and "~~第三句話。~~" in s03

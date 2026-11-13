"""Tests for the review page files in pipeline/review_page (index.html, review.css, review.js, logic.js).

logic.js holds the page's pure functions; they are tested with node --test (tests/js/), and their clock and reel
time are checked against storyboard.clock and edit.map_time here. Both skip where node is not installed. The rest
reads the files as text: the page loads nothing from the network, is light and Chinese, shows no id, says the
server's texts word for word, and asks the server by relative URLs only.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline.edit import Segment, map_time
from pipeline.review_server import DENIED_TEXT, PAGE_DIR, SERVER_ERROR_TEXT
from pipeline.storyboard import clock

ROOT = Path(__file__).resolve().parent.parent
FILES = ("index.html", "review.css", "review.js", "logic.js")
NODE_TESTS = ROOT / "tests" / "js"

# The texts of the page that the plan fixes. "{}" stands where review.js puts a value in a template literal.
FIXED_TEXTS = (
    "原長 {} → 新長 {}",
    "剪掉 {} 段",
    "同意，輸出成品",
    "要你決定 {} 項（還沒看 {} 項）",
    "‹ 上一項",
    "下一項 ›",
    "建議聽一下 {} 處",
    "字幕可能有錯字 {} 句",
    "▶ 播放",
    "0.5×",
    "剪掉",
    "保留",
    "（建議）",
    "▶ 播這一段前後",
    "拿掉這一句",
    "保留（錯字會留在成品裡）",
    "▶ 播這一句",
    "要重錄：回到 Claude 對話說「{} 那句要重錄」。",
    "從這裡播放",
    "拿掉了這一句　復原",
    "沒講到，不出字幕",
    "回去再看看",
    "確定，輸出成品",
    "正在重做分鏡稿",
    "正在記下同意",
    "正在輸出成品",
    "已經在對話裡同意了。輸出的進度請看對話；輸出完成後，這裡會播預覽版。",
    "打開所在的資料夾",
    "想用講的：回到 Claude 對話說「1:15 那段保留」也可以",
    "Claude 改了剪接設定，頁面已更新。",
)
ID = re.compile(r"(?<![A-Za-z0-9])[st]\d{2}(?!\d)")  # a sentence or candidate id: s01, t12


def read(name: str) -> str:
    return (PAGE_DIR / name).read_text(encoding="utf-8")


def quoted_strings(source: str) -> list[str]:
    """The contents of the string literals ('...', "...", `...`) of JavaScript source, in order; comments are
    skipped. A template literal's ${...} parts count as its contents."""
    strings: list[str] = []
    i, n = 0, len(source)
    while i < n:
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif source[i] in "'\"`":
            quote, j, content = source[i], i + 1, []
            while j < n and source[j] != quote:
                step = 2 if source[j] == "\\" else 1
                content.append(source[j:j + step])
                j += step
            strings.append("".join(content))
            i = j + 1
        else:
            i += 1
    return strings


def node_or_skip() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the page's pure functions are tested with node --test")
    return node


def test_logic_js_passes_its_node_tests():
    node = node_or_skip()
    tests = sorted(str(path) for path in NODE_TESTS.glob("*.test.mjs"))
    assert tests, "tests/js holds no node tests"
    result = subprocess.run([node, "--test", *tests], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_clock_and_reel_time_are_the_twins_of_storyboard_clock_and_map_time():
    node = node_or_skip()
    segments = [Segment(0.4, 2.15), Segment(3.3, 7.05), Segment(9.9, 12.0)]
    times = [round(i * 0.037, 3) for i in range(400)]  # 0 to 14.763 s: before, inside, between and after them
    code = (
        f"import {{ clock, reelTime }} from {json.dumps((PAGE_DIR / 'logic.js').as_uri())};\n"
        f"const segments = {json.dumps([[s.start, s.end] for s in segments])};\n"
        f"console.log(JSON.stringify({json.dumps(times)}.map((t) => clock(reelTime(t, segments)))));\n"
    )
    result = subprocess.run([node, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [clock(map_time(t, segments)) for t in times]


def test_the_page_loads_nothing_from_the_network():
    for name in FILES:
        text = read(name)
        assert "http://" not in text and "https://" not in text, name
        assert not re.search(r"""(?:src|href)\s*=\s*["']?//|url\(\s*["']?//|@import""", text), name


def test_the_page_is_light_and_in_chinese():
    css, index, js = read("review.css"), read("index.html"), read("review.js")
    assert re.search(r":root\s*\{[^}]*color-scheme:\s*light\s*;", css)
    assert re.search(r"(?m)^body\s*\{[^}]*background:\s*#[0-9a-fA-F]{6}\s*;", css)  # an explicit light background
    assert "prefers-color-scheme" not in css
    assert '<html lang="zh-Hant">' in index
    assert "<title>審閱頁</title>" in index
    page = index + js
    for text in FIXED_TEXTS:
        pattern = r"\$\{[^{}]+\}".join(re.escape(part) for part in text.split("{}"))
        assert re.search(pattern, page), text
    # the approved view does not claim an output runs: Claude may have stopped before the render, or it failed
    assert "正在輸出…" not in page and "同意，正在輸出" not in page


def test_the_page_text_has_no_ids():
    index = read("index.html")
    assert not ID.search(index), ID.findall(index)
    for name in ("review.js", "logic.js"):
        found = [text for text in quoted_strings(read(name)) if ID.search(text)]
        assert not found, (name, found)


def test_quoted_strings_reads_the_literals_and_skips_comments():
    source = 'a("s01"); // don\'t "t02"\nb(\'x\\\'y\', `t${s}`); /* "s03" */ c("//")'
    assert quoted_strings(source) == ["s01", "x\\'y", "t${s}", "//"]


def test_the_page_says_the_server_texts_word_for_word():
    strings = quoted_strings(read("review.js"))
    assert DENIED_TEXT in strings
    assert SERVER_ERROR_TEXT in strings


def test_the_page_asks_the_server_by_relative_urls():
    strings = quoted_strings(read("review.js"))
    for route in ("api/data", "api/state", "api/change", "api/approve", "api/reveal", "media/preview.mp4"):
        assert route in strings, route
    assert not [text for text in strings if text.startswith("/")]
    assert 'request("POST", "api/reveal", {})' in read("review.js")  # JSON, as the server asks of every POST
    assert 'import {' in read("review.js") and 'from "./logic.js";' in read("review.js")
    index = read("index.html")
    for reference in ('href="review.css"', '<script type="module" src="review.js"></script>',
                      'src="media/review.mp4"'):
        assert reference in index, reference
    assert 'url("font/caption.ttf")' in read("review.css")


def test_the_page_offers_caption_editing():
    js = read("review.js")
    strings = quoted_strings(js)
    for text in ("改字幕", "儲存", "取消", "復原成原本的字", "▶ 播這一句", "已改字幕　復原", "講了什麼（辨識的結果）",
                 "原本的字幕（取自腳本）", "字幕要顯示", "會斷成這幾行",
                 "改的字幕還沒存，先按「儲存」或「取消」，再按同意。"):
        assert text in strings, text
    assert "要改字" not in js  # the typo card speaks of recording again only
    assert "api/caption-lines" in strings
    assert re.search(r"^const PREVIEW_MS = 300;", js, re.MULTILINE)  # the lines are asked 300 ms after the typing
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js


def function_text(source: str, name: str) -> str:
    """The text of the top-level JavaScript function `name` in `source`, from its declaration to its closing brace."""
    start = re.search(rf"^(?:async )?function {name}\(", source, re.MULTILINE).start()
    return source[start:source.index("\n}\n", start) + 2]


def test_a_saved_caption_takes_away_the_notice_that_it_was_not_saved():
    js = read("review.js")
    save = function_text(js, "saveEditor")
    assert save.count("hideNotice(UNSAVED_CAPTION_TEXT)") == 2  # saved, and nothing left to save: both close it
    hide = function_text(js, "hideNotice")
    assert "notice.textContent !== text" in hide  # any other notice stays (Claude's change, a refusal)


def test_the_page_polls_every_three_seconds_and_shows_a_notice_for_four():
    js = read("review.js")
    assert re.search(r"^const POLL_MS = 3000;", js, re.MULTILINE)
    assert re.search(r"^const NOTICE_MS = 4000;", js, re.MULTILINE)

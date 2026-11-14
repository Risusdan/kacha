"""Tests for the review page files in pipeline/review_page (index.html, review.css and the script's modules:
review.js, logic.js, shared.js, editor.js).

logic.js holds the page's pure functions; they are tested with node --test (tests/js/), and their clock and reel
time are checked against storyboard.clock and edit.map_time here. tests/js also runs the page's modules on a fake
browser (review_page.test.mjs): the confirm dialog and the caption editor, driven as the user and Claude would. The
node tests run here too; all of this skips where node is not installed. The rest reads the files as text: the page
loads nothing from the network, is light and Chinese, shows no id, says the server's texts word for word, and asks
the server by relative URLs only. The script is split into modules that index.html does not load one by one:
review.js (the views, the player, the changes and the start) imports shared.js (the page state, the requests, the
notices) and editor.js (the caption editor) and logic.js; every import has to resolve to a route of the review
server, and the imports must not form a cycle.
"""

import json
import re
import shutil
import subprocess
from graphlib import TopologicalSorter
from pathlib import Path

import pytest

from pipeline.edit import Segment, map_time
from pipeline.review_server import DENIED_TEXT, PAGE_DIR, PAGE_FILES, SERVER_ERROR_TEXT
from pipeline.storyboard import clock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ("review.js", "logic.js", "shared.js", "editor.js")  # the script of the page, one file for each module
FILES = ("index.html", "review.css", *SCRIPTS)
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


def read_scripts() -> str:
    """The source of every module of the page's script, one after the other."""
    return "\n".join(read(name) for name in SCRIPTS)


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


def script_strings() -> list[str]:
    """The string literals of every module of the page's script."""
    return [text for name in SCRIPTS for text in quoted_strings(read(name))]


def node_or_skip() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the page's modules are tested with node --test")
    return node


def test_the_node_tests_of_the_page_pass():
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


def clock_calls(js: str) -> list[str]:
    """The argument text of every call of clock( in the JavaScript source `js`."""
    return re.findall(r"(?<![\w.])clock\(((?:[^()]|\([^()]*\))*)\)", js)


def test_the_page_prints_the_times_the_server_sent_and_formats_only_its_running_clock():
    # data.source_clock, data.duration_clock and the time of a listen item are storyboard.clock of the unrounded
    # time (review_data); a time the page rounded again from the seconds it was sent could differ by 0.1 s
    js = read("review.js")
    assert sorted(clock_calls(js)) == ["0"]  # the start of the track; the player's running clock is playerClock's
    for sent in ("data.source_clock", "data.duration_clock", "item.time"):
        assert sent in js, sent
    assert "${data.source_clock} → 新長 ${data.duration_clock}" in js
    assert "新長 ${data.duration_clock}，剪掉" in js  # the confirm dialog
    assert 'textContent = data.duration_clock' in js  # the end of the track
    # the running clock is the length string itself at the end of the reel (playerClock), where clock of the rounded
    # seconds could read another tenth than the length it is shown with
    assert "${playerClock(t, data.segments, data.duration_s, data.duration_clock)} / ${data.duration_clock}" in js
    assert not [name for name in ("shared.js", "editor.js") if clock_calls(read(name))]  # only review.js


def test_the_page_loads_nothing_from_the_network():
    for name in FILES:
        text = read(name)
        assert "http://" not in text and "https://" not in text, name
        assert not re.search(r"""(?:src|href)\s*=\s*["']?//|url\(\s*["']?//|@import""", text), name


def test_the_page_is_light_and_in_chinese():
    css, index, js = read("review.css"), read("index.html"), read_scripts()
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
    for name in SCRIPTS:
        found = [text for text in quoted_strings(read(name)) if ID.search(text)]
        assert not found, (name, found)


def test_quoted_strings_reads_the_literals_and_skips_comments():
    source = 'a("s01"); // don\'t "t02"\nb(\'x\\\'y\', `t${s}`); /* "s03" */ c("//")'
    assert quoted_strings(source) == ["s01", "x\\'y", "t${s}", "//"]


def test_the_page_says_the_server_texts_word_for_word():
    strings = script_strings()
    assert DENIED_TEXT in strings
    assert SERVER_ERROR_TEXT in strings


def test_the_page_asks_the_server_by_relative_urls():
    strings = script_strings()
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
    js = read_scripts()
    strings = script_strings()
    for text in ("改字幕", "儲存", "取消", "復原成原本的字", "▶ 播這一句", "已改字幕　復原", "講了什麼（辨識的結果）",
                 "原本的字幕（取自腳本）", "字幕要顯示", "會斷成這幾行",
                 "改的字幕還沒存，先按「儲存」或「取消」，再按同意。", "剪接設定剛被更新，你改到一半的字幕沒有存。"):
        assert text in strings, text
    assert "要改字" not in js  # the typo card speaks of recording again only
    assert "api/caption-lines" in strings
    assert re.search(r"^const PREVIEW_MS = 300;", js, re.MULTILINE)  # the lines are asked 300 ms after the typing
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js


def function_text(source: str, name: str) -> str:
    """The text of the top-level JavaScript function `name` in `source`, from its declaration to its closing brace."""
    start = re.search(rf"^(?:export )?(?:async )?function {name}\(", source, re.MULTILINE).start()
    return source[start:source.index("\n}\n", start) + 2]


def test_a_saved_caption_takes_away_the_notice_that_it_was_not_saved():
    save = function_text(read("editor.js"), "saveEditor")
    assert save.count("hideNotice(UNSAVED_CAPTION_TEXT)") == 2  # saved, and nothing left to save: both close it
    hide = function_text(read("shared.js"), "hideNotice")
    assert "notice.textContent !== text" in hide  # any other notice stays (Claude's change, a refusal)


CAPTION_DROPPED_TEXT = "剪接設定剛被更新，你改到一半的字幕沒有存。"


def test_a_caption_editor_the_page_closes_on_its_own_says_when_it_drops_typed_text():
    # The behaviour (the notice shows, and stays when the same update says that Claude changed the edit) is checked
    # on a DOM; here only that every way the page closes the editor on its own goes through dropEditor, which says it.
    editor, page = read("editor.js"), read("review.js")  # the caption editor; the views and the changes
    assert re.search(r"^const CAPTION_DROPPED_TEXT = \"%s\";" % CAPTION_DROPPED_TEXT, editor, re.MULTILINE)
    drop = function_text(editor, "dropEditor")
    assert "unsavedText(editor)" in drop and "notify(CAPTION_DROPPED_TEXT, true)" in drop and "closeEditor()" in drop
    assert "hasUnsavedCaption(" in function_text(editor, "unsavedText")  # the test of logic.js, which node tests
    assert "unsavedText(editor)" in function_text(page, "openConfirm")  # and 同意 waits on the same one
    for source, name in ((editor, "placeEditor"), (page, "renderView")):  # the sentence went, or the page left editing
        body = function_text(source, name)
        assert "dropEditor()" in body and "closeEditor()" not in body, name
    # the user's own ways to close it say nothing: 儲存, 取消, 同意 with nothing to save, a new editor
    assert "closeEditor()" in function_text(editor, "saveEditor")
    assert "dropEditor" not in function_text(editor, "saveEditor")
    # the notice that Claude changed the edit does not replace it within the same update
    for name in ("applyStatus", "sendChange"):
        body = function_text(page, name)
        assert "notify(UPDATED_TEXT)" not in body and "notifyUnlessDropped(" in body, name


UNSAVED_OPEN_TEXT = "改的字幕還沒存，先按「儲存」或「取消」，再改別句。"


def test_opening_another_caption_editor_keeps_the_open_one_that_holds_unsaved_text():
    # The behaviour (the second editor does not open, the first keeps its text and focus, the notice shows) is
    # checked on a DOM; here only that openEditor asks the pure decision before it closes anything.
    js = read("editor.js")
    assert re.search(r"^const UNSAVED_OPEN_TEXT = \"%s\";" % UNSAVED_OPEN_TEXT, js, re.MULTILINE)
    assert UNSAVED_OPEN_TEXT in quoted_strings(js)
    opening = function_text(js, "openEditor")
    keep = opening.index("keepsOpenEditor(")
    assert keep < opening.index("closeEditor()")  # decided before the open editor is closed
    assert "notify(UNSAVED_OPEN_TEXT, true)" in opening and "return;" in opening[keep:opening.index("closeEditor()")]
    # a saved caption takes the notice away, as it does UNSAVED_CAPTION_TEXT's
    assert function_text(js, "saveEditor").count("hideNotice(UNSAVED_OPEN_TEXT)") == 2


def test_a_reload_that_changes_the_saved_caption_moves_a_field_nothing_was_typed_in():
    # The behaviour (the field shows the caption Claude wrote, the caret goes to its end, the green marks follow) is
    # checked on a DOM; here only that placeEditor asks the pure decision and then redraws the field's marks.
    placing = function_text(read("editor.js"), "placeEditor")
    take = placing.index("fieldAfterReload(")
    assert take < placing.index("editor.saved = sentence.caption")  # decided against the caption it had before
    assert "showMarks(editor)" in placing[take:] and "input.value = " in placing[take:]


def test_the_page_polls_every_three_seconds_and_shows_a_notice_for_four():
    assert re.search(r"^const POLL_MS = 3000;", read("review.js"), re.MULTILINE)
    assert re.search(r"^const NOTICE_MS = 4000;", read("shared.js"), re.MULTILINE)  # notify is in shared.js


# An import statement of the page's script: import {a, b} from "./x.js"; or import "./x.js";
IMPORT = re.compile(r"^import\s+(?:[\w$*{},\s]+?\s+from\s+)?\"([^\"]+)\"\s*;", re.MULTILINE)  # $ is a JS name


def imports_of(name: str) -> list[str]:
    """The module specifiers the file `name` of the page imports, in order."""
    source = read(name)
    assert not re.search(r"\bimport\s*\(", source), name  # no dynamic import: the routes are fixed, the graph static
    assert not re.search(r"^export\b[^;{]*\bfrom\b", source, re.MULTILINE), name  # no re-export from another file
    found = IMPORT.findall(source)
    # every import statement is one IMPORT reads (none in single quotes or over several lines that it would miss)
    assert len(re.findall(r"^import\b", source, re.MULTILINE)) == len(found), name
    return found


def test_every_import_of_the_page_is_a_served_route_and_the_imports_form_no_cycle():
    # index.html loads review.js only; a browser asks for each module it imports by that URL, relative to the page,
    # so each one needs a route of its own in PAGE_FILES (the server joins nothing else to a file path).
    assert '<script type="module" src="review.js"></script>' in read("index.html")
    on_disk = sorted(path.name for path in PAGE_DIR.glob("*.js"))
    served = sorted(name for name, kind in PAGE_FILES.values() if kind.startswith("text/javascript"))
    assert on_disk == served == sorted(SCRIPTS)  # no module is left without its route, and none is named in vain
    graph: dict[str, set[str]] = {}
    for name in SCRIPTS:
        graph[name] = set()
        for specifier in imports_of(name):
            assert re.fullmatch(r"\./[a-z]+\.js", specifier), (name, specifier)  # flat, relative, no query
            route = "/" + specifier[2:]
            assert route in PAGE_FILES and PAGE_FILES[route][0] == specifier[2:], (name, specifier)
            assert (PAGE_DIR / specifier[2:]).is_file(), (name, specifier)
            graph[name].add(specifier[2:])
    # no cycle (static_order raises CycleError): a module is evaluated after everything it imports, so no module
    # runs while one it needs is still waiting to be evaluated
    order = list(TopologicalSorter(graph).static_order())
    assert order[-1] == "review.js"  # the page starts last, from the module nothing imports
    assert not [name for name, found in graph.items() if "review.js" in found]
    # every module is reached from the one script the page loads
    seen, todo = set(), ["review.js"]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(graph[name])
    assert seen == set(SCRIPTS)
    assert graph["logic.js"] == set()  # the pure functions import nothing


def test_the_modules_review_js_imports_run_no_startup_code():
    # Only review.js binds the page and starts it (bind, the first load, the poll loop). The others can be imported
    # with no page at all, as a test with a fake DOM does: node imports them here with no document, no fetch and no
    # timer, so they only declare what review.js uses.
    node = node_or_skip()
    names = ("logic.js", "shared.js", "editor.js")
    code = "".join(f"import {json.dumps((PAGE_DIR / name).as_uri())};\n" for name in names)
    result = subprocess.run([node, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""

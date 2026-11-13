import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from helpers import caption_dictionary

from pipeline import words
from pipeline.errors import PipelineError
from pipeline.paths import repo_root
from pipeline.words import word_ends

# A creator's word the dictionary does not hold whole: its parts are words of their own.
SPLIT_WORD = "分鏡腳本"
SPLIT_TEXT = "先寫好分鏡腳本再開拍"


@pytest.fixture(autouse=True)
def fresh_tokenizer():
    """Every test builds its own tokenizer: the one a test leaves behind may hold another cache folder or word list."""
    words._tokenizer.cache_clear()
    yield
    words._tokenizer.cache_clear()


def inside(ends: frozenset[int], text: str, word: str) -> bool:
    """Whether some word end falls inside the first `word` of `text`, that is, whether a line could break in it."""
    at = text.index(word)
    return any(at < i < at + len(word) for i in ends)


def test_word_ends_keep_taiwan_and_creator_words_whole():
    caption_dictionary()
    for text, kept in [
        ("把影片上傳到Instagram以後記得加上標籤", ["上傳", "記得", "標籤"]),
        ("其實自媒體經營最重要的是持續產出", ["自媒體", "經營"]),
        ("記得按讚追蹤並且分享給需要的朋友", ["按讚", "追蹤", "並且"]),
        ("用手機錄影時記得開啟飛航模式", ["手機", "錄影", "飛航"]),
        ("我馬上傳給你", ["馬上"]),
    ]:
        ends = word_ends(text)
        for word in kept:
            assert not inside(ends, text, word), (text, word)


def test_word_ends_put_latin_words_and_numbers_whole():
    caption_dictionary()
    text = "我用iPhone 15 Pro拍了3.5小時"
    ends = word_ends(text)
    for word in ["iPhone", "15", "Pro", "3.5"]:
        assert not inside(ends, text, word), word


def test_word_ends_are_the_places_between_two_words():
    caption_dictionary()
    assert word_ends("按讚追蹤") == frozenset({2})
    text = "記得按讚追蹤並且分享給需要的朋友"
    ends = word_ends(text)
    assert isinstance(ends, frozenset)
    assert all(isinstance(i, int) and 0 < i < len(text) for i in ends)
    assert {2, 4, 6, 8}.issubset(ends)


def test_word_ends_of_no_text_and_of_one_character_are_empty():
    caption_dictionary()
    assert word_ends("") == frozenset()
    assert word_ends("好") == frozenset()


def listed_words() -> list[str]:
    return [line.strip() for line in words.WORDS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


# Creator words the dictionary alone splits (短/影音, 互動/率, 限時/動態, 粉絲/專頁), each in a sentence of its own.
ADDED_WORDS = {
    "短影音": "我最近在做短影音的企劃",
    "互動率": "這篇貼文的互動率很高",
    "限時動態": "記得去看我的限時動態喔",
    "粉絲專頁": "歡迎追蹤我的粉絲專頁",
}


def test_word_list_holds_the_creator_words_and_the_dictionary_alone_would_split_two_of_them():
    caption_dictionary()
    assert {"上傳", "自媒體", "按讚", "觸及率", "CP值"} <= set(listed_words())
    assert words.WORD_FREQ == 2000
    for text, kept in [("這篇貼文的觸及率很高", "觸及率"), ("這個方案的CP值很高", "CP值"), ("我最近在做短影音的企劃", "短影音")]:
        assert not inside(word_ends(text), text, kept)


def test_the_listed_creator_words_that_the_dictionary_alone_splits_are_kept_whole(tmp_path, monkeypatch):
    caption_dictionary()
    assert set(ADDED_WORDS) <= set(listed_words())
    for word, text in ADDED_WORDS.items():
        assert not inside(word_ends(text), text, word), word
    empty = tmp_path / "words.txt"
    empty.write_text("\n", encoding="utf-8")
    monkeypatch.setattr(words, "WORDS_FILE", empty)
    for word, text in ADDED_WORDS.items():
        assert inside(word_ends(text), text, word), word  # why the word is on the list


def test_a_changed_word_list_is_read_again_without_a_restart(tmp_path, monkeypatch):
    caption_dictionary()
    word_file = tmp_path / "words.txt"
    word_file.write_text("\n", encoding="utf-8")
    monkeypatch.setattr(words, "WORDS_FILE", word_file)
    assert inside(word_ends(SPLIT_TEXT), SPLIT_TEXT, SPLIT_WORD)  # the dictionary alone splits it
    word_file.write_text(f"\n{SPLIT_WORD}\n\n", encoding="utf-8")
    stamp = word_file.stat().st_mtime_ns + 10**9
    os.utime(word_file, ns=(stamp, stamp))  # a file system with a coarse clock may leave the mtime as it was
    assert not inside(word_ends(SPLIT_TEXT), SPLIT_TEXT, SPLIT_WORD)


def test_word_ends_without_the_dictionary_says_run_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(words, "dictionary_path", lambda: tmp_path / "absent.txt.big")
    with pytest.raises(PipelineError) as error:
        word_ends("今天天氣很好")
    assert error.value.message == "caption dictionary not found: run `uv run pipeline setup`"


def jieba_caches() -> set[tuple[str, int]]:
    """(name, mtime) of every jieba cache file in the system temporary folder, where jieba writes by default."""
    return {(p.name, p.stat().st_mtime_ns) for p in Path(tempfile.gettempdir()).glob("jieba*.cache")}


def test_jieba_writes_no_file_outside_the_models_folder_and_nothing_to_the_terminal(tmp_path, monkeypatch, capfd, caplog):
    caption_dictionary()
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(words, "models_dir", lambda: models)  # the folder the cache goes to; the dictionary stays
    before = jieba_caches()
    capfd.readouterr()
    word_ends("把影片上傳到雲端以後記得加上標籤")
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert caplog.records == []  # jieba's "Building prefix dict" lines are log records
    assert (models / "jieba-dict.cache").is_file()
    assert [p.name for p in models.iterdir()] == ["jieba-dict.cache"]  # no temporary file is left
    assert jieba_caches() == before


def test_importing_and_using_the_module_prints_nothing_even_when_jieba_is_compiled_again(tmp_path):
    caption_dictionary()
    models = tmp_path / "models"
    models.mkdir()
    # Python prints jieba's invalid-escape SyntaxWarnings when it compiles the package; the empty bytecode
    # folder makes it compile jieba again, as it does on a machine that never imported it.
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "import pipeline.assets\n"
        "sys.pycache_prefix = sys.argv[1]\n"
        "from pipeline import words\n"
        "words.models_dir = lambda: Path(sys.argv[2])\n"
        "print(sorted(words.word_ends('把影片上傳到雲端')))\n"
    )
    done = subprocess.run(
        [sys.executable, "-W", "default", "-c", script, str(tmp_path / "bytecode"), str(models)],
        capture_output=True, text=True, cwd=repo_root(), timeout=300,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert done.stdout.startswith("[") and done.stdout.endswith("]\n")
    assert (models / "jieba-dict.cache").is_file()

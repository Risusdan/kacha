"""Tests for pipeline.trim: rough-cut candidates, sentence cut points, work/trim.json and the trim
command."""

import json

import pytest
from helpers import (
    QUIET_DB,
    caption_dictionary,
    flat_envelope,
    synth_wav,
    transcript_and_trims,
    transcript_of,
    trims_for,
)

from pipeline import words
from pipeline.audio import HOP_S, Envelope, envelope, noise_floor_db, silence_threshold_db
from pipeline.cli import main
from pipeline.errors import PipelineError
from pipeline.project import load_state
from pipeline.trim import (
    LEAD_S,
    PAUSE_KEEP_S,
    PAUSE_MIN_PIECE_S,
    PAUSE_MIN_S,
    build_trims,
    pause_lengths,
    pause_reason,
    trim,
)

FRAME = 1 / 30


def _of_kind(tr: dict, kind: str) -> list[dict]:
    return [t for t in tr["trims"] if t["kind"] == kind]


def _assert_well_formed(tr: dict) -> None:
    """Every trim is a real stretch and the list is in order. No two trims that suggest a cut overlap,
    nor do two listed ones (a trim that suggests a cut may lie inside a listed one or across its edge)."""
    trims = tr["trims"]
    assert all(t["start"] < t["end"] for t in trims)
    assert [(t["start"], t["end"]) for t in trims] == sorted((t["start"], t["end"]) for t in trims)
    for suggest in ("cut", "list"):
        group = [t for t in trims if t["suggest"] == suggest]
        for earlier, later in zip(group, group[1:]):
            assert earlier["end"] <= later["start"] + 1e-9, (earlier, later)


def _inside(inner: dict, outer: dict) -> bool:
    return outer["start"] <= inner["start"] < inner["end"] <= outer["end"]


def _assert_clear_of_matched_words(transcript: dict, tr: dict) -> None:
    """No trim takes more than a frame's rounding from a recognised unit matched to the script."""
    matched = [(t["start"], t["end"]) for t in transcript["tokens"] if t["s"] is not None]
    for trim in tr["trims"]:
        for start, end in matched:
            assert min(trim["end"], end) - max(trim["start"], start) <= FRAME / 2 + 1e-9, (trim, start, end)


def test_constants():
    assert (PAUSE_MIN_S, PAUSE_KEEP_S, PAUSE_MIN_PIECE_S, LEAD_S) == (0.7, 0.35, 0.1, 0.3)


# ---- pauses -----------------------------------------------------------------------------------


def test_long_pause_compressed():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 2.1, 3.0)])
    p = [t for t in tr["trims"] if t["kind"] == "pause"]; assert len(p) == 1
    kept = 1.2 - (p[0]["end"] - p[0]["start"])
    assert abs(kept - 0.35) <= 0.1 and p[0]["suggest"] == "cut"


def test_short_pause_kept():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 1.5, 2.4)])
    assert not [t for t in tr["trims"] if t["kind"] == "pause"]


def test_pause_of_exactly_the_minimum_is_kept():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 1.6, 2.5)])
    assert _of_kind(tr, "pause") == []


def test_pause_fields():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 2.1, 3.0)])
    (p,) = tr["trims"]
    assert p["id"] == "t01" and p["text"] == "" and p["needs_listen"] is False
    assert p["sentence"] == "s02"  # a pause between two sentences belongs to the one after it, as a run does
    assert p["reason"].startswith("pause 1.20s -> 0.3")


def test_pause_reason_round_trips():
    assert pause_reason(1.2, 0.3) == "pause 1.20s -> 0.30s"
    assert pause_lengths(pause_reason(0.8, 0.37)) == (0.8, 0.37)
    assert pause_lengths("filler 嗯") is None


def test_no_pause_before_the_first_word_or_after_the_last():
    tr = trims_for("今天我們來聊剪輯。", asr=[("今天我們來聊剪輯", 2.0, 3.6)])
    assert tr["trims"] == []  # the 2 s of silence before the speech is cut_in's business, not a pause
    tr = trims_for("第一句。", asr=[("第一句", 0.0, 0.9), ("嗯", 3.0, 3.2)])
    assert _of_kind(tr, "pause") == []  # nor the silence after the last spoken word


def test_pause_inside_a_retake_is_not_a_trim_of_its_own():
    tr = trims_for(
        "第一句。今天我們來聊剪輯。",
        asr=[("第一句", 0.0, 0.9), ("今天", 1.0, 1.4), ("我們", 2.6, 3.0), ("今天我們來聊剪輯", 3.2, 4.8)],
    )
    assert [t["kind"] for t in tr["trims"]] == ["retake"]


def test_pauses_next_to_a_retake_are_shrunk_to_the_retake():
    tr = trims_for(
        "第一句。今天我們來聊剪輯。",
        asr=[("第一句", 0.0, 0.9), ("今天我們", 2.0, 2.8), ("今天我們來聊剪輯", 4.0, 5.6)],
    )
    before, retake, after = tr["trims"]
    assert [t["kind"] for t in tr["trims"]] == ["pause", "retake", "pause"]
    assert before["end"] == retake["start"] and after["start"] == retake["end"]
    assert before["start"] == pytest.approx(0.9 + PAUSE_KEEP_S / 2, abs=FRAME)  # the far ends are untouched
    assert after["end"] == pytest.approx(4.0 - PAUSE_KEEP_S / 2, abs=FRAME)
    _assert_well_formed(tr)


def test_pause_next_to_a_filler_is_shrunk_to_the_filler():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("嗯", 1.2, 1.5), ("第二句", 2.7, 3.6)])
    filler, = _of_kind(tr, "filler")
    pause, = _of_kind(tr, "pause")
    _assert_well_formed(tr)
    assert pause["start"] == pytest.approx(filler["end"])  # what the filler cut leaves of the pause
    assert pause["end"] == pytest.approx(2.7 - PAUSE_KEEP_S / 2, abs=FRAME)  # the end is untouched
    assert filler["start"] < 1.2 < 1.5 < filler["end"]  # the filler cut takes the whole 嗯


def test_a_pause_inside_kept_extra_speech_is_still_shortened():
    # The extra speech 這個真的很重要 has a 1.2 s gap after 的 (1.8 to 3.0 s).
    tr = trims_for(
        "第一句話。第二句話。",
        asr=[("第一句話", 0.0, 0.8), ("這個真的", 1.0, 1.8), ("很重要", 3.0, 3.6), ("第二句話", 3.8, 4.6)],
    )
    extra, = _of_kind(tr, "extra")
    pause, = _of_kind(tr, "pause")
    assert _inside(pause, extra) and pause["suggest"] == "cut"
    assert pause["start"] == pytest.approx(1.8 + PAUSE_KEEP_S / 2, abs=FRAME)
    assert pause["end"] == pytest.approx(3.0 - PAUSE_KEEP_S / 2, abs=FRAME)
    _assert_well_formed(tr)


def test_a_pause_inside_a_reworded_take_is_still_shortened():
    # The reworded take 今天咱們來講 has a 1.2 s gap after 們 (1.9 to 3.1 s).
    tr = trims_for(
        "第一句。今天我們來聊剪輯。",
        asr=[("第一句", 0.0, 0.9), ("今天咱們", 1.1, 1.9), ("來講", 3.1, 3.5), ("今天我們來聊剪輯", 3.7, 5.3)],
    )
    reworded, = _of_kind(tr, "retake")
    pause, = _of_kind(tr, "pause")
    assert reworded["suggest"] == "list" and _inside(pause, reworded)
    assert pause["start"] == pytest.approx(1.9 + PAUSE_KEEP_S / 2, abs=FRAME)
    assert pause["end"] == pytest.approx(3.1 - PAUSE_KEEP_S / 2, abs=FRAME)


def test_a_pause_next_to_a_listed_take_is_not_shortened_by_it():
    # Kept, the reworded take leaves the 1.2 s gap before it (0.9 to 2.1 s) to the pause alone.
    tr = trims_for(
        "第一句。今天我們來聊剪輯。",
        asr=[("第一句", 0.0, 0.9), ("今天咱們來講", 2.1, 3.3), ("今天我們來聊剪輯", 3.5, 5.1)],
    )
    reworded, = _of_kind(tr, "retake")
    pause, = _of_kind(tr, "pause")
    assert reworded["suggest"] == "list" and reworded["start"] == pytest.approx(1.5, abs=FRAME)
    assert pause["start"] == pytest.approx(0.9 + PAUSE_KEEP_S / 2, abs=FRAME)
    assert pause["end"] == pytest.approx(2.1 - PAUSE_KEEP_S / 2, abs=FRAME)


@pytest.mark.parametrize("hole,pauses", [(1.36, 0), (1.40, 1)])
def test_a_pause_left_shorter_than_a_tenth_of_a_second_is_no_candidate(hole, pauses):
    # The 0.8 s gap from 1.0 to 1.8 s before the 嗯 is speech-loud except two 10 ms quiet windows, at
    # 1.30 s and at `hole`. The pause starts in the first (frame 39, 1.300 s); the 嗯 cut starts in the
    # second, the one nearest to the middle of the gap: frame 41 (1.367 s) leaves the pause 2 frames,
    # frame 42 (1.400 s) leaves it 3, which is 0.1 s.
    loud = [(1.0, 1.3), (1.31, hole), (hole + 0.01, 1.8)]
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 1.0), ("嗯", 1.8, 2.0), ("第二句", 2.3, 3.2)], loud=loud)
    filler, = _of_kind(tr, "filler")
    assert filler["start"] == pytest.approx(round(hole * 30) / 30)
    assert len(_of_kind(tr, "pause")) == pauses
    for pause in _of_kind(tr, "pause"):
        assert (pause["start"], pause["end"]) == (pytest.approx(1.3), pytest.approx(filler["start"]))


# ---- runs -------------------------------------------------------------------------------------


@pytest.mark.parametrize("asr,kind,suggest", [
    ("嗯今天我們來聊剪輯", "filler", "cut"),
    ("今天我們今天我們來聊剪輯", "retake", "cut"),
    ("今天今天我們來聊剪輯", "repeat", "cut"),
    ("今天咱們來講今天我們來聊剪輯", "retake", "list"),
])
def test_run_trims(asr, kind, suggest):
    t = trims_for("今天我們來聊剪輯。", asr=asr)["trims"][0]
    assert (t["kind"], t["suggest"]) == (kind, suggest)


def test_run_trim_covers_the_run_and_only_the_run():
    tr = trims_for("今天我們來聊剪輯。", asr="今天我們今天我們來聊剪輯")  # the NG take is 0.0 to 0.8, all quiet
    t, = tr["trims"]
    assert t["start"] == pytest.approx(0.0) and t["end"] == pytest.approx(0.8)
    assert t["text"] == "今天我們" and t["sentence"] == "s01" and t["needs_listen"] is False


def test_run_trim_edges_are_the_middle_of_the_gaps_next_to_it():
    tr = trims_for(
        "第一句。今天我們來聊剪輯。",
        asr=[("第一句", 0.0, 0.9), ("今天我們", 1.5, 2.3), ("今天我們來聊剪輯", 2.9, 4.5)],
    )
    t, = _of_kind(tr, "retake")
    assert t["start"] == pytest.approx((0.9 + 1.5) / 2, abs=FRAME)
    assert t["end"] == pytest.approx((2.3 + 2.9) / 2, abs=FRAME)


def test_reasons():
    assert trims_for("今天我們來聊剪輯。", asr="今天我們今天我們來聊剪輯")["trims"][0]["reason"] == "earlier take of s01"
    assert trims_for("今天我們來聊剪輯。", asr="今天咱們來講今天我們來聊剪輯")["trims"][0]["reason"] == "reworded take of s01"
    assert trims_for("今天我們來聊剪輯。", asr="嗯今天我們來聊剪輯")["trims"][0]["reason"] == "filler 嗯"
    assert trims_for("今天我們來聊剪輯。", asr="今天今天我們來聊剪輯")["trims"][0]["reason"] == "repeated 今天"


@pytest.mark.parametrize("asr,phrase", [
    ("今天我們來講重來今天我們來聊剪輯", "重來"),
    ("今天我們來講重呃來今天我們來聊剪輯", "重來"),  # the command as the run records it, 呃 left out
    ("今天我們來講不對呃今天我們來聊剪輯", "不對"),
])
def test_a_command_retake_names_the_command(asr, phrase):
    t, = trims_for("今天我們來聊剪輯。", asr=asr)["trims"]
    assert (t["kind"], t["suggest"]) == ("retake", "cut")
    assert t["reason"] == f"earlier take of s01 ended with {phrase}"


def test_extra_speech_is_a_listed_candidate():
    tr = trims_for("第一句話。第二句話。", asr="第一句話這個真的很重要第二句話")
    extra = [t for t in tr["trims"] if t["kind"] == "extra"]
    assert [(t["text"], t["suggest"], t["length"]) for t in extra] == [("這個真的很重要", "list", 7)]
    assert (extra[0]["reason"], extra[0]["sentence"]) == ("extra speech 這個真的很重要", "s02")
    assert extra[0]["start"] == pytest.approx(0.8) and extra[0]["end"] == pytest.approx(2.2)  # all of 這 to 要


def test_extra_speech_before_the_first_or_after_the_last_matched_unit_is_no_candidate():
    tr = trims_for("第一句話。", asr="大家好第一句話謝謝收看")
    assert [t for t in tr["trims"] if t["kind"] == "extra"] == []


def test_extra_speech_in_a_sentence_shown_as_recognised_gives_no_candidate_at_all():
    # The second sentence is said quite unlike its script, so its caption shows the recognised text;
    # the extra speech 級多的這個呃 in it is on the caption, 呃 included.
    transcript, tr = transcript_and_trims(
        "第一句話。這個方法可以省下時間。第二句話。", asr="第一句話这招能帮你省掉超级多的这个呃事情第二句話"
    )
    assert transcript["sentences"][1]["display_source"] == "asr"
    assert [(r["kind"], r["text"], r["sentence"]) for r in transcript["runs"]] == [("extra", "級多的這個呃", "s02")]
    assert tr["trims"] == []


@pytest.mark.parametrize("source,expected", [("script", [("retake", "乙丙", "list")]), ("asr", [])])
def test_a_reworded_take_in_a_sentence_shown_as_recognised_gives_no_candidate(source, expected):
    # The caption of a sentence shown as recognised holds the units of its reworded takes as well as of its
    # extra speech (align leaves out only fillers, repeats and NG takes): cutting the take would leave words
    # of the caption unheard. Hand-built: a reworded take 乙丙 before the sentence 丁戊 it belongs to.
    def token(text, start, end, s):
        return {"text": text, "start": start, "end": end, "op": "match" if s else "ins", "s": s}

    tokens = [token("甲", 0.0, 0.5, "s01"), token("乙", 1.0, 1.3, None), token("丙", 1.3, 1.6, None),
              token("丁", 2.0, 2.3, "s02"), token("戊", 2.3, 2.6, "s02")]
    runs = [{"h0": 1, "h1": 3, "kind": "reworded", "text": "乙丙", "similarity": 0.5, "command": None, "sentence": "s02"}]
    first = {"id": "s01", "text": "甲", "display_source": "script", "spoken": True, "start": 0.0, "end": 0.5,
             "ratio": 1.0, "needs_listen": False, "reasons": [], "units": []}
    shown_from = 1.0 if source == "asr" else 2.0  # a caption of recognised text starts with the take
    second = {"id": "s02", "text": "乙丙丁戊" if source == "asr" else "丁戊", "display_source": source, "spoken": True,
              "start": shown_from, "end": 2.6, "ratio": 0.4, "needs_listen": True, "reasons": [], "units": []}
    transcript = {"version": 2, "audio_s": 3.0, "tokens": tokens, "runs": runs, "sentences": [first, second],
                  "markers": []}
    tr = build_trims(transcript, flat_envelope(4.0, loud=[(0.0, 0.5), (1.0, 1.6), (2.0, 2.6)]))
    assert [(t["kind"], t["text"], t["suggest"]) for t in tr["trims"] if t["kind"] != "pause"] == expected


@pytest.mark.parametrize("asr,where", [
    ("第一句話呃這個很重要第二句話", "start"),
    ("第一句話這個呃很重要第二句話", "middle"),
    ("第一句話這個很重要呃第二句話", "end"),
])
def test_a_hesitation_inside_extra_speech_is_its_own_cut_inside_it(asr, where):
    tr = trims_for("第一句話。第二句話。", asr=asr)
    extra, = _of_kind(tr, "extra")
    filler, = _of_kind(tr, "filler")
    assert filler["suggest"] == "cut" and extra["start"] <= filler["start"] < filler["end"] <= extra["end"]
    assert extra["length"] == 5
    assert (filler["text"], filler["reason"], filler["length"], filler["sentence"]) == ("呃", "filler 呃", 0, "s02")
    hesitation = asr.index("呃") * 0.2  # every unit is 0.2 s
    assert filler["start"] == pytest.approx(hesitation) and filler["end"] == pytest.approx(hesitation + 0.2)
    assert (filler["start"] == extra["start"]) is (where == "start")
    assert (filler["end"] == extra["end"]) is (where == "end")
    _assert_well_formed(tr)


def test_a_hesitation_inside_a_reworded_take_is_its_own_cut():
    tr = trims_for("今天我們來聊剪輯。", asr="今天咱們呃來講今天我們來聊剪輯")
    assert [(t["kind"], t["text"], t["suggest"]) for t in tr["trims"]] == [
        ("retake", "今天咱們呃來講", "list"), ("filler", "呃", "cut"),
    ]
    reworded, filler = tr["trims"]
    assert _inside(filler, reworded) and filler["reason"] == "filler 呃"
    assert filler["start"] == pytest.approx(0.8) and filler["end"] == pytest.approx(1.0)


def test_every_hesitation_of_extra_speech_is_a_cut_of_its_own():
    tr = trims_for("第一句話。第二句話。", asr="第一句話嗯這個呃很重要欸第二句話")
    extra, = _of_kind(tr, "extra")
    assert [t["text"] for t in _of_kind(tr, "filler")] == ["嗯", "呃", "欸"]
    assert all(_inside(t, extra) for t in _of_kind(tr, "filler")) and extra["length"] == 5


@pytest.mark.parametrize("extra", ["真的很感恩大家", "額外補充一下", "這個金額很高"])
def test_a_hesitation_character_inside_a_word_is_no_hesitation(extra):
    # 恩 and 額 are hesitations only where the caption dictionary makes them a word of their own: the 恩 of
    # 感恩 and the 額 of 額外 and 金額 are part of a word, so no filler cut takes them and they are counted.
    caption_dictionary()
    tr = trims_for("第一句話。第二句話。", asr=f"第一句話{extra}第二句話")
    assert [(t["kind"], t["text"], t["length"]) for t in tr["trims"]] == [("extra", extra, len(extra))]


@pytest.mark.parametrize("extra,fillers,length", [("然後恩這個", ["恩"], 4), ("呃呃我想一下", ["呃", "呃"], 4)])
def test_a_hesitation_that_is_a_word_of_its_own_is_still_a_cut_of_its_own(extra, fillers, length):
    caption_dictionary()
    tr = trims_for("第一句話。第二句話。", asr=f"第一句話{extra}第二句話")
    run, = _of_kind(tr, "extra")
    assert (run["text"], run["length"]) == (extra, length)
    assert [t["text"] for t in _of_kind(tr, "filler")] == fillers
    assert all(_inside(t, run) for t in _of_kind(tr, "filler"))


@pytest.mark.parametrize("extra,fillers,length", [
    ("對額我覺得", ["額"], 4),
    ("額額這個", ["額", "額"], 2),
    ("恩恩這個", ["恩", "恩"], 2),
    ("我恩覺得", ["恩"], 3),
])
def test_a_hesitation_next_to_a_one_character_word_is_still_a_cut_of_its_own(extra, fillers, length):
    # Only the dictionary decides where a word ends: jieba's guessing of new words would glue 額 or 恩 to a
    # neighbouring character (對額, 額額, 恩恩, 我恩), which no dictionary word is.
    caption_dictionary()
    tr = trims_for("第一句話。第二句話。", asr=f"第一句話{extra}第二句話")
    run, = _of_kind(tr, "extra")
    assert (run["text"], run["length"]) == (extra, length)
    assert [t["text"] for t in _of_kind(tr, "filler")] == fillers


def test_a_filler_run_of_two_hesitations_has_no_length():
    caption_dictionary()
    t, = trims_for("第一句話。第二句話。", asr="第一句話恩恩第二句話")["trims"]
    assert (t["kind"], t["text"], t["length"]) == ("filler", "恩恩", 0)


def test_trim_reads_the_caption_dictionary_only_for_a_run_of_more_than_one_unit_with_a_hesitation(tmp_path,
                                                                                                    monkeypatch):
    monkeypatch.setattr(words, "dictionary_path", lambda: tmp_path / "absent.txt.big")
    # a lone 嗯, a repeat, and extra speech without a hesitation character: no dictionary needed
    for script, asr, kind, text in [
        ("第一句。第二句。", [("第一句", 0.0, 0.9), ("嗯", 1.2, 1.5), ("第二句", 2.7, 3.6)], "filler", "嗯"),
        ("今天我們來聊剪輯。", "今天今天我們來聊剪輯", "repeat", "今天"),
        ("第一句話。第二句話。", "第一句話用reels很方便第二句話", "extra", "用reels很方便"),
    ]:
        assert (kind, text) in [(t["kind"], t["text"]) for t in trims_for(script, asr=asr)["trims"]]
    with pytest.raises(PipelineError) as error:
        trims_for("第一句話。第二句話。", asr="第一句話然後恩這個第二句話")
    assert error.value.message == "caption dictionary not found: run `uv run pipeline setup`"


def test_a_hesitation_in_extra_speech_before_the_first_matched_unit_is_still_cut():
    # The extra speech 大家好 is not listed, as nothing before the first matched unit is in the reel;
    # its 呃 is a filler candidate as a 呃 said alone there would be.
    tr = trims_for("今天我們來聊剪輯。", asr="大家好呃今天我們來聊剪輯")
    assert [(t["kind"], t["text"]) for t in tr["trims"]] == [("filler", "呃")]


@pytest.mark.parametrize("asr", ["今天我們呃今天我們來聊剪輯", "今天我們來講重呃來今天我們來聊剪輯"])
def test_an_ng_take_keeps_its_hesitations_inside_one_cut(asr):
    t, = trims_for("今天我們來聊剪輯。", asr=asr)["trims"]
    assert (t["kind"], t["suggest"]) == ("retake", "cut") and "呃" in t["text"]
    assert t["length"] == len(t["text"]) - 1  # the 呃 is not counted


@pytest.mark.parametrize("script,asr,expected", [
    ("第一句。第二句。", [("第一句", 0.0, 0.9), ("第二句", 2.1, 3.0)], [("pause", "", 0)]),
    ("今天我們來聊剪輯。", "嗯今天我們來聊剪輯", [("filler", "嗯", 0)]),
    ("今天我們來聊剪輯。", "那個今天我們來聊剪輯", [("filler", "那個", 2)]),
    ("今天我們來聊剪輯。", "今天今天我們來聊剪輯", [("repeat", "今天", 2)]),
    ("今天我們來聊剪輯。", "今天我們呃今天我們來聊剪輯", [("retake", "今天我們呃", 4)]),
    ("今天我們來聊剪輯。", "今天咱們呃來講今天我們來聊剪輯", [("retake", "今天咱們呃來講", 6), ("filler", "呃", 0)]),
    ("第一句話。第二句話。", "第一句話用reels呃很方便第二句話", [("extra", "用reels呃很方便", 5), ("filler", "呃", 0)]),
])
def test_every_candidate_has_a_length(script, asr, expected):
    # A Chinese character and an English word count 1 each; a one-character hesitation does not count.
    tr = trims_for(script, asr=asr)
    assert [(t["kind"], t["text"], t["length"]) for t in tr["trims"]] == expected


def test_misrecognised_script_text_never_cut():
    assert trims_for("今天我們來聊剪輯。", asr="今天我們來談剪輯")["trims"] == []      # 談≠聊，是替換


def test_run_cut_in_speech_needs_listening():
    asr = "嗯今天我們來聊剪輯"
    assert trims_for("今天我們來聊剪輯。", asr=asr)["trims"][0]["needs_listen"] is False
    assert trims_for("今天我們來聊剪輯。", asr=asr, loud=[(0.0, 1.8)])["trims"][0]["needs_listen"] is True


def test_one_cut_point_in_speech_is_enough_to_need_listening():
    # The start of the filler has quiet in reach (0 to 0.05 s); its end has only speech around it.
    t = trims_for("今天我們來聊剪輯。", asr="嗯今天我們來聊剪輯", loud=[(0.05, 1.8)])["trims"][0]
    assert t["needs_listen"] is True


# ---- ids, grid, sentence cut points ---------------------------------------------------------------


def test_ids_sorted_and_all_times_on_grid():
    tr = trims_for("第一句。第二句。", asr=[("嗯第一句", 0.0, 1.2), ("第二句", 2.4, 3.3)])
    assert [t["id"] for t in tr["trims"]] == [f"t{i:02d}" for i in range(1, len(tr["trims"]) + 1)]
    pts = [x for t in tr["trims"] for x in (t["start"], t["end"])] + [x for b in tr["bounds"].values() for x in (b["cut_in"], b["cut_out"])]
    assert all(abs(x * 30 - round(x * 30)) < 1e-9 for x in pts)


def test_trims_are_ordered_by_start_whatever_their_kind():
    tr = trims_for(
        "第一句。今天我們來聊剪輯。",
        asr=[("第一句", 0.0, 0.9), ("嗯", 1.1, 1.3), ("第一", 1.4, 1.9), ("今天我們來聊剪輯", 3.5, 5.1)],
    )
    _assert_well_formed(tr)
    assert [t["id"] for t in tr["trims"]] == [f"t{i:02d}" for i in range(1, len(tr["trims"]) + 1)]


def test_document_shape():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 2.1, 3.0)])
    assert set(tr) == {"version", "noise_floor_db", "warnings", "trims", "bounds"} and tr["version"] == 2
    assert tr["noise_floor_db"] == QUIET_DB and tr["warnings"] == []
    assert list(tr["trims"][0]) == [
        "id", "kind", "start", "end", "sentence", "text", "reason", "suggest", "length", "needs_listen",
    ]
    assert set(tr["bounds"]["s01"]) == {"cut_in", "cut_out", "needs_listen"}


def test_bounds_of_two_sentences():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 2.1, 3.0)])
    assert list(tr["bounds"]) == ["s01", "s02"]
    assert tr["bounds"]["s01"]["cut_in"] == pytest.approx(0.0)  # nothing before the first word
    assert tr["bounds"]["s01"]["cut_out"] == pytest.approx(0.9 + LEAD_S, abs=FRAME)  # the gap is 1.2 s: 0.3 s is kept
    assert tr["bounds"]["s02"]["cut_in"] == pytest.approx(2.1 - LEAD_S, abs=FRAME)
    assert tr["bounds"]["s02"]["cut_out"] == pytest.approx(3.0, abs=FRAME)  # the audio ends with the last word
    assert not any(b["needs_listen"] for b in tr["bounds"].values())


def test_bounds_take_the_middle_of_a_gap_shorter_than_twice_the_lead():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 1.3, 2.2)])
    assert tr["bounds"]["s01"]["cut_out"] == pytest.approx(1.1, abs=FRAME)
    assert tr["bounds"]["s02"]["cut_in"] == pytest.approx(1.1, abs=FRAME)


def test_bounds_head_and_tail_keep_at_most_the_lead():
    tr = trims_for("今天我們來聊剪輯。", asr=[("今天我們來聊剪輯", 2.0, 3.6)])
    assert tr["bounds"]["s01"]["cut_in"] == pytest.approx(2.0 - LEAD_S, abs=FRAME)


def test_bounds_are_searched_inside_the_lead():
    # Quiet until 1.65 s and speech from there on: the first word is at 2.0 s, so the cut may not go
    # earlier than 1.70 s however quiet it is out there; the cut lands in speech and needs listening.
    tr = trims_for("今天我們來聊剪輯。", asr=[("今天我們來聊剪輯", 2.0, 3.6)], loud=[(1.65, 3.6)])
    assert tr["bounds"]["s01"]["cut_in"] >= 2.0 - LEAD_S - FRAME / 2
    assert tr["bounds"]["s01"]["needs_listen"] is True
    # The same at the tail: speech until 1.25 s, quiet after, the last word ends at 0.9 s.
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 5.0, 5.9)], loud=[(0.0, 1.25), (4.0, 6.0)])
    assert tr["bounds"]["s01"]["cut_out"] <= 0.9 + LEAD_S + FRAME / 2


def test_bounds_next_to_a_filler():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("嗯", 1.2, 1.5), ("第二句", 2.7, 3.6)])
    filler, = _of_kind(tr, "filler")
    assert tr["bounds"]["s01"]["cut_out"] == filler["start"]  # both are the middle of the 0.9 to 1.2 gap
    assert tr["bounds"]["s02"]["cut_in"] == pytest.approx(2.7 - LEAD_S, abs=FRAME)  # the filler is before it


def test_bound_needing_listening():
    asr = [("第一句", 0.0, 0.9), ("第二句", 2.1, 3.0)]
    quiet = trims_for("第一句。第二句。", asr=asr)["bounds"]
    assert not quiet["s01"]["needs_listen"] and not quiet["s02"]["needs_listen"]
    loud = trims_for("第一句。第二句。", asr=asr, loud=[(0.0, 3.0)])["bounds"]  # speech where the cut_in of s02 falls
    assert loud["s02"]["needs_listen"] is True


def test_unspoken_sentence_has_no_bounds():
    tr = trims_for("第一句話。第二句話。第三句話。", asr="第一句話第三句話")
    assert list(tr["bounds"]) == ["s01", "s03"]


def test_nothing_spoken_gives_nothing():
    tr = trims_for("第一句話。", asr="天氣")
    assert tr["trims"] == [] and tr["bounds"] == {}


# ---- cuts and the words around them ---------------------------------------------------------------


def test_a_cut_does_not_enter_the_word_it_touches():
    # The repeated 今天 touches the 今天 that stays, so the cut has no gap to go in. The only quiet
    # window within reach lies inside the word that stays (0.50 to 0.52 s), but the cut ends at 0.4 s.
    transcript = transcript_of("今天我們來聊剪輯。", "今天今天我們來聊剪輯")
    tr = build_trims(transcript, flat_envelope(2.8, loud=[(0.0, 0.5), (0.52, 1.8)]))
    repeat, = tr["trims"]
    assert repeat["kind"] == "repeat" and repeat["end"] == pytest.approx(0.4)
    assert repeat["needs_listen"] is True  # 0.4 s is in the middle of speech
    _assert_clear_of_matched_words(transcript, tr)


def test_cuts_stay_clear_of_matched_words():
    for script, asr in [
        ("第一句。第二句。", [("嗯第一句", 0.0, 1.2), ("第二句", 2.4, 3.3)]),
        ("第一句。今天我們來聊剪輯。", [("第一句", 0.0, 0.9), ("今天我們", 2.0, 2.8), ("今天我們來聊剪輯", 4.0, 5.6)]),
        ("今天我們來聊剪輯。", "今天今天我們來聊剪輯"),
    ]:
        transcript = transcript_of(script, asr)
        _assert_clear_of_matched_words(transcript, build_trims(transcript, flat_envelope(transcript["audio_s"] + 1)))


def test_overlapping_recognised_times_do_not_make_trims_overlap():
    def token(text, start, end, s):
        return {"text": text, "start": start, "end": end, "op": "match" if s else "ins", "s": s}

    tokens = [token("甲", 0.0, 1.0, "s01"), token("嗯", 1.1, 1.9, None), token("乙", 1.2, 1.3, "s01"),
              token("嗯", 1.4, 1.5, None), token("丙", 2.1, 3.0, "s01")]
    runs = [{"h0": 1, "h1": 2, "kind": "filler", "text": "嗯", "similarity": 0.0, "command": None, "sentence": "s01"},
            {"h0": 3, "h1": 4, "kind": "filler", "text": "嗯", "similarity": 0.0, "command": None, "sentence": "s01"}]
    sentence = {"id": "s01", "text": "甲乙丙", "display_source": "script", "spoken": True, "start": 0.0, "end": 3.0,
                "ratio": 1.0, "needs_listen": False, "reasons": [], "units": []}
    transcript = {"version": 1, "audio_s": 3.0, "tokens": tokens, "runs": runs, "sentences": [sentence], "markers": []}
    tr = build_trims(transcript, flat_envelope(4.0))
    first, second = tr["trims"]
    assert first["end"] == second["start"] and second["start"] < second["end"]
    _assert_well_formed(tr)


# ---- the end of the audio -----------------------------------------------------------------------------


def _points(tr: dict) -> list[float]:
    return [x for t in tr["trims"] for x in (t["start"], t["end"])] + [
        x for b in tr["bounds"].values() for x in (b["cut_in"], b["cut_out"])
    ]


def test_cut_out_does_not_pass_the_end_of_the_audio():
    # The last word ends with the audio at 1.62 s, which is not on the frame grid: frame 49 (1.6333 s)
    # is the grid point nearest to the end, but it is after the audio. The cut is the frame before.
    tr = trims_for("今天我們來聊剪輯。", asr=[("今天我們來聊剪輯", 0.0, 1.62)], pad_s=0)
    assert tr["bounds"]["s01"]["cut_out"] == pytest.approx(1.6)
    assert tr["bounds"]["s01"]["cut_in"] == 0.0  # and the first word starts at 0.0


@pytest.mark.parametrize("end", [1.5, 1.6, 1.62, 1.63, 1.64])
def test_no_cut_lies_outside_the_audio(end):
    for asr in ([("今天我們來聊剪輯", 0.0, end)], [("嗯", 0.0, 0.2), ("今天我們來聊剪輯", 0.2, end)]):
        points = _points(trims_for("今天我們來聊剪輯。", asr=asr, pad_s=0))
        assert points and all(0.0 <= x <= end + 1e-9 for x in points)


def test_no_cut_is_before_the_start_of_the_audio():
    # A recognised time before 0 (out of contract, but a cut must still lie in the audio).
    tokens = [{"text": "嗯", "start": -0.04, "end": 0.1, "op": "ins", "s": None},
              {"text": "甲", "start": 0.1, "end": 1.0, "op": "match", "s": "s01"}]
    runs = [{"h0": 0, "h1": 1, "kind": "filler", "text": "嗯", "similarity": 0.0, "command": None, "sentence": "s01"}]
    sentence = {"id": "s01", "text": "甲", "display_source": "script", "spoken": True, "start": 0.1, "end": 1.0,
                "ratio": 1.0, "needs_listen": False, "reasons": [], "units": []}
    tr = build_trims({"version": 1, "audio_s": 1.0, "tokens": tokens, "runs": runs, "sentences": [sentence],
                      "markers": []}, flat_envelope(3.0, loud=[(1.0, 3.0)]))
    assert tr["trims"] and all(x >= 0.0 for x in _points(tr))


# ---- the silence threshold on footage with few pauses or digital silence ---------------------------


def test_cuts_in_speech_are_flagged_when_the_speech_has_no_pause(tmp_path):
    # Speech all the way, with a 10 dB dip every 0.35 s (at 0.30, 0.65, 1.00, 1.35, 1.70, 2.05 and
    # 2.40 s). The recogniser hears gaps the room does not have, so every cut looks for the quietest
    # window and finds a dip: still speech, so every trim and every sentence cut point is flagged.
    env = envelope(synth_wav(tmp_path, [("tone", 0.30, -20), ("tone", 0.05, -30)] * 8))
    transcript = transcript_of("第一句。第二句。", [("第一句", 0.35, 0.9), ("嗯", 1.4, 1.7), ("第二句", 2.0, 2.4)])
    tr = build_trims(transcript, env)
    assert [t["kind"] for t in tr["trims"]] == ["filler"]
    assert all(t["needs_listen"] for t in tr["trims"]) and all(b["needs_listen"] for b in tr["bounds"].values())
    assert tr["warnings"] == ["few pauses: quiet-point detection unreliable"]


def test_room_noise_cuts_are_not_flagged_when_a_fifth_of_the_clip_is_digital_zero(tmp_path):
    parts = [("silence", 1.2, 0), ("noise", 0.3, -65), ("tone", 0.9, -20), ("noise", 0.6, -65), ("tone", 0.3, -20),
             ("noise", 0.6, -65), ("tone", 0.9, -20), ("noise", 0.7, -65)]  # speech at 1.5, 3.0 and 3.9 s
    env = envelope(synth_wav(tmp_path, parts))
    transcript = transcript_of("第一句。第二句。", [("第一句", 1.5, 2.4), ("嗯", 3.0, 3.3), ("第二句", 3.9, 4.8)])
    tr = build_trims(transcript, env)
    assert abs(tr["noise_floor_db"] - (-65)) < 2
    assert [t["kind"] for t in tr["trims"]] == ["filler"]
    assert not tr["trims"][0]["needs_listen"] and not any(b["needs_listen"] for b in tr["bounds"].values())
    assert tr["warnings"] == []


# ---- the Meijia fixture ----------------------------------------------------------------------------


def _db_at(env: Envelope, t: float) -> float:
    return float(env.db[min(int(t / HOP_S + 1e-9), len(env.db) - 1)])


def test_fixture_trims(trimmed_project):
    tr = json.loads((trimmed_project.work / "trim.json").read_text())
    assert tr["warnings"] == []
    assert {"pause", "retake", "repeat"} <= {t["kind"] for t in tr["trims"]}
    assert all(t["suggest"] == "cut" for t in tr["trims"])


def test_fixture_trims_are_the_designed_events(trimmed_project):
    tr = json.loads((trimmed_project.work / "trim.json").read_text())
    transcript = json.loads((trimmed_project.work / "transcript.json").read_text())
    by_kind = {k: [t for t in tr["trims"] if t["kind"] == k] for k in ("pause", "retake", "filler", "repeat")}
    assert {k: len(v) for k, v in by_kind.items()} == {"pause": 2, "retake": 1, "filler": 1, "repeat": 1}
    retake, filler, repeat = by_kind["retake"][0], by_kind["filler"][0], by_kind["repeat"][0]
    assert (retake["text"], filler["text"], repeat["text"]) == ("第一步是把停頓", "嗯", "最後")
    assert retake["start"] < 3.92 < 5.36 < retake["end"] <= 5.76  # the half take 3.92 to 5.36, and the quiet around it
    assert 9.60 < filler["start"] < 10.16 < 10.40 < filler["end"] < 10.56
    first_pause, second_pause = by_kind["pause"]  # 2.56 to 3.92 (up to the retake), 13.52 to 14.80 (up to the repeat)
    assert 2.56 < first_pause["start"] and first_pause["end"] == retake["start"]
    assert 13.52 < second_pause["start"] and second_pause["end"] == repeat["start"]
    _assert_well_formed(tr)
    _assert_clear_of_matched_words(transcript, tr)


def test_fixture_flags_follow_the_measured_levels(trimmed_project):
    tr = json.loads((trimmed_project.work / "trim.json").read_text())
    env = envelope(trimmed_project.work / "audio48k.wav")
    threshold = silence_threshold_db(env)
    assert tr["noise_floor_db"] == round(noise_floor_db(env), 1)
    for trim in tr["trims"]:
        quiet = all(_db_at(env, t) <= threshold for t in (trim["start"], trim["end"]))
        assert trim["needs_listen"] is (not quiet), trim
    for sid, bound in tr["bounds"].items():
        quiet = all(_db_at(env, t) <= threshold for t in (bound["cut_in"], bound["cut_out"]))
        assert bound["needs_listen"] is (not quiet), (sid, bound)
    # Only the repeated 最後 touches the word after it, so only it has a cut point inside speech.
    assert [t["kind"] for t in tr["trims"] if t["needs_listen"]] == ["repeat"]


def test_fixture_bounds(trimmed_project):
    tr = json.loads((trimmed_project.work / "trim.json").read_text())
    transcript = json.loads((trimmed_project.work / "transcript.json").read_text())
    assert list(tr["bounds"]) == [s["id"] for s in transcript["sentences"] if s["spoken"]]
    for sentence in transcript["sentences"]:
        bound = tr["bounds"][sentence["id"]]
        assert bound["cut_in"] <= sentence["start"] + FRAME / 2 and bound["cut_out"] >= sentence["end"] - FRAME / 2
        assert bound["cut_in"] >= sentence["start"] - LEAD_S - FRAME and bound["cut_out"] <= sentence["end"] + LEAD_S + FRAME
    assert tr["bounds"]["s01"]["cut_in"] == 0.0
    assert tr["bounds"]["s04"]["cut_out"] <= transcript["audio_s"]


def test_fixture_trim_json_does_not_change_between_runs(trimmed_project):
    first = (trimmed_project.work / "trim.json").read_bytes()
    assert main(["trim", trimmed_project.name, "--force"]) == 0
    assert (trimmed_project.work / "trim.json").read_bytes() == first


# ---- the trim command ---------------------------------------------------------------------------------


def _summary_of(project) -> dict:
    doc = json.loads((project.work / "trim.json").read_text())
    trims = doc["trims"]
    return {
        "trims": len(trims),
        "by_kind": {k: sum(t["kind"] == k for t in trims) for k in {t["kind"] for t in trims}},
        "suggest_cut": sum(t["suggest"] == "cut" for t in trims),
        "needs_listen": sum(t["needs_listen"] for t in trims),
        "warnings": doc["warnings"],
    }


def test_trim_command_reports_and_records_the_step(trimmed_project, capsys):
    capsys.readouterr()
    assert main(["trim", trimmed_project.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, **_summary_of(trimmed_project), "skipped": True}  # the fixture ran it once already
    record = load_state(trimmed_project)["steps"]["trim"]
    assert set(record["inputs"]) == {"transcript", "audio"} and record["outputs"] == ["work/trim.json"]


def test_trim_command_force_runs_again(trimmed_project, capsys):
    capsys.readouterr()
    assert main(["trim", trimmed_project.name, "--force"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, **_summary_of(trimmed_project), "skipped": False}
    assert out["trims"] == 5 and out["suggest_cut"] == 5 and out["needs_listen"] == 1


def test_trim_runs_again_when_the_transcript_changes(trimmed_project, capsys):
    path = trimmed_project.work / "transcript.json"
    doc = json.loads(path.read_text())
    doc["runs"] = [run for run in doc["runs"] if run["kind"] != "retake"]
    path.write_text(json.dumps(doc, ensure_ascii=False))
    capsys.readouterr()
    assert main(["trim", trimmed_project.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is False and "retake" not in out["by_kind"]


def test_trim_needs_the_transcript(project, capsys):
    capsys.readouterr()
    assert main(["trim", project.name]) == 1
    assert "transcript" in json.loads(capsys.readouterr().out)["error"]


def test_trim_refuses_a_transcript_of_an_older_version(transcribed_project):
    path = transcribed_project.work / "transcript.json"
    transcript = json.loads(path.read_text(encoding="utf-8"))
    transcript["version"] = 1
    path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(PipelineError) as refused:
        trim(transcribed_project, force=True)
    assert refused.value.message == "transcript.json was made by an older pipeline: run transcribe again"

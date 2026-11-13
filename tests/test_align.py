"""Tests for pipeline.align: the script-to-speech alignment, insertion-run classes, transcript.json
and the transcribe command."""

import importlib.metadata
import json
import time

import pytest
from helpers import MEIJIA, alts_of, fixture_transcript, keys, project_with, transcript_of
from media_fixtures import make_clip

from pipeline import align as align_module
from pipeline.align import Op, align, classify_run, command_phrase
from pipeline.asr import MODELS
from pipeline.cli import main
from pipeline.errors import PipelineError
from pipeline.project import load_state
from pipeline.textnorm import to_taiwan

RECORDED = MEIJIA / "asr-qwen3-1.7b.json"


# ---- align ----------------------------------------------------------------------------------


def test_exact_match():
    assert [o.kind for o in align(alts_of("今天好"), keys("今天好"))] == ["match"] * 3


def test_homophone_is_match():
    assert align(alts_of("我的書"), keys("我地書"))[1].kind == "match"


def test_retake_binds_last_take():
    ops = align(alts_of("甲乙丙丁好"), keys("甲乙甲乙丙丁好"))
    assert [(o.kind, o.h0, o.h1) for o in ops][:2] == [("ins", 0, 1), ("ins", 1, 2)]
    assert all(o.kind == "match" for o in ops[2:])


def test_number_spoken_differently_matches():
    ops = align(alts_of("省下150分鐘"), keys("省下一百五分鐘"))
    assert [o.kind for o in ops] == ["match"] * 5 and ops[2].h1 - ops[2].h0 == 3


def test_number_as_digits_matches():
    ops = align(alts_of("省下150分鐘"), keys("省下") + [frozenset({"#150"})] + keys("分鐘"))
    assert all(o.kind == "match" for o in ops)


def test_align_1500_under_5s():
    text = "我們今天來聊短影音剪輯的流程" * 107
    t0 = time.perf_counter(); align(alts_of(text), keys(text)); assert time.perf_counter() - t0 < 5


def test_deleted_unit_uses_no_asr_unit():
    assert align(alts_of("今天好"), keys("今好")) == [Op("match", 0, 0, 1), Op("del", 1, 1, 1), Op("match", 2, 1, 2)]


def test_empty_sides():
    assert align([], keys("你好")) == [Op("ins", None, 0, 1), Op("ins", None, 1, 2)]
    assert align(alts_of("你好"), []) == [Op("del", 0, 0, 0), Op("del", 1, 0, 0)]


def test_number_reading_with_a_wrong_character_is_one_sub():
    ops = align(alts_of("省下150分鐘"), keys("省下一百六分鐘"))
    assert [o.kind for o in ops] == ["match", "match", "sub", "match", "match"] and (ops[2].h0, ops[2].h1) == (2, 5)


def test_secondary_cost_never_overrides_the_brief_cost():
    # Deleting 丙 inside the sentence is the only alignment of brief cost 1.
    ops = align(alts_of("甲乙丙丁戊"), keys("甲乙丁戊"))
    assert [(o.kind, o.s) for o in ops] == [("match", 0), ("match", 1), ("del", 2), ("match", 3), ("match", 4)]
    # With an extra 己 at the end, three subs would avoid the deletion inside the sentence (brief cost 3,
    # secondary 0); the deletion plus one insertion costs 2 and must still win (an additive cost would
    # make it 4 against 3 and pick the subs).
    ops = align(alts_of("甲乙丙丁戊"), keys("甲乙丁戊己"))
    assert [(o.kind, o.s) for o in ops] == [
        ("match", 0), ("match", 1), ("del", 2), ("match", 3), ("match", 4), ("ins", None),
    ]


def test_sentence_boundaries_steer_equal_cost_deletions():
    # 第一句話第三句話 against three sentences: four alignments cost 4. As one sentence the tie order
    # lets 第二句話 take the 句話 spoken in the first; told where the sentences end, it is deleted whole.
    alts, heard = alts_of("第一句話第二句話第三句話"), keys("第一句話第三句話")
    loose = align(alts, heard)
    assert {o.s for o in loose if o.kind == "del"} != {4, 5, 6, 7}
    ops = align(alts, heard, [0] * 4 + [1] * 4 + [2] * 4)
    assert {o.s for o in ops if o.kind == "del"} == {4, 5, 6, 7}
    assert all(o.kind == "match" for o in ops if o.kind != "del")


# ---- classify_run ---------------------------------------------------------------------------


@pytest.mark.parametrize("run,prev,nxt,psent,nsent,kind", [
    ("嗯", "剪輯", "這個", "短影音剪輯", "這個方法", "filler"),
    ("那個就是", "剪輯", "這個", "短影音剪輯", "這個方法", "filler"),
    ("第一步是把", "剪輯", "第一步是把停頓", "短影音剪輯", "第一步是把停頓剪掉", "retake"),
    ("我們", "", "我們來聊", "", "我們來聊", "repeat"),
    ("第一招是先", "剪輯", "第一步是把", "短影音剪輯", "第一步是把停頓剪掉", "reworded"),
    ("順便說一下", "剪輯", "這個", "短影音剪輯", "這個方法", "extra"),
])
def test_classify_run(run, prev, nxt, psent, nsent, kind):
    assert classify_run(keys(run), keys(prev), keys(nxt), keys(psent), keys(nsent))[0] == kind


def test_classify_run_similarity():
    assert classify_run(keys("第一步是把"), [], [], [], keys("第一步是把停頓剪掉")) == ("retake", 1.0)
    assert classify_run(keys("第一招是先"), [], [], [], keys("第一步是把停頓剪掉")) == ("reworded", 0.6)


def test_restart_of_the_sentence_just_finished_is_a_retake():
    # Said in full, then started again and dropped: compared with the start of the previous sentence.
    assert classify_run(keys("第一步是把"), keys("剪掉"), keys("這個"), keys("第一步是把停頓剪掉"), keys("這個方法"))[0] == "retake"


def test_repeat_of_the_words_just_before():
    assert classify_run(keys("剪輯"), keys("短影音剪輯"), keys("這個"), keys("短影音剪輯"), keys("這個方法"))[0] == "repeat"


def test_short_run_like_a_sentence_start_is_not_a_retake():
    assert classify_run(keys("第一步"), [], keys("這個"), [], keys("第一步是把停頓"))[0] == "extra"


def test_hesitations_are_the_one_character_filler_spellings():
    assert align_module.HESITATIONS == ("嗯", "呃", "欸", "額", "誒", "恩")


def test_classify_run_by_unit_texts():
    # Without run_text, the units' texts joined are the run's text.
    assert classify_run(keys("謝謝"), [], [], [], [], unit_texts=["謝", "謝"])[0] == "extra"
    assert classify_run(keys("額那個"), [], [], [], [], unit_texts=["額", "那", "個"])[0] == "filler"
    with pytest.raises(ValueError, match="unit_texts"):
        classify_run(keys("謝謝"), [], [], [], [], unit_texts=["謝"])


def test_a_hesitation_does_not_make_a_run_long_enough():
    # Without its 呃 the run is three units, fewer than a take needs (RETAKE_MIN_UNITS), and exactly
    # like the sentence start.
    run = "第一呃步"
    assert classify_run(keys(run), [], keys("這個"), [], keys("第一步是把停頓"), unit_texts=list(run)) == ("extra", 1.0)


def test_command_phrase():
    assert command_phrase("第二個做法不對重來") == "重來"
    assert command_phrase("我再講一次呃") == "我再講一次"
    assert command_phrase("這樣不對嗎") is None


# ---- build_transcript -----------------------------------------------------------------------


def test_ratio_and_low_match():
    t = transcript_of("一二三四五六七八九十。", asr="一二三四五六七八九拾")    # helper：parse_script + 假 AsrResult
    assert t["sentences"][0]["ratio"] == 1.0                                 # 拾與十同音
    t = transcript_of("一二三四五六七八九十。", asr="一二三四五六七八天地")
    s = t["sentences"][0]; assert s["ratio"] == 0.8 and "low_match" in s["reasons"] and s["needs_listen"]


def test_extra_speech_flags_sentence():
    s = transcript_of("今天我們來聊剪輯。", asr="今天我們順便說一下來聊剪輯")["sentences"][0]
    assert s["ratio"] == 1.0 and "extra_speech" in s["reasons"] and s["needs_listen"]


def test_skipped_sentence_not_spoken():
    t = transcript_of("第一句話。第二句話。第三句話。", asr="第一句話第三句話")
    assert [s["spoken"] for s in t["sentences"]] == [True, False, True]


def test_skipped_parallel_sentence_keeps_its_neighbours_whole():
    # Under the brief's cost and tie order alone, the second sentence would take the 剪掉 spoken in the
    # first one; the secondary cost keeps the skipped sentence whole.
    t = transcript_of(
        "第一個方法是先把停頓剪掉。第二個方法是先把贅字剪掉。第三個方法是先把重講剪掉。",
        asr="第一個方法是先把停頓剪掉第三個方法是先把重講剪掉",
    )
    s1, s2, s3 = t["sentences"]
    assert [s["spoken"] for s in t["sentences"]] == [True, False, True]
    assert (s1["ratio"], s2["ratio"], s3["ratio"]) == (1.0, 0.0, 1.0)
    assert s2["start"] == s2["end"] == s1["end"] and all(u["start"] == s1["end"] for u in s2["units"])


def test_reworded_sentence_uses_asr_text():
    s = transcript_of("這個方法可以省下時間。", asr="这招能帮你省很多事")["sentences"][0]
    assert s["display_source"] == "asr" and s["text"] == "這招能幫你省很多事" and "asr_text" in s["reasons"]


def test_asr_text_units_carry_asr_times_and_spans():
    s = transcript_of(
        "這個方法可以省下時間。", asr=[("这招", 1.0, 1.4), ("reels", 1.4, 1.8), ("shorts", 1.8, 2.2), ("省很多事", 2.2, 3.0)]
    )["sentences"][0]
    assert s["display_source"] == "asr" and s["text"] == "這招reels shorts省很多事"
    assert all(s["text"][a:b] == u["text"] for u, (a, b) in ((u, u["span"]) for u in s["units"]))
    assert [(u["text"], u["start"], u["end"], u["src"]) for u in s["units"][:3]] == [
        ("這", 1.0, 1.2, "asr"), ("招", 1.2, 1.4, "asr"), ("reels", 1.4, 1.8, "asr"),
    ]
    assert (s["start"], s["end"]) == (1.0, 3.0)


def test_asr_text_leaves_out_fillers_and_repeats():
    # Inside a reworded sentence a filler is usually absorbed as a sub (as cheap as an insertion plus a
    # deletion); after the last bound unit it is a run of its own and stays out, while extra speech
    # (級多的) goes in.
    for asr, kind in (("这招能帮你省掉超级多的事情嗯", "filler"), ("这招能帮你省掉超级多的事情事情", "repeat")):
        t = transcript_of("這個方法可以省下時間。", asr=asr)
        assert [(r["text"], r["kind"], r["sentence"]) for r in t["runs"]][0] == ("級多的", "extra", "s01")
        assert [(r["kind"], r["sentence"]) for r in t["runs"]][1:] == [(kind, "s01")]
        s = t["sentences"][0]
        assert s["display_source"] == "asr" and s["text"] == "這招能幫你省掉超級多的事情"


def test_latin_word_time_is_interpolated():
    u = transcript_of("用Reels做影片。", asr="用reels做影片", times={"reels": (5.0, 5.2)})["sentences"][0]["units"]
    assert u[1]["src"] == "interp" and u[1]["start"] == u[0]["end"] and u[1]["end"] == u[2]["start"]


def test_script_unit_text_and_span_come_from_the_sentence():
    s = transcript_of("用Reels省下1,500秒。", asr="用reels省下一千五百秒")["sentences"][0]
    assert [(u["text"], u["span"]) for u in s["units"]] == [
        ("用", [0, 1]), ("Reels", [1, 6]), ("省", [6, 7]), ("下", [7, 8]), ("1,500", [8, 13]), ("秒", [13, 14]),
    ]
    assert (s["units"][4]["start"], s["units"][4]["end"]) == (0.8, 1.6)  # 一千五百: four ASR units


def test_latin_word_at_sentence_edge_keeps_its_asr_time_inside_its_neighbour():
    u = transcript_of("Reels很好用。", asr="reels很好用")["sentences"][0]["units"]
    assert (u[0]["start"], u[0]["end"], u[0]["src"]) == (0.0, 0.2, "asr")
    u = transcript_of("Reels很好用。", asr="reels很好用", times={"reels": (3.0, 3.2)})["sentences"][0]["units"]
    assert u[0]["end"] <= u[1]["start"] and [x["start"] for x in u] == sorted(x["start"] for x in u)


def test_clamped_latin_word_is_interp():
    # Kept inside its neighbour (很 starts at 0.2 s, 用 ends at 0.6 s), the word has no length left: the
    # time is no longer its own.
    u = transcript_of("Reels很好用。", asr="reels很好用", times={"reels": (3.0, 3.2)})["sentences"][0]["units"]
    assert (u[0]["start"], u[0]["end"], u[0]["src"]) == (0.2, 0.2, "interp")
    u = transcript_of("很好用Reels。", asr="很好用reels", times={"reels": (0.0, 0.1)})["sentences"][0]["units"]
    assert (u[3]["start"], u[3]["end"], u[3]["src"]) == (0.6, 0.6, "interp")


def test_deleted_unit_between_timed_units_is_interpolated():
    u = transcript_of("今天我們來聊剪輯。", asr=[("今天我們", 0.0, 0.8), ("聊剪輯", 1.0, 1.6)])["sentences"][0]["units"]
    assert (u[4]["text"], u[4]["start"], u[4]["end"], u[4]["src"]) == ("來", 0.8, 1.0, "interp")


def test_deleted_unit_at_sentence_edge_has_zero_length():
    u = transcript_of("今天好。", asr="今天")["sentences"][0]["units"]
    assert (u[2]["start"], u[2]["end"], u[2]["src"]) == (0.4, 0.4, "interp")
    u = transcript_of("好今天。", asr=[("今天", 1.0, 1.4)])["sentences"][0]["units"]
    assert (u[0]["start"], u[0]["end"], u[0]["src"]) == (1.0, 1.0, "interp")


def test_runs_belong_to_the_sentence_around_or_after_them():
    t = transcript_of("今天天氣很好。我們去爬山吧。", asr="嗯今天真的天氣很好順便說一下我們去爬山吧好累")
    assert [(r["text"], r["sentence"]) for r in t["runs"]] == [
        ("嗯", "s01"), ("真的", "s01"), ("順便說一下", "s02"), ("好累", "s02"),
    ]
    assert [r["kind"] for r in t["runs"]] == ["filler", "extra", "extra", "extra"]


def test_skipped_sentence_does_not_take_a_filler_in_its_gap():
    t = transcript_of("第一句話。第二句話。第三句話。", asr="第一句話嗯第三句話")
    assert [s["spoken"] for s in t["sentences"]] == [True, False, True]
    assert [(r["text"], r["kind"], r["sentence"]) for r in t["runs"]] == [("嗯", "filler", "s03")]
    assert [x["op"] for x in t["tokens"]][4] == "ins"


def test_skipped_sentence_does_not_take_a_partial_take_of_the_next():
    t = transcript_of(
        "第一個方法是先把停頓剪掉。第二個方法是先把贅字剪掉。第三個方法是先把重講剪掉。",
        asr="第一個方法是先把停頓剪掉第三個方法第三個方法是先把重講剪掉",
    )
    assert [s["spoken"] for s in t["sentences"]] == [True, False, True]
    assert [(r["h0"], r["h1"], r["text"], r["kind"], r["sentence"]) for r in t["runs"]] == [
        (12, 17, "第三個方法", "retake", "s03"),
    ]
    assert t["sentences"][2]["ratio"] == 1.0


def test_short_sentence_stays_bound():
    # 第三次 is bound to a 第一步 that is the start of the next sentence, but three units are fewer than a
    # take needs (RETAKE_MIN_UNITS): the sentence still counts as spoken, shown as recognised.
    t = transcript_of("第三次。第一步是把停頓剪掉。", asr="第一步第一步是把停頓剪掉")
    s1, s2 = t["sentences"]
    assert s1["spoken"] and s1["display_source"] == "asr" and s1["text"] == "第一步"
    assert s2["spoken"] and s2["ratio"] == 1.0 and t["runs"] == []


def test_filler_next_to_a_freed_take_is_its_own_run():
    # The second sentence was bound to 第三個方法, a partial take of the third, and gave it back. The 呃
    # said right before or after that take is a filler run of its own; the take alone is a retake. (In
    # the second script the take is bound to the end of the second sentence, which leaves no unit of it
    # to take the 呃 after the take.)
    script = "第一個方法是先把停頓剪掉。第二個方法是先把贅字剪掉。第三個方法是先把重講剪掉。"
    t = transcript_of(script, asr="第一個方法是先把停頓剪掉呃第三個方法第三個方法是先把重講剪掉")
    assert [(r["h0"], r["h1"], r["text"], r["kind"], r["sentence"]) for r in t["runs"]] == [
        (12, 13, "呃", "filler", "s03"), (13, 18, "第三個方法", "retake", "s03"),
    ]
    script = "第一個方法是先把停頓剪掉。接下來要介紹第二個方法。第三個方法是先把重講剪掉。"
    t = transcript_of(script, asr="第一個方法是先把停頓剪掉第三個方法呃第三個方法是先把重講剪掉")
    assert [(r["h0"], r["h1"], r["text"], r["kind"], r["sentence"]) for r in t["runs"]] == [
        (12, 17, "第三個方法", "retake", "s03"), (17, 18, "呃", "filler", "s03"),
    ]


@pytest.mark.parametrize("script,asr,take", [
    ("第一個方法是先把停頓剪掉。這個方法是先把贅字剪掉。那個方法是先把重講剪掉。",
     "第一個方法是先把停頓剪掉那個方法那個方法是先把重講剪掉", "那個方法"),  # the take starts with 那個
    ("第一個方法是先把停頓剪掉。第二個方法是先把贅字剪掉。第三個就是先把重講剪掉。",
     "第一個方法是先把停頓剪掉第三個就是第三個就是先把重講剪掉", "第三個就是"),  # the take ends with 就是
])
def test_filler_words_of_a_freed_take_stay_in_it(script, asr, take):
    # The second sentence gave back a partial take of the third; no filler is said next to it, so the take
    # stays one retake run, filler words of its own included.
    t = transcript_of(script, asr=asr)
    assert [(r["h0"], r["h1"], r["text"], r["kind"]) for r in t["runs"]] == [(12, 12 + len(take), take, "retake")]


@pytest.mark.parametrize("hesitation,shown", [("呃", "呃"), ("额", "額")])
def test_a_hesitation_bound_to_a_skipped_sentence_does_not_keep_it_spoken(hesitation, shown):
    # The second sentence takes 第三個呃方法, a partial take of the third, its 呃 as a substitution. Without
    # the hesitation the take is exactly the start of the third sentence: the second gives it back.
    script = "第一個方法是先把停頓剪掉。第二個步驟是要有耐心。第三個方法是先把重講剪掉。"
    t = transcript_of(script, asr=f"第一個方法是先把停頓剪掉第三個{hesitation}方法第三個方法是先把重講剪掉")
    assert [s["spoken"] for s in t["sentences"]] == [True, False, True]
    assert [(r["h0"], r["h1"], r["text"], r["kind"], r["sentence"]) for r in t["runs"]] == [
        (12, 18, f"第三個{shown}方法", "retake", "s03"),
    ]


def test_a_hesitation_does_not_make_a_partial_take_long_enough():
    # Without its 呃, 第三呃步 is three units, fewer than a take needs (RETAKE_MIN_UNITS): the second
    # sentence keeps it, shown as recognised.
    script = "第一個方法是先把停頓剪掉。第二個步驟是要有耐心。第三步是先把重講剪掉。"
    t = transcript_of(script, asr="第一個方法是先把停頓剪掉第三呃步第三步是先把重講剪掉")
    s2 = t["sentences"][1]
    assert s2["spoken"] and s2["display_source"] == "asr" and s2["text"] == "第三呃步"
    assert t["sentences"][2]["reasons"] == []


def test_low_ratio_sentence_unlike_the_next_stays_spoken_as_asr_text():
    t = transcript_of("這個方法可以省下時間。最後記得追蹤我。", asr="这招能帮你省很多事最后记得追踪我")
    s1, s2 = t["sentences"]
    assert s1["spoken"] and s1["display_source"] == "asr" and s1["text"] == "這招能幫你省很多事"
    assert s2["spoken"] and s2["ratio"] == 1.0


def test_sentences_record_their_script_text():
    t = transcript_of("這個方法可以省下時間。最後記得追蹤我。", asr="这招能帮你省很多事最后记得追踪我")
    assert [s["script_text"] for s in t["sentences"]] == ["這個方法可以省下時間。", "最後記得追蹤我。"]


@pytest.mark.parametrize("heard",["今天我们来聊剪辑谢谢", "今天我们来聊啊剪辑"])
def test_words_that_sound_like_fillers_are_not_fillers(heard):
    runs = transcript_of("今天我們來聊剪輯。", asr=heard)["runs"]
    assert len(runs) == 1 and runs[0]["kind"] != "filler"


def test_simplified_filler_spellings_are_fillers():
    runs = transcript_of("今天我們來聊剪輯。", asr="今天我们额来聊诶剪辑")["runs"]
    assert [(r["text"], r["kind"]) for r in runs] == [("額", "filler"), ("誒", "filler")]


def test_classify_run_by_text():
    assert classify_run(keys("謝謝"), [], [], [], [], run_text="謝謝")[0] == "extra"
    assert classify_run(keys("額那個"), [], [], [], [], run_text="額那個")[0] == "filler"


def test_en_is_a_filler():
    t = transcript_of("今天來聊剪輯。", asr="恩今天來聊剪輯")
    assert [(r["text"], r["kind"]) for r in t["runs"]] == [("恩", "filler")]


@pytest.mark.parametrize("script,asr,run", [
    ("我們今天來聊剪輯。", "我我我們今天來聊剪輯", "我我"),
    ("剪輯其實不難。", "剪剪剪輯其實不難", "剪剪"),
    ("我們今天來聊剪輯。", "我呃我們今天來聊剪輯", "我呃"),  # 猶豫聲不擋口吃
    ("我們今天來聊剪輯。", "我們我們我們今天來聊剪輯", "我們我們"),  # the first two units, said twice more
])
def test_a_stutter_is_a_repeat(script, asr, run):
    t = transcript_of(script, asr=asr)
    assert [(r["text"], r["kind"]) for r in t["runs"]] == [(run, "repeat")]


def test_a_hesitation_does_not_lower_the_similarity_of_a_retake():
    script = "第一個方法是先把停頓剪掉。第三個方法是先把重講剪掉。"
    t = transcript_of(script, asr="第一個方法是先把停頓剪掉第三個呃方法第三個方法是先把重講剪掉")
    assert [r["kind"] for r in t["runs"]] == ["retake"]


@pytest.mark.parametrize("said", ["不對重來", "不對重來呃", "我再講一次", "重新講"])
def test_a_run_that_ends_with_a_command_before_the_script_is_a_retake(said):
    script = "第一個方法是先把停頓剪掉。第二個方法是先把贅字剪掉。"
    t = transcript_of(script, asr=f"第一個方法是先把停頓剪掉第二個做法{said}第二個方法是先把贅字剪掉")
    assert [r["kind"] for r in t["runs"]] == ["retake"]


def test_wait_a_moment_is_not_a_command():
    t = transcript_of("第一句話。第二句話。", asr="第一句話等一下第二句話")
    assert [r["kind"] for r in t["runs"]] == ["extra"]


def test_a_command_after_the_last_sentence_is_not_a_retake():
    t = transcript_of("第一句話。", asr="第一句話不對重來")
    assert [r["kind"] for r in t["runs"]] != ["retake"]


@pytest.mark.parametrize("said,phrase", [
    ("不對重來", "重來"), ("不對重來呃", "重來"), ("我再講一次", "我再講一次"), ("重新講", "重新講"),
    ("不對重呃來", "重來"),  # a hesitation inside the command
])
def test_a_command_retake_records_its_command(said, phrase):
    script = "第一個方法是先把停頓剪掉。第二個方法是先把贅字剪掉。"
    t = transcript_of(script, asr=f"第一個方法是先把停頓剪掉第二個做法{said}第二個方法是先把贅字剪掉")
    assert [(r["kind"], r["command"]) for r in t["runs"]] == [("retake", phrase)]


def test_only_a_command_retake_records_a_command():
    # Restarted after the last sentence and dropped with 不對: no script follows, so it is a retake by its
    # likeness to the sentence start, and no command is recorded although its text ends with one.
    t = transcript_of("第一個方法是先把停頓剪掉。", asr="第一個方法是先把停頓剪掉第一個方法是先把不對")
    assert [(r["text"], r["kind"], r["command"]) for r in t["runs"]] == [("第一個方法是先把不對", "retake", None)]
    t = transcript_of("今天天氣很好。我們去爬山吧。", asr="嗯今天真的天氣很好順便說一下我們去爬山吧好累")
    assert [(r["kind"], r["command"]) for r in t["runs"]] == [
        ("filler", None), ("extra", None), ("extra", None), ("extra", None),
    ]


def test_taiwan_text_stays_with_its_own_token():
    # s2twp turns 博客 into 部落格 (one longer) and 方便面 into 泡麵 (one shorter): the whole text keeps its
    # length, but cutting it back by position would put 格 on the token 有. The units of those two phrases
    # are converted one at a time (面 alone stays 面).
    by_unit = ["我", "的", "博", "客", "有", "方", "便", "面"]
    t = transcript_of("我的部落格很好用。", asr="我的博客有方便面")  # shown as recognised
    s = t["sentences"][0]
    assert s["display_source"] == "asr" and [u["text"] for u in s["units"]] == by_unit
    assert s["text"] == "我的博客有方便面" and [x["text"] for x in t["tokens"]] == by_unit
    t = transcript_of("今天好。", asr="今天好我的博客有方便面")  # an extra run
    assert [(r["text"], r["kind"]) for r in t["runs"]] == [("我的博客有方便面", "extra")]
    assert [x["text"] for x in t["tokens"]][3:] == by_unit
    t = transcript_of("今天好。", asr="今天好我的软件")  # a phrase that keeps its length is still converted
    assert t["runs"][0]["text"] == "我的軟體" and [x["text"] for x in t["tokens"]][3:] == ["我", "的", "軟", "體"]


def test_length_changing_phrase_does_not_spoil_the_rest():
    # Only 博客 (-> 部落格, one longer) is converted unit by unit; the rest is converted as one text, so
    # 头发很干净 becomes 頭髮很乾淨 (发 and 干 on their own give 發 and 幹).
    t = transcript_of("我的部落格很好用。", asr="我的博客头发很干净")  # shown as recognised
    s = t["sentences"][0]
    assert s["display_source"] == "asr" and s["text"] == "我的博客頭髮很乾淨"
    assert [x["text"] for x in t["tokens"]] == list("我的博客頭髮很乾淨")


def test_stretch_conversion_is_linear(monkeypatch):
    # A run of 1500 units: 149 times ten units whose phrases keep their length (头发, 干净, 软件), then ten
    # with a phrase that grows (博客) and one that shrinks (方便面). Each piece of ten, converted on its own,
    # gives the text expected of it in the long run.
    plain, mixed = "头发很干净的软件很好", "博客头发很干净方便面"
    pieces = [transcript_of("今天天氣。", asr="今天天气" + text)["runs"][0]["text"] for text in (plain, mixed)]
    passed = []

    def counting(text: str) -> str:
        passed.append(len(text))
        return to_taiwan(text)

    monkeypatch.setattr(align_module, "to_taiwan", counting)
    t = transcript_of("今天天氣。", asr="今天天气" + plain * 149 + mixed)
    assert sum(passed) <= 20 * 1500
    (run,) = t["runs"]
    assert run["h1"] - run["h0"] == 1500 and run["text"] == pieces[0] * 149 + pieces[1]
    assert pieces == ["頭髮很乾淨的軟體很好", "博客頭髮很乾淨方便面"]


def test_stretch_conversion_is_linear_in_characters(monkeypatch):
    # A run of 600 Latin words, short and long: what is passed to OpenCC is at most 20 times the
    # characters of the words, and the words come out as they went in.
    words = ["reels", "storytelling", "edit", "notification", "shorts", "subscription"] * 100
    passed = []

    def counting(text: str) -> str:
        passed.append(len(text))
        return to_taiwan(text)

    monkeypatch.setattr(align_module, "to_taiwan", counting)
    t = transcript_of("今天天氣。", asr="今天天气 " + " ".join(words))
    assert sum(passed) <= 20 * len("".join(words))
    (run,) = t["runs"]
    assert run["h1"] - run["h0"] == len(words) and run["text"] == " ".join(words)


@pytest.mark.parametrize("script,asr,run_text", [
    ("我的部落格。", "我的博客的软件", "軟體"),  # the run is 软件
    ("我的部落格很好用。", "我的博客很干净软件", "體"),  # the run is the 件 of 软件; 软 is bound
])
def test_run_text_matches_the_caption(script, asr, run_text):
    t = transcript_of(script, asr=asr)
    s = t["sentences"][0]
    assert s["display_source"] == "asr" and len(s["units"]) == len(t["tokens"])  # the caption shows every unit
    (run,) = t["runs"]
    first, last = s["units"][run["h0"]], s["units"][run["h1"] - 1]
    assert run["text"] == s["text"][first["span"][0]:last["span"][1]] == run_text
    assert "".join(x["text"] for x in t["tokens"][run["h0"]:run["h1"]]) == run_text


def test_tokens_record_op_and_sentence():
    t = transcript_of("今天好。", asr="今天嗯好")
    assert [(x["text"], x["op"], x["s"]) for x in t["tokens"]] == [
        ("今", "match", "s01"), ("天", "match", "s01"), ("嗯", "ins", None), ("好", "match", "s01"),
    ]


def test_script_without_sentences_is_an_error():
    with pytest.raises(PipelineError, match="no sentence"):
        transcript_of("[B-roll: 只有註記]", asr="你好")


def test_fixture_transcript():
    t = fixture_transcript()      # helper：Meijia script.md + 錄好的 JSON
    assert all(s["spoken"] for s in t["sentences"]) and t["sentences"][2]["ratio"] == 1.0
    assert {"retake", "repeat"} <= {r["kind"] for r in t["runs"]}


def test_fixture_designed_events():
    t = fixture_transcript()
    assert [(r["kind"], r["h0"], r["h1"], r["text"], r["similarity"], r["sentence"]) for r in t["runs"]] == [
        ("retake", 11, 18, "第一步是把停頓", 1.0, "s02"),  # half a take, then the whole sentence
        ("filler", 32, 33, "嗯", 0.0, "s03"),  # filler before s03
        ("repeat", 46, 48, "最後", 1.0, "s04"),  # 最後最後
    ]
    s = {x["id"]: x for x in t["sentences"]}
    assert (s["s01"]["end"], s["s02"]["start"]) == (2.56, 5.76)  # the pause, then the take that binds
    number = s["s03"]["units"][8]
    assert (number["text"], number["start"], number["end"]) == ("150", 12.40, 12.88)  # read as 一百五
    assert all(x["ratio"] == 1.0 and x["reasons"] == [] and not x["needs_listen"] for x in t["sentences"])
    assert t["markers"] == [{"before": "s03", "text": "[B-roll: 剪輯軟體畫面]"}]
    assert t["version"] == 2 and t["audio_s"] == pytest.approx(17.0, abs=1e-4)


def test_fixture_unit_starts_never_go_back():
    for s in fixture_transcript()["sentences"]:
        starts = [u["start"] for u in s["units"]]
        assert starts == sorted(starts) and all(u["end"] >= u["start"] for u in s["units"])


# ---- transcribe command ---------------------------------------------------------------------


def test_transcribe_writes_transcript(transcribed_project):
    p = transcribed_project
    t = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))
    runtime = f"mlx-qwen3-asr {importlib.metadata.version('mlx-qwen3-asr')}"
    assert t["asr"] == {"model": MODELS["1.7b"], "fallback_used": False, "runtime": runtime}
    assert [s["id"] for s in t["sentences"]] == ["s01", "s02", "s03", "s04"]
    assert t["audio_s"] == pytest.approx(17.0, abs=0.01)  # the length prep gave audio16k.wav
    assert (p.work / "asr-model.txt").read_text(encoding="utf-8").strip() == "1.7b"
    inputs = load_state(p)["steps"]["transcribe"]["inputs"]
    assert inputs["asr_json"]["path"] == str(RECORDED.resolve())
    assert set(inputs) == {"audio", "script", "asr_model", "asr_json"}


def test_transcribe_reports_counts_and_skips_when_nothing_changed(transcribed_project, capsys):
    capsys.readouterr()
    assert main(["transcribe", transcribed_project.name, "--asr-json", str(RECORDED)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True, "sentences": 4, "spoken": 4, "needs_listen": 0,
        "model": MODELS["1.7b"], "fallback_used": False, "skipped": True,
    }


def test_transcribe_runs_again_when_the_asr_source_changes(transcribed_project, tmp_path, capsys):
    p = transcribed_project
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    recorded["segments"] = [seg for seg in recorded["segments"] if seg["text"] != "嗯"]
    other = tmp_path / "no-filler.json"
    other.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert main(["transcribe", p.name, "--asr-json", str(other)]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is False
    t = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))
    assert "filler" not in {r["kind"] for r in t["runs"]}

    assert main(["transcribe", p.name, "--asr-model", "0.6b", "--asr-json", str(other)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is False and out["model"] == MODELS["0.6b"]


def test_transcribe_runs_the_engine_and_records_the_fallback(transcribed_project, monkeypatch, capsys):
    p = transcribed_project
    calls = []

    def fake_transcribe_audio(audio16k, cache_dir, model_key):
        calls.append((audio16k, cache_dir, model_key))
        return RECORDED, MODELS["0.6b"], True

    monkeypatch.setattr(align_module, "transcribe_audio", fake_transcribe_audio)
    capsys.readouterr()
    assert main(["transcribe", p.name]) == 0  # no --asr-json: the inputs changed, so it runs
    assert calls == [(p.work / "audio16k.wav", p.work / "asr", "1.7b")]
    out = json.loads(capsys.readouterr().out)
    assert (out["model"], out["fallback_used"], out["skipped"]) == (MODELS["0.6b"], True, False)
    t = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))
    assert (t["asr"]["model"], t["asr"]["fallback_used"]) == (MODELS["0.6b"], True)
    assert set(load_state(p)["steps"]["transcribe"]["inputs"]) == {"audio", "script", "asr_model"}


def test_transcribe_before_prep_is_an_error(tmp_path, capsys):
    p = project_with(make_clip(tmp_path, seconds=1))
    assert main(["transcribe", p.name, "--asr-json", str(RECORDED)]) == 1
    assert "audio16k.wav" in json.loads(capsys.readouterr().out)["error"]

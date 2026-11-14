"""Tests for pipeline.review_actions: the changes the review page sends.

The documents are made up (helpers.transcript_and_trims: a fake recognition, 0.2 s per unit from 0 s). The script has
three sentences; the speaker said extra words before sentence 2 (t01, listed: rejected by default) and before
sentence 3 (t03), and a filler inside sentence 2 (t02, accepted by default). Sentence 2 carries the labels hook and
sub, so that a restore shows whether they come back. Each test works on a project of its own in tmp_path."""

import copy
import json
import os
import stat
from pathlib import Path

import pytest
from helpers import default_edit, edited_project_from_documents, transcript_and_trims

from pipeline import review_actions
from pipeline.edit import load_edit, validate_project
from pipeline.errors import PipelineError
from pipeline.project import edit_stage, file_sha256
from pipeline.review_actions import (
    CAPTION_EMPTY_TEXT,
    CONFLICT_TEXT,
    DEFAULT_SEM,
    DEFAULT_WEIGHT,
    GLYPH_TEXT,
    INVISIBLE_TEXT,
    NOTHING_LEFT_TEXT,
    REFUSED_TEXT,
    TOO_SHORT_TEXT,
    UNDO_FILE,
    ChangeRefused,
    VersionConflict,
    apply_change,
    remove_sentence,
    restore_sentence,
    set_caption,
    set_cut,
    write_edit,
)
from pipeline.review_data import NOT_READY_TEXT, NotReady, edit_version
from pipeline.review_text import REMOVED_REASON

SCRIPT = "第一句話。今天我們來聊剪輯。第三句話。"
ASR = "第一句話順便說一下今天我們來聊嗯剪輯另外補充一下第三句話"
REWORDED_ASR = "第一句話今天咱們來講今天我們來聊剪輯第三句話"  # t01: a reworded take of sentence 2, listed
S2_ENTRY = {"id": "s02", "sem": "hook", "weight": "sub"}


def project_for(tmp_path, mutate=None, asr=ASR):
    """(project, transcript, trims): a project whose edit.json passed validate. `mutate` changes the default edit
    (sentence 2 labelled hook and sub) before it is written."""
    transcript, trims = transcript_and_trims(SCRIPT, asr)
    edit = default_edit(transcript, trims)
    edit["sentences"][1].update(sem="hook", weight="sub")
    if mutate is not None:
        mutate(edit)
    p = edited_project_from_documents(tmp_path, transcript, trims, edit)
    validate_project(p)
    return p, transcript, trims


def removed_by_claude(*ranges):
    """A mutate for project_for: every (from, to) is a cuts.extra range with Claude's reason, and the sentences it
    holds are not listed any more."""

    def mutate(edit):
        ids = ["s01", "s02", "s03"]
        for first, last in ranges:
            edit["cuts"]["extra"].append({"from": first, "to": last, "reason": "離題"})
            held = ids[ids.index(first):ids.index(last) + 1]
            edit["sentences"] = [item for item in edit["sentences"] if item["id"] not in held]

    return mutate


def change_cut(trim, cut):
    return {"type": "cut", "trim": trim, "cut": cut}


def change_remove(sentence):
    return {"type": "remove", "sentence": sentence}


def change_restore(sentence):
    return {"type": "restore", "sentence": sentence}


def change_caption(sentence, text):
    return {"type": "caption", "sentence": sentence, "text": text}


def undo_file(p):
    return json.loads((p.work / UNDO_FILE).read_text(encoding="utf-8"))


def log_text(p):
    path = p.work / "review.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def sentence_ids(edit):
    return [item["id"] for item in edit["sentences"]]


def assert_nothing_written(p, before):
    """edit.json holds the bytes it held, and the files a change may write were not made."""
    assert p.edit_path.read_bytes() == before
    assert not (p.work / UNDO_FILE).exists()
    assert not list(p.work.glob("*.tmp"))


# ---- cuts -------------------------------------------------------------------------------------


@pytest.mark.parametrize("trim, cut", [("t01", True), ("t02", False)])
def test_toggling_a_cut_writes_edit_json_and_records_validate(tmp_path, trim, cut):
    p, _, _ = project_for(tmp_path)
    before = load_edit(p.edit_path)
    version = edit_version(p)
    out = apply_change(p, version, change_cut(trim, cut))
    after = load_edit(p.edit_path)
    assert (trim in after["cuts"]["accept"], trim in after["cuts"]["reject"]) == (cut, not cut)
    assert sorted(after["cuts"]["accept"] + after["cuts"]["reject"]) == ["t01", "t02", "t03"]
    assert {**after, "cuts": None} == {**before, "cuts": None}
    assert out == {"version": edit_version(p)} and out["version"] != version
    assert edit_stage(p) is None  # validate_project recorded the new edit.json


def test_a_cut_that_is_already_so_changes_no_decision(tmp_path):
    p, _, _ = project_for(tmp_path)
    before = load_edit(p.edit_path)
    apply_change(p, edit_version(p), change_cut("t02", True))
    assert load_edit(p.edit_path) == before


def test_a_stale_version_changes_nothing(tmp_path):
    p, _, _ = project_for(tmp_path)
    seen = edit_version(p)
    apply_change(p, seen, change_cut("t01", True))  # the page's change; Claude's follows
    before = p.edit_path.read_bytes()
    with pytest.raises(VersionConflict) as refused:
        apply_change(p, seen, change_cut("t03", True))
    assert refused.value.message == CONFLICT_TEXT == "決定剛被更新過，請再看一次。"
    assert isinstance(refused.value, PipelineError)
    assert p.edit_path.read_bytes() == before
    assert not (p.work / "review.log").exists()


def rewrite(path: Path) -> None:
    """Write the JSON document at `path` again with the same content in other bytes (as a step run again may)."""
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("name", ["transcript.json", "trim.json"])
def test_a_change_for_the_files_before_a_new_transcript_or_trim_is_a_conflict(tmp_path, name):
    p, _, _ = project_for(tmp_path)
    seen = edit_version(p)
    rewrite(p.work / name)  # transcribe or trim ran again; Claude validated the same edit.json on it
    validate_project(p)
    before = p.edit_path.read_bytes()
    with pytest.raises(VersionConflict):
        apply_change(p, seen, change_cut("t01", True))
    assert_nothing_written(p, before)


def test_a_change_is_not_applied_on_files_that_changed_while_they_were_read(tmp_path, monkeypatch):
    p, _, _ = project_for(tmp_path)
    seen = edit_version(p)
    read = review_actions.require_valid

    def read_while_transcribe_runs_again(project):
        documents = read(project)
        rewrite(project.work / "transcript.json")
        return documents

    monkeypatch.setattr(review_actions, "require_valid", read_while_transcribe_runs_again)
    before = p.edit_path.read_bytes()
    with pytest.raises(VersionConflict):
        apply_change(p, seen, change_cut("t01", True))
    assert_nothing_written(p, before)


def test_changes_are_refused_while_edit_json_is_not_validated(tmp_path):
    p, _, _ = project_for(tmp_path)
    edit = load_edit(p.edit_path)
    edit["captions"]["mode"] = "word"  # Claude wrote it; validate has not seen it
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    before = p.edit_path.read_bytes()
    assert edit_stage(p) is not None
    with pytest.raises(NotReady) as refused:
        apply_change(p, "stale", change_cut("t01", True))  # not ready comes before the version
    assert refused.value.message == NOT_READY_TEXT
    assert_nothing_written(p, before)


def test_changes_are_refused_while_there_is_no_edit_json(tmp_path):
    p, _, _ = project_for(tmp_path)
    p.edit_path.unlink()
    with pytest.raises(NotReady):
        apply_change(p, "stale", change_cut("t01", True))
    assert not p.edit_path.exists()


def test_removing_the_last_kept_sentence_is_refused_in_chinese(tmp_path):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s01", "s01"), ("s03", "s03")))
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_remove("s02"))
    assert refused.value.message == NOTHING_LEFT_TEXT == "至少要留下一句，這個改動沒有存。"
    assert isinstance(refused.value, PipelineError)
    assert_nothing_written(p, before)
    assert "cuts.extra: nothing is left, every spoken sentence is removed" in log_text(p)
    assert edit_stage(p) is None  # the edit.json that was there is still the validated one


def test_a_change_that_leaves_only_pieces_too_short_is_refused_with_its_own_text(tmp_path):
    """A listed candidate t04 covers the whole reel but its first frame: cutting it keeps every sentence and leaves
    no piece of 2 frames or more, so the text does not say that no sentence would be left."""
    transcript, trims = transcript_and_trims(SCRIPT, ASR)
    bounds = trims["bounds"]
    start, end = bounds["s01"]["cut_in"] + 1 / 30, bounds["s03"]["cut_out"]
    trims["trims"].append({**trims["trims"][0], "id": "t04", "start": start, "end": end, "suggest": "list"})
    p = edited_project_from_documents(tmp_path, transcript, trims)  # t04 is listed, so kept by default
    validate_project(p)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_cut("t04", True))
    assert refused.value.message == TOO_SHORT_TEXT == "這樣剪完，留下的每一段都太短，沒辦法輸出，這個改動沒有存。"
    assert_nothing_written(p, before)
    assert "no piece of 2 frames or more remains" in log_text(p)
    assert edit_stage(p) is None


def test_any_other_invalid_change_is_refused_in_chinese(tmp_path):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_cut("t99", True))  # there is no such candidate
    assert refused.value.message == REFUSED_TEXT == "這個改動會讓剪接設定出錯，沒有存。請回對話請 Claude 看看。"
    assert_nothing_written(p, before)
    assert "unknown trim 't99'" in log_text(p)


def test_every_refusal_adds_lines_to_the_log_without_dropping_the_earlier_ones(tmp_path):
    p, _, _ = project_for(tmp_path)
    for _ in range(2):
        with pytest.raises(ChangeRefused):
            apply_change(p, edit_version(p), change_cut("t99", True))
    assert log_text(p).count("unknown trim 't99'") == 2
    assert all(line.strip() for line in log_text(p).splitlines())


@pytest.mark.parametrize(
    "change",
    [
        {"type": "caption", "sentence": "s01"},  # no text: a change of the caption says the text or null
        {"type": "caption", "sentence": "s01", "text": 5},
        {"type": "caption", "sentence": "s01", "text": ["好"]},
        {"type": "caption", "text": "好"},
        {"type": "caption", "sentence": None, "text": "好"},
        {"type": "rename", "sentence": "s01"},  # a type the page does not send
        {"type": "cut", "trim": "t01"},
        {"type": "cut", "cut": True},
        {"type": "cut", "trim": "t01", "cut": 1},
        {"type": "cut", "trim": "t01", "cut": "yes"},
        {"type": "cut", "trim": 1, "cut": True},
        {"type": "remove"},
        {"type": "remove", "sentence": None},
        {"type": "restore", "sentence": 3},
        {"sentence": "s01"},
        "remove",
        ["remove", "s01"],
        None,
    ],
)
def test_a_change_the_page_does_not_send_is_refused_and_logged(tmp_path, change):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change)
    assert refused.value.message == REFUSED_TEXT
    assert_nothing_written(p, before)
    assert log_text(p).strip() != ""


@pytest.mark.parametrize("change", [
    change_remove,
    change_restore,
    lambda text: {"type": text, "cut": True},
    lambda text: change_caption(text, "好"),
    lambda text: change_caption("s02", text + "\U0001F600"),  # refused for the font, which says the characters
])
def test_text_the_page_sent_stays_on_one_line_of_the_log(tmp_path, change):
    p, _, _ = project_for(tmp_path)
    with pytest.raises(ChangeRefused):
        apply_change(p, edit_version(p), change("s99\n2099-01-01T00:00:00+00:00 forged line"))
    assert len(log_text(p).splitlines()) == 1


# ---- removing a sentence ----------------------------------------------------------------------


def test_removing_a_sentence_also_cuts_the_extra_speech_before_it(tmp_path):
    p, _, _ = project_for(tmp_path)
    out = apply_change(p, edit_version(p), change_remove("s02"))
    edit = load_edit(p.edit_path)
    assert edit["cuts"]["extra"] == [{"from": "s02", "to": "s02", "reason": REMOVED_REASON}]
    assert sentence_ids(edit) == ["s01", "s03"]
    assert "t01" in edit["cuts"]["accept"] and "t01" not in edit["cuts"]["reject"]
    assert "t03" in edit["cuts"]["reject"]  # the extra speech before sentence 3 is sentence 3's
    assert out == {"version": edit_version(p)} and edit_stage(p) is None
    assert undo_file(p) == {
        "transcript": file_sha256(p.work / "transcript.json"),
        "trim": file_sha256(p.work / "trim.json"),
        "removed": {"s02": {"sentences": [S2_ENTRY], "accepted": ["t01"]}},
    }
    assert not list(p.work.glob("*.tmp"))


def test_removing_a_sentence_cuts_a_reworded_take_before_it(tmp_path):
    p, _, _ = project_for(tmp_path, asr=REWORDED_ASR)
    apply_change(p, edit_version(p), change_remove("s02"))
    edit = load_edit(p.edit_path)
    assert edit["cuts"]["accept"] == ["t01"] and edit["cuts"]["reject"] == []
    assert undo_file(p)["removed"]["s02"]["accepted"] == ["t01"]


def test_a_kept_filler_before_the_sentence_is_not_cut_with_it(tmp_path):
    def mutate(edit):  # the filler before sentence 2 is kept
        edit["cuts"]["accept"].remove("t01")
        edit["cuts"]["reject"].append("t01")

    p, _, _ = project_for(tmp_path, mutate, asr="第一句話嗯今天我們來聊剪輯第三句話")
    apply_change(p, edit_version(p), change_remove("s02"))  # the filler t01 is s02's and still in the reel
    cuts = load_edit(p.edit_path)["cuts"]
    assert cuts["accept"] == [] and cuts["reject"] == ["t01"]  # only extra speech and retakes go with the sentence
    assert undo_file(p)["removed"]["s02"]["accepted"] == []


def test_the_extra_speech_before_a_sentence_outside_the_reel_stays_as_it_was(tmp_path):
    p, _, _ = project_for(tmp_path)
    apply_change(p, edit_version(p), change_remove("s03"))  # the reel now ends at sentence 2: t03 lies after it
    cuts = load_edit(p.edit_path)["cuts"]
    assert "t03" in cuts["reject"] and "t01" in cuts["reject"]
    assert undo_file(p)["removed"]["s03"]["accepted"] == []


def test_two_removals_are_both_kept_in_the_record(tmp_path):
    p, _, _ = project_for(tmp_path)
    apply_change(p, edit_version(p), change_remove("s03"))
    apply_change(p, edit_version(p), change_remove("s02"))
    assert sorted(undo_file(p)["removed"]) == ["s02", "s03"]


def test_the_record_drops_a_sentence_claude_put_back(tmp_path):
    p, _, _ = project_for(tmp_path)
    apply_change(p, edit_version(p), change_remove("s02"))
    edit = load_edit(p.edit_path)  # Claude keeps sentence 2 again by editing edit.json, and validates it
    edit["cuts"]["extra"] = []
    edit["sentences"].insert(1, {"id": "s02", "sem": "claim", "weight": "main"})
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    validate_project(p)
    apply_change(p, edit_version(p), change_remove("s03"))
    assert undo_file(p)["removed"] == {"s03": {"sentences": [{"id": "s03", "sem": "claim", "weight": "main"}],
                                               "accepted": []}}


def test_the_record_keeps_a_sentence_still_inside_a_wider_range_claude_wrote(tmp_path):
    transcript, trims = transcript_and_trims("第一句話。今天我們來聊剪輯。第三句話。第四句話。",
                                             "第一句話今天我們來聊剪輯第三句話第四句話")
    edit = default_edit(transcript, trims)
    edit["sentences"][1].update(sem="hook", weight="sub")
    p = edited_project_from_documents(tmp_path, transcript, trims, edit)
    validate_project(p)
    apply_change(p, edit_version(p), change_remove("s02"))
    saved = undo_file(p)["removed"]["s02"]
    edit = load_edit(p.edit_path)  # Claude puts sentences 1 and 2 in one range of its own, and validates it
    edit["cuts"]["extra"] = [{"from": "s01", "to": "s02", "reason": "離題"}]
    edit["sentences"] = [item for item in edit["sentences"] if item["id"] != "s01"]
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    validate_project(p)
    apply_change(p, edit_version(p), change_remove("s04"))  # the next write of the record
    removed = undo_file(p)["removed"]
    assert sorted(removed) == ["s02", "s04"] and removed["s02"] == saved
    apply_change(p, edit_version(p), change_restore("s02"))  # the range goes; sentence 2 gets its saved labels
    assert {"id": "s02", "sem": "hook", "weight": "sub"} in load_edit(p.edit_path)["sentences"]


@pytest.mark.parametrize("sid", ["s01", "s99"])
def test_removing_a_sentence_that_is_not_kept_is_refused(tmp_path, sid):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s01", "s01")))  # s01 is already gone; s99 is no sentence
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_remove(sid))
    assert refused.value.message == REFUSED_TEXT
    assert_nothing_written(p, before)
    assert log_text(p).strip() != ""


def test_removing_a_sentence_the_speaker_skipped_is_refused(tmp_path):
    transcript, trims = transcript_and_trims(SCRIPT, "第一句話今天我們來聊剪輯")  # sentence 3 was not spoken
    p = edited_project_from_documents(tmp_path, transcript, trims)
    validate_project(p)
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_remove("s03"))
    assert refused.value.message == REFUSED_TEXT


# ---- restoring a sentence ---------------------------------------------------------------------


def test_restore_puts_back_the_labels_and_the_decisions(tmp_path):
    p, _, _ = project_for(tmp_path)
    before = load_edit(p.edit_path)
    apply_change(p, edit_version(p), change_remove("s02"))
    out = apply_change(p, edit_version(p), change_restore("s02"))
    after = load_edit(p.edit_path)
    assert after["sentences"] == before["sentences"] and S2_ENTRY in after["sentences"]
    assert after["cuts"]["extra"] == []
    assert sorted(after["cuts"]["accept"]) == ["t02"] and sorted(after["cuts"]["reject"]) == ["t01", "t03"]
    assert out == {"version": edit_version(p)} and edit_stage(p) is None
    assert undo_file(p)["removed"] == {}  # what was kept for it is used up


def test_restore_keeps_a_candidate_that_was_cut_before_the_removal(tmp_path):
    def mutate(edit):  # t01 is cut already when the page removes sentence 2
        edit["cuts"]["reject"].remove("t01")
        edit["cuts"]["accept"].append("t01")

    p, _, _ = project_for(tmp_path, mutate)
    apply_change(p, edit_version(p), change_remove("s02"))
    assert undo_file(p)["removed"]["s02"]["accepted"] == []
    apply_change(p, edit_version(p), change_restore("s02"))
    assert "t01" in load_edit(p.edit_path)["cuts"]["accept"]


def test_restore_without_a_record_uses_claim_and_main(tmp_path):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s02", "s02")))  # a cuts.extra Claude wrote
    assert not (p.work / UNDO_FILE).exists()
    apply_change(p, edit_version(p), change_restore("s02"))
    edit = load_edit(p.edit_path)
    assert (DEFAULT_SEM, DEFAULT_WEIGHT) == ("claim", "main")
    assert {"id": "s02", "sem": "claim", "weight": "main"} in edit["sentences"]
    assert sentence_ids(edit) == ["s01", "s02", "s03"] and edit["cuts"]["extra"] == []
    assert sorted(edit["cuts"]["accept"]) == ["t02"] and sorted(edit["cuts"]["reject"]) == ["t01", "t03"]


def test_restore_brings_back_the_whole_range(tmp_path):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s01", "s02")))  # from s01 to s02 in one item
    apply_change(p, edit_version(p), change_restore("s02"))
    edit = load_edit(p.edit_path)
    assert edit["cuts"]["extra"] == []
    assert edit["sentences"] == [
        {"id": "s01", "sem": "claim", "weight": "main"},
        {"id": "s02", "sem": "claim", "weight": "main"},
        {"id": "s03", "sem": "claim", "weight": "main"},
    ]


def test_restore_leaves_the_other_ranges_alone(tmp_path):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s01", "s01"), ("s03", "s03")))
    apply_change(p, edit_version(p), change_restore("s03"))
    edit = load_edit(p.edit_path)
    assert edit["cuts"]["extra"] == [{"from": "s01", "to": "s01", "reason": "離題"}]
    assert sentence_ids(edit) == ["s02", "s03"]


def test_restore_keeps_the_sentences_in_script_order(tmp_path):
    p, _, _ = project_for(tmp_path)
    for sid in ("s01", "s03"):
        apply_change(p, edit_version(p), change_remove(sid))
    for sid in ("s03", "s01"):  # the other way round
        apply_change(p, edit_version(p), change_restore(sid))
    assert sentence_ids(load_edit(p.edit_path)) == ["s01", "s02", "s03"]


@pytest.mark.parametrize("name", ["transcript.json", "trim.json"])
def test_an_undo_record_of_an_older_transcript_is_not_used(tmp_path, name):
    p, _, _ = project_for(tmp_path)
    apply_change(p, edit_version(p), change_remove("s02"))
    path = p.work / name  # the same document in other bytes: its ids may now mean other things
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=4) + "\n",
                    encoding="utf-8")
    validate_project(p)
    apply_change(p, edit_version(p), change_restore("s02"))
    edit = load_edit(p.edit_path)
    assert {"id": "s02", "sem": "claim", "weight": "main"} in edit["sentences"]  # not hook and sub
    assert "t01" in edit["cuts"]["accept"]  # and not put back
    assert undo_file(p)["transcript"] == file_sha256(p.work / "transcript.json")


@pytest.mark.parametrize(
    "garbage",
    [
        "not json",
        "[]",
        '{"transcript": 1}',
        '{"transcript": "x", "trim": "y", "removed": 5}',
    ],
)
def test_an_undo_file_that_cannot_be_used_is_treated_as_empty(tmp_path, garbage):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s02", "s02")))
    (p.work / UNDO_FILE).write_text(garbage, encoding="utf-8")
    apply_change(p, edit_version(p), change_restore("s02"))
    assert {"id": "s02", "sem": "claim", "weight": "main"} in load_edit(p.edit_path)["sentences"]
    assert undo_file(p)["removed"] == {}


def test_an_entry_of_the_undo_file_in_the_wrong_shape_is_not_used(tmp_path):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s02", "s02")))
    document = {
        "transcript": file_sha256(p.work / "transcript.json"),
        "trim": file_sha256(p.work / "trim.json"),
        "removed": {"s02": {"sentences": "hook", "accepted": ["t01"]}},
    }
    (p.work / UNDO_FILE).write_text(json.dumps(document), encoding="utf-8")
    apply_change(p, edit_version(p), change_restore("s02"))
    assert {"id": "s02", "sem": "claim", "weight": "main"} in load_edit(p.edit_path)["sentences"]


def test_a_saved_entry_that_no_longer_validates_is_refused_and_changes_nothing(tmp_path):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s02", "s02")))
    document = {
        "transcript": file_sha256(p.work / "transcript.json"),
        "trim": file_sha256(p.work / "trim.json"),
        "removed": {"s02": {"sentences": [{"id": "s02", "sem": "bogus", "weight": "main"}], "accepted": []}},
    }
    (p.work / UNDO_FILE).write_text(json.dumps(document), encoding="utf-8")
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_restore("s02"))
    assert refused.value.message == REFUSED_TEXT
    assert p.edit_path.read_bytes() == before
    assert "sentences[1].sem" in log_text(p)


@pytest.mark.parametrize("sid", ["s01", "s99"])
def test_restoring_a_sentence_that_is_not_removed_is_refused(tmp_path, sid):
    p, _, _ = project_for(tmp_path)  # s01 is kept; s99 is no sentence
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_restore(sid))
    assert refused.value.message == REFUSED_TEXT
    assert_nothing_written(p, before)
    assert log_text(p).strip() != ""


def test_restoring_a_sentence_the_speaker_skipped_is_refused(tmp_path):
    script = "第一句話。這句沒講。第三句話。第四句話。"
    transcript, trims = transcript_and_trims(script, "第一句話第三句話第四句話")
    edit = default_edit(transcript, trims)
    edit["cuts"]["extra"] = [{"from": "s01", "to": "s03", "reason": "離題"}]  # the range holds the skipped sentence
    edit["sentences"] = [item for item in edit["sentences"] if item["id"] == "s04"]
    p = edited_project_from_documents(tmp_path, transcript, trims, edit)
    validate_project(p)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_restore("s02"))
    assert refused.value.message == REFUSED_TEXT
    assert_nothing_written(p, before)
    apply_change(p, edit_version(p), change_restore("s03"))  # a spoken sentence of the range brings the range back
    assert load_edit(p.edit_path)["cuts"]["extra"] == []


# ---- the caption text of a sentence -----------------------------------------------------------


def test_a_caption_change_writes_captions_text(tmp_path):
    p, _, _ = project_for(tmp_path)
    before = load_edit(p.edit_path)
    version = edit_version(p)
    out = apply_change(p, version, change_caption("s02", "今天我們來聊剪接"))
    after = load_edit(p.edit_path)
    assert after["captions"] == {"mode": "line", "text": {"s02": "今天我們來聊剪接"}}
    assert {**after, "captions": None} == {**before, "captions": None}
    assert out == {"version": edit_version(p)} and out["version"] != version
    assert edit_stage(p) is None  # validate_project recorded the new edit.json
    assert not (p.work / UNDO_FILE).exists()
    apply_change(p, edit_version(p), change_caption("s01", "第一句話吧"))  # a second text joins the first
    apply_change(p, edit_version(p), change_caption("s02", "今天來聊剪接"))  # and a text replaces the one before
    assert load_edit(p.edit_path)["captions"]["text"] == {"s02": "今天來聊剪接", "s01": "第一句話吧"}


def test_none_restores_the_original_caption(tmp_path):
    p, _, _ = project_for(tmp_path, lambda edit: edit["captions"].update(text={"s01": "第一句話吧", "s02": "今天來聊"}))
    apply_change(p, edit_version(p), change_caption("s02", None))
    assert load_edit(p.edit_path)["captions"] == {"mode": "line", "text": {"s01": "第一句話吧"}}
    apply_change(p, edit_version(p), change_caption("s01", None))
    assert load_edit(p.edit_path)["captions"] == {"mode": "line"}  # no empty text object is left behind
    apply_change(p, edit_version(p), change_caption("s01", None))  # a sentence with its own caption: nothing to do
    assert load_edit(p.edit_path)["captions"] == {"mode": "line"}
    assert edit_stage(p) is None


def test_a_character_the_font_lacks_is_refused_in_chinese(tmp_path):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_caption("s02", "今天我們來聊剪輯\U0001F600"))
    assert refused.value.message == GLYPH_TEXT.format(chars="\U0001F600") == "字型沒有「\U0001F600」這幾個字，沒有存。"
    assert_nothing_written(p, before)
    assert "U+1F600" in log_text(p) and len(log_text(p).splitlines()) == 1


def test_the_characters_the_font_lacks_are_named_once_in_text_order(tmp_path):
    p, _, _ = project_for(tmp_path)
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_caption("s02", "\U0001F603今天\U0001F600我們\U0001F603"))
    assert refused.value.message == "字型沒有「\U0001F603\U0001F600」這幾個字，沒有存。"


def test_the_font_is_checked_before_validate(tmp_path):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:  # validate would refuse the line break too
        apply_change(p, edit_version(p), change_caption("s02", "今天\u2028\U0001F600"))
    assert refused.value.message == GLYPH_TEXT.format(chars="\U0001F600")
    assert_nothing_written(p, before)


@pytest.mark.parametrize("invisible", [
    "\u200b",  # zero-width space
    "\ufeff",  # zero-width no-break space
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\ufe0f",  # variation selector 16
    "\U000E0100",  # variation selector 17
])
def test_a_character_the_font_lacks_and_nobody_sees_is_refused_as_invisible(tmp_path, invisible):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_caption("s02", f"今天我們{invisible}來聊剪接"))
    assert refused.value.message == INVISIBLE_TEXT == (
        "字幕裡有看不見的字元，多半是貼上時帶進來的，沒有存。把貼上的那一段刪掉重打。")
    assert_nothing_written(p, before)
    assert f"U+{ord(invisible):04X}" in log_text(p) and len(log_text(p).splitlines()) == 1


@pytest.mark.parametrize("text, visible", [
    ("今天\u200b我們\U0001F600來聊", "\U0001F600"),
    ("今天我們來聊❤\ufe0f\u200b", "❤"),  # the heart and its variation selector
])
def test_with_characters_of_both_kinds_missing_only_the_visible_ones_are_named(tmp_path, text, visible):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_caption("s02", text))
    assert refused.value.message == GLYPH_TEXT.format(chars=visible)
    assert_nothing_written(p, before)
    assert "U+200B" in log_text(p) and f"U+{ord(visible):04X}" in log_text(p)  # the log names all of them


def test_only_the_characters_the_captions_show_are_checked_against_the_font(tmp_path):
    p, _, _ = project_for(tmp_path)
    apply_change(p, edit_version(p), change_caption("s02", "今天我們來聊剪接\t"))  # the font has no tab; none shows
    assert load_edit(p.edit_path)["captions"]["text"] == {"s02": "今天我們來聊剪接\t"}


def test_a_caption_that_replaces_a_quoted_word_is_saved(tmp_path):
    """「好」。 becomes 棒, whose place falls on the 「 (no unit's): 棒 takes the unit of 好, so it has a character
    to show and is saved."""
    transcript, trims = transcript_and_trims("「好」。今天我們來聊剪輯。", "好今天我們來聊剪輯")
    p = edited_project_from_documents(tmp_path, transcript, trims)
    validate_project(p)
    apply_change(p, edit_version(p), change_caption("s01", "棒"))
    assert load_edit(p.edit_path)["captions"]["text"] == {"s01": "棒"}
    assert edit_stage(p) is None


@pytest.mark.parametrize("text", ["", "   ", "　", "，。", "**，**"])
def test_a_caption_text_with_nothing_to_show_is_refused_in_chinese(tmp_path, text):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_caption("s02", text))
    assert refused.value.message == CAPTION_EMPTY_TEXT == "字幕至少要有一個字，沒有存。"
    assert_nothing_written(p, before)
    assert "captions.text.s02" in log_text(p)


@pytest.mark.parametrize("sid, text", [
    ("s99", "好"),  # no such sentence
    ("s02", "今天\u2028我們"),  # two lines
])
def test_any_other_invalid_caption_change_is_refused_with_the_general_text(tmp_path, sid, text):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_caption(sid, text))
    assert refused.value.message == REFUSED_TEXT
    assert_nothing_written(p, before)
    assert "captions.text" in log_text(p)


def test_a_caption_change_of_a_sentence_the_speaker_skipped_is_refused(tmp_path):
    transcript, trims = transcript_and_trims(SCRIPT, "第一句話今天我們來聊剪輯")  # sentence 3 was not spoken
    p = edited_project_from_documents(tmp_path, transcript, trims)
    validate_project(p)
    with pytest.raises(ChangeRefused) as refused:
        apply_change(p, edit_version(p), change_caption("s03", "第三句"))
    assert refused.value.message == REFUSED_TEXT


def test_set_caption_returns_a_new_edit_and_leaves_the_one_it_is_given(tmp_path):
    p, _, _ = project_for(tmp_path)
    edit = load_edit(p.edit_path)
    given = copy.deepcopy(edit)
    edited = set_caption(edit, "s02", "今天來聊")
    assert edited is not edit and edit == given and edited["captions"]["text"] == {"s02": "今天來聊"}
    back = set_caption(edited, "s02", None)
    assert edited["captions"]["text"] == {"s02": "今天來聊"} and back == given
    assert set_caption(edit, "s99", "好")["captions"]["text"] == {"s99": "好"}  # validate checks the id


# ---- the pure steps ---------------------------------------------------------------------------


def test_the_steps_return_a_new_edit_and_leave_the_one_they_are_given(tmp_path):
    p, transcript, trims = project_for(tmp_path)
    edit = load_edit(p.edit_path)
    given = copy.deepcopy(edit)

    cut = set_cut(edit, "t01", True)
    assert cut is not edit and edit == given and "t01" in cut["cuts"]["accept"]

    removed, entry = remove_sentence(edit, transcript, trims, "s02")
    assert removed is not edit and edit == given
    assert entry == {"sentences": [S2_ENTRY], "accepted": ["t01"]}
    entry_before = copy.deepcopy(entry)

    back = restore_sentence(removed, transcript, "s02", {"s02": entry})
    assert back is not removed and "s02" in sentence_ids(back) and sentence_ids(removed) == ["s01", "s03"]
    assert entry == entry_before  # the record is not changed either
    back["sentences"][1]["sem"] = "cta"  # the entry in the new edit is not the record's
    assert entry == entry_before


def test_the_steps_refuse_a_sentence_in_the_wrong_state(tmp_path):
    p, transcript, trims = project_for(tmp_path)
    edit = load_edit(p.edit_path)
    removed, entry = remove_sentence(edit, transcript, trims, "s02")
    with pytest.raises(ChangeRefused) as refused:
        remove_sentence(removed, transcript, trims, "s02")  # not kept any more
    assert refused.value.message == REFUSED_TEXT
    with pytest.raises(ChangeRefused) as refused:
        restore_sentence(edit, transcript, "s02", {})  # not removed
    assert refused.value.message == REFUSED_TEXT


def test_set_cut_does_not_check_the_id_validate_does(tmp_path):
    p, _, _ = project_for(tmp_path)
    edit = set_cut(load_edit(p.edit_path), "t99", True)
    assert "t99" in edit["cuts"]["accept"]


# ---- writing edit.json ------------------------------------------------------------------------


def test_write_edit_leaves_no_temp_file(tmp_path):
    p, _, _ = project_for(tmp_path)
    names = sorted(path.name for path in p.work.iterdir())
    edit = load_edit(p.edit_path)
    edit["captions"]["mode"] = "word"
    write_edit(p, edit)
    assert sorted(path.name for path in p.work.iterdir()) == names
    text = p.edit_path.read_text(encoding="utf-8")
    assert json.loads(text) == edit and text.endswith("}\n")
    assert load_edit(p.edit_path)["captions"]["mode"] == "word"


def test_two_writes_of_edit_json_use_temp_files_of_their_own(tmp_path, monkeypatch):
    """A change from the review page and Claude's validate may write in work/ at once: each write goes through a temp
    file of its own beside its target, which keeps the mode a plain open gives."""
    p, _, _ = project_for(tmp_path)
    names = sorted(path.name for path in p.work.iterdir())
    plain = tmp_path / "plain.txt"
    plain.write_text("made by a plain open", encoding="utf-8")
    temps = []
    replace = os.replace

    def record(source, target):
        temps.append(Path(source))
        replace(source, target)

    monkeypatch.setattr(os, "replace", record)
    edit = load_edit(p.edit_path)
    write_edit(p, edit)
    write_edit(p, edit)
    assert len(temps) == 2 and temps[0] != temps[1] and all(temp.parent == p.work for temp in temps)
    assert sorted(path.name for path in p.work.iterdir()) == names
    assert stat.S_IMODE(p.edit_path.stat().st_mode) == stat.S_IMODE(plain.stat().st_mode)


def test_write_edit_keeps_chinese_as_it_is(tmp_path):
    p, _, _ = project_for(tmp_path, removed_by_claude(("s02", "s02")))
    write_edit(p, load_edit(p.edit_path))
    assert "離題" in p.edit_path.read_text(encoding="utf-8")


def test_a_write_that_fails_leaves_edit_json_and_no_temp_file(tmp_path, monkeypatch):
    p, _, _ = project_for(tmp_path)
    before = p.edit_path.read_bytes()
    bystander = p.work / "edit.json.keep"  # a file of someone else with a name like the temp's
    bystander.write_text("not ours", encoding="utf-8")

    def refuse(source, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(PipelineError) as failed:
        write_edit(p, {**load_edit(p.edit_path), "captions": {"mode": "word"}})
    assert "edit.json" in failed.value.message and "No space left on device" in failed.value.message
    assert p.edit_path.read_bytes() == before
    assert not list(p.work.glob("*.tmp"))
    assert bystander.read_text(encoding="utf-8") == "not ours"


def test_a_change_writes_only_inside_work(tmp_path):
    p, _, _ = project_for(tmp_path)

    def tree(root: Path) -> dict[str, bytes]:
        return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}

    before = tree(p.dir)
    apply_change(p, edit_version(p), change_remove("s02"))
    after = tree(p.dir)
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    assert changed - {"state.json"} == {"work/edit.json", "work/review-undo.json"}

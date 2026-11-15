"""The docs Claude reads (CLAUDE.md and the two skills) and the third-party licence register."""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pipeline.captions import LONG_WORD_WARNING
from pipeline.cli import registered_commands
from pipeline.scenes import CARDS
from pipeline.edit import UNCHANGED_EDIT_WARNING
from pipeline.render import PREVIEW_OVER
from pipeline.scene_render import NODE_MISSING
from pipeline.scenes import CARDS
from pipeline.storyboard import NOT_VALIDATED_REFUSAL, OLDER_FILES_REFUSAL
from pipeline.trim import FEW_PAUSES_WARNING
from pipeline.words import WORDS_FILE

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "CLAUDE.md", ROOT / ".claude/skills/reel/SKILL.md", ROOT / ".claude/skills/storyboard/SKILL.md"]
FEATURE = ROOT / "FEATURE.md"
BACKLOG = ROOT / "docs/backlog/2026-11-12-phase1-review-items.md"
ORIGINAL_SPEC = ROOT / "docs/superpowers/specs/2026-11-11-reels-workflow-design.md"
REVISION_SPEC = "docs/superpowers/specs/2026-11-12-phase1-revision-design.md"
SCENES_SPEC = "docs/superpowers/specs/2026-11-15-remotion-foundation-design.md"
# a sentence or candidate id (s05, t03) that stands alone or next to Chinese characters, not inside a longer word or number
ID = re.compile(r"(?<![A-Za-z0-9])[st]\d{2}(?!\d)")
FENCED = re.compile(r"```(?:text)?\n(.*?)```", re.S)


def test_docs_mention_only_real_commands():
    real = set(registered_commands())
    for d in DOCS:
        used = set(re.findall(r"pipeline ([a-z][a-z0-9-]*)", d.read_text(encoding="utf-8")))
        assert used <= real, (d.name, used - real)


def test_reel_skill_covers_the_flow():
    text = DOCS[1].read_text(encoding="utf-8")
    for c in ("status", "new", "prep", "transcribe", "trim", "validate", "storyboard", "approve", "render"):
        assert f"pipeline {c}" in text


def test_reel_skill_explains_validate_and_the_edit_that_was_checked_against_older_files():
    text = DOCS[1].read_text(encoding="utf-8")
    assert "| `validate` |" in text  # a row of the table of `next` values, which already has one for `edit`
    for message in (UNCHANGED_EDIT_WARNING, OLDER_FILES_REFUSAL, NOT_VALIDATED_REFUSAL):
        assert message in text, message


def test_docs_do_not_describe_what_the_revision_replaced():
    for d in DOCS + [FEATURE]:
        text = d.read_text(encoding="utf-8")
        for gone in (
            "請聽",  # the page no longer marks sentence rows to listen to
            "有腳本外的話",  # extra speech is a candidate of its own, not a reason of a sentence
            "no face detected",
            "captions overlap face",
            "臉太低",
            "字幕放在臉的下方",
            "default_top_px",
            "face_margin_px",
            "設計中",
        ):
            assert gone not in text, (d.relative_to(ROOT), gone)


def test_needs_listen_is_named_only_to_say_that_the_counts_of_transcribe_and_trim_are_not_used():
    for d in DOCS + [FEATURE]:
        lines = [line for line in d.read_text(encoding="utf-8").splitlines() if "needs_listen" in line]
        if d == DOCS[1]:
            assert len(lines) == 1, lines
            assert "`transcribe`" in lines[0] and "`trim`" in lines[0] and "不看" in lines[0], lines[0]
        else:
            assert not lines, (d.relative_to(ROOT), lines)


def test_reel_skill_translates_every_warning_and_knows_the_results_of_the_revision():
    text = DOCS[1].read_text(encoding="utf-8")
    for warning in (FEW_PAUSES_WARNING, LONG_WORD_WARNING, PREVIEW_OVER, "preview failed:"):
        assert warning in text, warning
    assert "有詞太長，字幕從詞的中間斷開" in text
    for word in ("decisions", "listen_points", "storyboard.md", "preview.mp4", "preview_mb", "SendUserFile"):
        assert word in text, word
    assert "caption_dictionary" in text


def test_claude_md_and_reel_skill_say_that_setup_downloads_the_caption_dictionary():
    for d in (DOCS[0], DOCS[1]):
        assert "斷詞字典" in d.read_text(encoding="utf-8"), d.relative_to(ROOT)


def test_reel_skill_has_one_gate_1_message_example_without_ids():
    text = DOCS[1].read_text(encoding="utf-8")
    blocks = [block for block in FENCED.findall(text) if "「可以」" in block]
    assert len(blocks) == 1
    assert not ID.search(blocks[0])  # the user is told item numbers and times, never ids
    for block in FENCED.findall(text):
        assert not ID.search(block), block
    assert "review/gate1/index.html" in blocks[0]  # the message says where the page is


@pytest.mark.parametrize("text", ["看s05那句", "第s05句", "t03剪掉", "s05", "把 t12 接受", "（s01）", "「t03」"])
def test_the_id_check_catches_ids_next_to_chinese_characters(text):
    assert ID.search(text)


@pytest.mark.parametrize("text", ["0:12 字幕用辨識文字", "uv run pipeline status", "剪掉 14 段", "has05x", "s051", "第 s5 句"])
def test_the_id_check_leaves_other_text_alone(text):
    assert not ID.search(text)


def test_reel_skill_gate_1_wording_that_must_not_steer_wrongly():
    text = DOCS[1].read_text(encoding="utf-8")
    assert "沒回的項目照建議" not in text  # 可以 leaves a typo of a recognised caption in, whatever the suggestion was
    assert "我都先照建議設好了" not in text
    assert "不用決定" not in text  # a candidate not in the reel needs no question, but edit.json still lists it
    assert "同時用 SendUserFile" not in text  # the page goes after the gate 1 message, not with it
    assert "傳完這則訊息之後，用 SendUserFile 把 `index.html` 傳給他" in text
    assert "cuts.accept" in text and "cuts.reject" in text
    section = text.split("### 字幕有錯字")[1].split("### 腳本外的話")[0]
    assert "cuts.extra" in section and "`reason`" in section  # removing the sentence is one of the four ways out


def test_removing_a_sentence_also_cuts_the_speech_said_before_it_and_drops_its_sentences_entry():
    reel = DOCS[1].read_text(encoding="utf-8").split("### 字幕有錯字")[1].split("### 腳本外的話")[0]
    story = DOCS[2].read_text(encoding="utf-8").split("### extra：整段移除句子")[1].split("## sentences")[0]
    for name, text in (("reel", reel), ("storyboard", story)):
        for words in ("is removed by cuts.extra", "`sentences`", "`cuts.accept`", "不在成品裡", "`extra`", "`retake`"):
            assert words in text, (name, words)
    items = {m.group(1): line for line in BACKLOG.read_text(encoding="utf-8").splitlines()
             if (m := re.match(r"- (I\d+)\b", line))}
    assert "build_segments" in items["I1"] and "cut_in" in items["I1"]


def test_short_extra_speech_is_not_said_to_be_only_in_the_candidate_table():
    # an accepted one or two units of extra speech in the reel whose cut point is not in silence is a place
    # to listen to (trim_cut_point), so it is not only in the candidate table
    for d in DOCS + [ROOT / REVISION_SPEC]:
        text = d.read_text(encoding="utf-8")
        for wrong in ("一兩個字的只在", "只在網頁的候選表", "只出現在候選表"):
            assert wrong not in text, (d.relative_to(ROOT), wrong)
    row = next(line for line in DOCS[1].read_text(encoding="utf-8").splitlines() if line.startswith("| 要你決定 | 腳本外的話 |"))
    assert "剪掉的、在成品裡、切點不在靜音裡的" in row, row  # only the ones in the reel are listed


def test_the_units_of_the_preview_are_defined_where_they_are_used():
    for d in (DOCS[1], FEATURE):
        text = d.read_text(encoding="utf-8")
        assert "1,048,576" in text and "31,457,280" in text, d.relative_to(ROOT)
    assert "1,000,000" in DOCS[1].read_text(encoding="utf-8")  # preview_mb is decimal MB


def test_claude_md_names_the_revision_design_and_feature_list_does_not_deny_recognised_captions():
    assert REVISION_SPEC in DOCS[0].read_text(encoding="utf-8")
    assert "不會有辨識錯字" not in FEATURE.read_text(encoding="utf-8")


def test_docs_do_not_promise_that_no_word_is_split_and_name_the_word_list_as_the_remedy():
    for d in DOCS + [FEATURE]:
        assert "不會把一個詞拆到兩行" not in d.read_text(encoding="utf-8"), d.relative_to(ROOT)
    assert WORDS_FILE == ROOT / "pipeline/caption_words.txt"
    assert "`pipeline/caption_words.txt`" in DOCS[2].read_text(encoding="utf-8")
    feature = FEATURE.read_text(encoding="utf-8")
    assert "所有影片共用" in feature and "每一支影片的分鏡稿都要重做" in feature  # one list, shared by every video


def test_storyboard_skill_decides_the_listed_candidates_by_kind():
    text = DOCS[2].read_text(encoding="utf-8")
    for word in ("`extra`", "改詞重講", "腳本外的話", "`script_text`", "`display_source`"):
        assert word in text, word


def test_feature_list_holds_the_revision_as_finished_work():
    text = FEATURE.read_text(encoding="utf-8")
    assert "固定在畫面下方置中" in text
    assert "preview.mp4" in text
    assert text.index("## 已完成") < text.index("## 待開發")
    assert "第 1 期改版" not in text.split("## 待開發")[1]


def test_original_design_points_at_the_revision():
    assert (ROOT / REVISION_SPEC).is_file()
    pointer = f"第 1 期改版取代了部分內容，見 `{REVISION_SPEC}` 第 10 節。"
    assert pointer in ORIGINAL_SPEC.read_text(encoding="utf-8")


def test_backlog_marks_what_the_revision_handled_and_leaves_h1():
    text = BACKLOG.read_text(encoding="utf-8")
    items = {m.group(1): line for line in text.splitlines() if (m := re.match(r"- ([A-H]\d+)\b", line))}
    handled = ("A1", "A2", "A3", "A4", "A5", "B5", "B7", "B12", "B13", "B14", "B15")
    for item in handled:
        assert "已在第 1 期改版處理" in items[item] and REVISION_SPEC in items[item], item
        assert "跟下一輪" not in items[item], item
    assert "已在第 1 期改版處理" not in items["H1"]
    row = next(line for line in text.splitlines() if line.startswith("| 已在第 1 期改版處理 |"))
    assert "| 11 |" in row and all(item in row for item in ("A1–A5", "B5", "B7", "B12–B15")), row


def test_reel_skill_starts_the_review_page_and_keeps_the_remote_control_route():
    text = DOCS[1].read_text(encoding="utf-8")
    assert "pipeline review" in text and "Remote Control" in text and "pipeline status" in text


def test_claude_md_lets_the_review_page_write_edit_json_and_approve():
    text = DOCS[0].read_text(encoding="utf-8")
    assert "審閱頁" in text and "確定" in text
    assert "work/review-undo.json" in text and "work/review.mp4" in text  # the files pipeline review makes
    # spec 8.3: with the page open, a 可以 in the chat needs no second question; after an approval in the page,
    # Claude does not ask again in the chat
    assert "他在對話裡回「可以」，你跑 `storyboard`、`approve`、`render`，不再問" in text
    assert "使用者在審閱頁上同意過" in text and "你不在對話裡再問一次" in text


def test_storyboard_skill_explains_notes_and_caption_text():
    text = DOCS[2].read_text(encoding="utf-8")
    assert "`notes`" in text and "captions.text" in text and "建議" in text and "。因為" in text


def test_feature_list_has_the_review_page_and_caption_editing():
    done = FEATURE.read_text(encoding="utf-8").split("## 待開發")[0]
    for words in ("審閱頁", "同意，輸出成品", "按「改字幕」", "Remote Control"):  # 「改字幕」: the caption bullet only
        assert words in done, words


def test_storyboard_skill_explains_scenes_with_the_three_cards_and_what_validate_says():
    text = DOCS[2].read_text(encoding="utf-8")
    assert "## scenes：動畫" in text
    keys = text.split("## 本期的鍵")[1].split("\n\n")[1]  # the paragraph on the keys of this phase
    assert "`scenes`" in keys and "`scenes`、" not in keys  # allowed now, no longer a key of a later phase
    assert "本期的成品沒有動畫" not in text
    for card, (label, _, params) in CARDS.items():
        assert f"`{card}`" in text and label in text, card
        for param in params:
            assert f"`{param.name}`" in text, (card, param.name)
    for word in ("`at`", "`until`", "`char`", '"end": true', "`layout`", "overlaps", "in a row", "so it is not shown",
                 "under 1 s", "the card font", "has no glyph for", "動畫」欄"):
        assert word in text, word
    section = text.split("## scenes：動畫")[1].split("\n## ")[0]
    assert "只在使用者要的地方加" in section  # like punch-in, Claude adds no animation of its own


def test_reel_skill_covers_what_the_animation_cards_need_and_say():
    text = DOCS[1].read_text(encoding="utf-8")
    assert "### 動畫" in text
    for word in ("`node`", "`remotion`", "`remotion_browser`", "npm ci", "Node.js 18.18", "`scenes[",
                 "the card font", "has no glyph for", NODE_MISSING, "scenes/node_modules", "the browser Remotion"):
        assert word in text, word


def test_claude_md_names_the_remotion_foundation_design_and_node():
    text = DOCS[0].read_text(encoding="utf-8")
    assert SCENES_SPEC in text and "Node.js" in text
    assert "動畫還沒有" not in text


def test_original_design_and_feature_list_point_at_the_animation_cards():
    assert (ROOT / SCENES_SPEC).is_file()
    section = ORIGINAL_SPEC.read_text(encoding="utf-8").split("### 8.5 場景渲染")[1].split("### 8.6")[0]
    assert SCENES_SPEC in section
    feature = FEATURE.read_text(encoding="utf-8")
    done, todo = feature.split("## 待開發")
    cards_todo = todo.split("- 其他 2D 動畫場景卡")[1].split("\n- ")[0]
    for card in CARDS.values():
        assert card.label in done, card.label
        assert card.label not in cards_todo, card.label
    assert "推近" in done and "畫面推近" not in todo


def test_skills_have_frontmatter():
    for d in DOCS[1:]:
        head = d.read_text(encoding="utf-8").split("---")[1]
        assert re.search(r"^name: \S+", head, re.M) and re.search(r"^description: .+", head, re.M)


def test_third_party_lists_direct_deps_and_models():
    text = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8").lower()
    deps = [
        re.split(r"[=<>\[ ]", d)[0].lower()
        for d in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    ]
    for name in deps + ["qwen3-asr-1.7b", "qwen3-asr-0.6b", "qwen3-forcedaligner-0.6b", "yunet", "noto sans tc", "ffmpeg"]:
        assert name in text, name


def test_third_party_all_commercial_ok():
    rows = [
        line
        for line in (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and "---" not in line
    ]
    assert not [r for r in rows if "不可" in r or "未知" in r]


def test_transitive_licenses_pass():
    if not (os.environ.get("UV") or shutil.which("uv")):
        pytest.skip("uv not found via UV or PATH: scripts/third_party.py --check reads the lock file with `uv export`")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "third_party.py"), "--check"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_docs_say_the_cards_can_be_seen_before_the_approval():
    """The gate-1 page has a preview of each card and the review page lays the card over the video; the stills are a
    warning when they cannot be made; and only the sound preview is left of gate 2."""
    from pipeline.storyboard import STILLS_FAILED

    reel, storyboard = DOCS[1].read_text(encoding="utf-8"), DOCS[2].read_text(encoding="utf-8")
    assert f"`{STILLS_FAILED.strip()}`" in reel and "動畫的樣子" in reel
    assert "動畫的樣子" in storyboard and "`work/faces.json`" in storyboard.split("## scenes：動畫")[1]
    feature = FEATURE.read_text(encoding="utf-8")
    done, todo = feature.split("## 待開發")
    assert "動畫的樣子" in done
    gate2 = next(line for line in todo.splitlines() if "關卡 2" in line)
    assert "有聲預覽" in gate2 and "樣張" not in gate2
    for path in (*DOCS, FEATURE, ROOT / "README.md", ROOT / "docs/features.md"):
        assert "不播真的動畫" not in path.read_text(encoding="utf-8"), path.name


def test_the_docs_explain_the_version():
    """Spec 2026-11-15-versioning-design, section 8: CLAUDE.md holds the bump rule but not the publish recipe (it
    ships, and the publish setup is the maintainer's), the reel skill reads version and made_with (never the record
    key), the README asks for the version in an Issue and shows what one looks like, and each finished feature in
    FEATURE.md says which version brought it."""
    claude = DOCS[0].read_text(encoding="utf-8")
    for text in ("docs/superpowers/specs/2026-11-15-versioning-design.md", "pyproject.toml", "uv lock"):
        assert text in claude, text
    assert "v<版本>" not in claude and "`public`" not in claude
    reel = DOCS[1].read_text(encoding="utf-8")
    assert "made_with" in reel and "`version`" in reel and "kacha_version" not in reel
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    issues = readme.split("### 出問題找誰？")[1].split("\n#")[0]
    assert "版本" in issues and re.search(r"例如 \d+\.\d+\.\d+", issues)
    feature = FEATURE.read_text(encoding="utf-8")
    done = [part for part in feature.split("\n## ") if part.startswith("已完成")]
    headings = [line for part in done for line in part.splitlines() if line.startswith("### ")]
    assert headings
    for heading in headings:
        assert heading.endswith(("（0.1.0）", "（0.2.0）")), heading
    previews = [line for part in done for line in part.splitlines() if line.startswith("- ") and "樣張" in line]
    assert previews
    for line in previews:  # card previews came in 0.2.0, so every item that speaks of them says so
        assert line.endswith("（0.2.0）"), line


STEP_NAMES = {"prep": "前處理", "transcribe": "轉錄", "trim": "粗剪", "storyboard": "分鏡稿", "render": "輸出",
              "review_video": "審閱用影片", "preview": "手機預覽版"}


def test_the_reel_skill_names_every_step_made_with_can_list():
    """made_with lists each done step by its record name; the skill gives Claude a Chinese name for each, and a
    step added later fails here until it has one."""
    source = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "pipeline").glob("*.py"))
    assert set(re.findall(r'run_step\(\w+, "([a-z_0-9]+)"', source)) == set(STEP_NAMES)
    paragraph = next(line for line in DOCS[1].read_text(encoding="utf-8").splitlines() if line.startswith("版本號："))
    for name in STEP_NAMES.values():
        assert name in paragraph, name

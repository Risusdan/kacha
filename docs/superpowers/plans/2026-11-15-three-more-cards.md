# Three More Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the title-card, bar-chart and versus-split animation cards, end to end: validate, widths and heights, labels, Remotion components, docs.

**Architecture:** The cards plug into the existing card registry (`pipeline/scenes.py` CARDS), the shared sizes file (`scenes/src/sizes.json`) and the Remotion compositions (`scenes/src/Root.jsx`). Placement, cache, render and storyboard need no change beyond what the registry feeds them.

**Tech Stack:** Python 3.12 (pytest), Remotion 4.0.542 with React 19 (node --test for pure JS).

**Spec:** `docs/superpowers/specs/2026-11-15-three-more-cards-design.md`; the foundation it builds on: `docs/superpowers/specs/2026-11-15-remotion-foundation-design.md`.

## Global Constraints

- Every size comes from `scenes/src/sizes.json`; the cards and `validate` read the same file. Colours, radius and fonts come from the style.
- Card frames depend only on the frame number and params (no `Date.now()`, no `Math.random()`).
- Timing shared with the other cards: enter 12 frames, exit 8, scaled down below 30 frames (`timing.js`).
- Example sentences and card texts in tests and docs are made up.
- Execution: inline, small committed steps, at most one subagent at a time (the final review).

## Review Focus

- A bar value with a thousands comma or a decimal ("1,200 元", "2.5 倍"): the bar length must follow 1200 and 2.5, the same in Python and JavaScript.
- A bar whose value is 0 beside non-zero ones: no bar, the value still shows; all zero is refused by validate.
- versus-split with a note on one side only: the card height is that of the taller column.
- title-card without a kicker: no empty line above the title.
- Latin-heavy texts ("iPhone 17 Pro") that pass the character-width limit but draw wider than the zone: validate refuses them by pixel width.

---

### Task 1: The registry and its checks

**Files:** Modify `pipeline/scenes.py`; Test `tests/test_scenes.py`.

**Interfaces — Produces:** `Text(name, least, most, optional=False, size=None)` (size: the sizes.json group to measure with, default the name); `Bars(name, fewest, most_items, least, most, value_most, optional=False)`; `bar_number(value: str) -> float | None` (the number a value starts with: digits, optional thousands commas, optional decimals; None when it does not start with one); CARDS gains `title-card` (`kicker` 0-14 optional, `title` 1-9), `bar-chart` (`bars` 2-4, label 1-6, value 1-8), `versus-split` (`left` 1-5, `left_note` 0-8 optional, `right` 1-5, `right_note` 0-8 optional; sizes groups `side` and `note`).

- [ ] Failing tests: the six cards and labels; each new card's valid params pass; the refusals of spec 6 (missing/extra key, width out of range, 1 or 5 bars, a bar that is not an object or lacks a key, value "兩小時" → `must start with a number`, all values "0" → `at least one value must be more than 0`); `bar_number` on "96%", "1,200 元", "2.5 倍", "0", "1,20", "兩小時", " 3x" (spaces at the ends do not count).
- [ ] Implement; run `uv run pytest tests/test_scenes.py -q`; commit.

### Task 2: Widths, heights and sizes.json

**Files:** Modify `scenes/src/sizes.json`, `pipeline/scenes.py` (`card_texts_px`, `card_height_px`, `_check_widths` key paths for bars); Test `tests/test_scenes.py`.

- [ ] Failing tests: per spec 3, with `text_px` of the default style's fonts: title-card per text 2*56 + px; bar-chart label 2*56 + px, value 2*56 + 420 + 16 + px (keys `bars[k].label`, `bars[k].value`); versus-split per text 2*48 + 2*32 + 88 + 2*px. Heights: title-card with and without kicker; bar-chart of 2 and 4 bars; versus-split with no note, one note, two notes. A too-wide text of each card is refused with its key path.
- [ ] Implement; run; commit.

### Task 3: Labels

**Files:** Modify `pipeline/scenes.py` (`scene_texts`); Test `tests/test_scenes.py`.

- [ ] Failing test: `scene_label` of a bar-chart is ("長條圖", "去年 49%／今年 96%"); of a title-card ("標題卡", "今天聊／三個省電技巧"); of a versus-split without notes ("對比", "自動亮度／手動調整"). The glyph check (scene_render) sees every bar's label and value.
- [ ] Implement; run; commit.

### Task 4: The Remotion cards

**Files:** Create `scenes/src/cards/title-card.jsx`, `bar-chart.jsx`, `versus-split.jsx`, `scenes/src/number.js` (`barNumber`); Modify `scenes/src/Root.jsx`; Test `tests/test_scene_cards.py`, a node test of `barNumber` against the Python examples (see how `timing.js` is tested).

- [ ] Failing tests: the real-render module renders the three new cards (CARDS dict of the test) and its existing checks apply to them (ProRes 4444, frame count, empty first and last frame, opaque on the zone top, width and height within 4 px, identical twice); bar-chart: the accent run of the longest bar is 420 px within 4, a bar of half the largest number about 210; `barNumber` matches `bar_number` on the shared examples.
- [ ] Implement the cards per spec 2; run the real renders; commit.

### Task 5: Docs

**Files:** `.claude/skills/storyboard/SKILL.md`, `.claude/skills/reel/SKILL.md`, `docs/features.md`, `README.md`, `CLAUDE.md`.

- [ ] Card table and writing rules for the three cards; "three cards" → six wherever said; CLAUDE.md lists the new spec. Run `uv run pytest tests/test_docs.py -q`; commit.

### Task 6: Demo and review

- [ ] On a scratch copy of test001 (never the real project), add one of each new card, render, send frames to the user.
- [ ] Full suite; one final review subagent; fix; stop and ask before merging.

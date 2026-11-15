// Tests for pipeline/review_page/logic.js, the pure functions of the review page (no DOM).
// Run from the repo root: node --test tests/js/*.test.mjs (tests/test_review_page.py runs them under pytest).
// Segments are [start, end] pairs in source seconds, as GET /api/data gives them; captions are on the cut reel.

import test from "node:test";
import assert from "node:assert/strict";
import {
  captionAt,
  captionToSave,
  changedRuns,
  clock,
  DIFF_CELLS,
  fieldAfterReload,
  hasUnsavedCaption,
  keepsOpenEditor,
  leadIn,
  litPieces,
  nearestUnit,
  pageState,
  shotTransform,
  sceneAt,
  placePopover,
  playerClock,
  pollVersion,
  previewLine,
  reelTime,
  SEAM_S,
  seekPoint,
  sentenceAt,
  skipTarget,
  sourceTime,
} from "../../pipeline/review_page/logic.js";

const SEGMENTS = [[1, 3], [5, 6]];

test("clock rounds to the tenth like storyboard.clock", () => {
  assert.equal(clock(83.25), "1:23.3");
  assert.equal(clock(0), "0:00.0");
  assert.equal(clock(59.96), "1:00.0");
  assert.equal(clock(600), "10:00.0");
  assert.equal(clock(0.04), "0:00.0");
  assert.equal(clock(-2), "0:00.0");
});

test("reelTime maps like edit.map_time", () => {
  const s = SEGMENTS;
  assert.equal(reelTime(0.5, s), 0);
  assert.equal(reelTime(2, s), 1);
  assert.equal(reelTime(4, s), 2);
  assert.equal(reelTime(5.5, s), 2.5);
  assert.equal(reelTime(9, s), 3);
  assert.equal(reelTime(3, s), 2); // the end of a segment is the start of the next on the reel
  assert.equal(reelTime(1, []), 0);
});

test("sourceTime inverts reelTime", () => {
  const s = SEGMENTS;
  assert.equal(sourceTime(2.5, s), 5.5);
  assert.equal(sourceTime(0, s), 1);
  assert.equal(sourceTime(2, s), 5); // a joint is the start of the later segment
  assert.equal(sourceTime(-1, s), 1);
  assert.equal(sourceTime(3, s), 6); // the end of the reel is the end of the last segment
  assert.equal(sourceTime(99, s), 6);
  for (const r of [0, 0.25, 1.5, 1.999, 2.75]) assert.equal(reelTime(sourceTime(r, s), s), r);
});

test("playerClock shows the reel's length string once the position is at the end of the reel, where clock would round it another way", () => {
  // The reel is 12.3496 s long unrounded, so the server writes 0:12.3; its segments and duration_s are rounded to the
  // millisecond (12.35), and clock of 12.35 is 0:12.4.
  const segments = [[0, 5], [8, 15.35]];
  assert.equal(clock(reelTime(15.35, segments)), "0:12.4");
  assert.equal(playerClock(15.35, segments, 12.35, "0:12.3"), "0:12.3"); // the end of the last segment
  assert.equal(playerClock(15.35 - SEAM_S / 2, segments, 12.35, "0:12.3"), "0:12.3"); // as the video seeks to it
  assert.equal(playerClock(20, segments, 12.35, "0:12.3"), "0:12.3"); // past it
  // inside the reel it is clock of the reel time, as before
  assert.equal(playerClock(0, segments, 12.35, "0:12.3"), "0:00.0");
  assert.equal(playerClock(2.5, segments, 12.35, "0:12.3"), "0:02.5");
  assert.equal(playerClock(9.5, segments, 12.35, "0:12.3"), "0:06.5");
  assert.equal(playerClock(15, segments, 12.35, "0:12.3"), "0:12.0");
  // the rounded segments can add up to less than duration_s: 12.348 here, 12.3504 unrounded (the server writes 0:12.4)
  const short = [[0, 5], [8, 15.348]];
  assert.equal(clock(reelTime(15.348, short)), "0:12.3");
  assert.equal(playerClock(15.348, short, 12.35, "0:12.4"), "0:12.4");
  // or to more than it: a reel time at or past duration_s is the end whatever the source time says
  const long = [[0, 5], [8, 15.352]];
  assert.equal(playerClock(15.3505, long, 12.35, "0:12.3"), "0:12.3");
  // no segment: an empty reel
  assert.equal(playerClock(0, [], 0, "0:00.0"), "0:00.0");
  assert.equal(playerClock(3, [], 0, "0:00.0"), "0:00.0");
});

test("skipTarget jumps over a cut to a hair past the next start, stays inside a segment, ends after the last", () => {
  const s = SEGMENTS;
  assert.equal(skipTarget(0.2, s), 1 + SEAM_S);
  assert.equal(skipTarget(2, s), null);
  assert.equal(skipTarget(3.01, s), 5 + SEAM_S);
  assert.equal(skipTarget(6.0, s), -1);
  assert.equal(skipTarget(3, s), 5 + SEAM_S); // a segment ends before its end
  assert.equal(skipTarget(1, s), null);
  assert.equal(skipTarget(0.9995, s), null); // a seek that lands a hair before a segment is inside it
  assert.equal(skipTarget(1 + SEAM_S, s), null); // where a jump lands is inside: no jump again
  assert.equal(skipTarget(7, s), -1);
  assert.equal(skipTarget(0, []), -1);
});

test("skipTarget with a lookahead jumps on the last frame of a segment", () => {
  const s = SEGMENTS;
  const frame = 1 / 30;
  assert.equal(skipTarget(2.9, s, frame), null); // the next frame is still in the segment
  assert.equal(skipTarget(89 / 30, s, frame), 5 + SEAM_S); // the next frame would be the first one of the cut
  assert.equal(skipTarget(2.97, s, frame), 5 + SEAM_S);
  assert.equal(skipTarget(179 / 30, s, frame), -1); // the last frame of the last segment ends the reel
  assert.equal(skipTarget(5.5, s, frame), null);
  assert.equal(skipTarget(0.2, s, frame), 1 + SEAM_S); // before a segment it jumps past its start, as without a lookahead
  assert.equal(skipTarget(89 / 30, s), null); // without a lookahead the last frame plays
  assert.equal(skipTarget(5 + SEAM_S, s, frame), null);
  // a frame shown just before a start (the seek had to decode up to it) plays on, no seek again
  assert.equal(skipTarget(1 - frame, s, frame), null);
  assert.equal(skipTarget(5 - frame, s, frame), null);
  assert.equal(skipTarget(1 - 2 * frame, s, frame), 1 + SEAM_S); // two frames before, it is in the cut
});

test("seekPoint puts the video a hair past a segment's start, so the frame shown is the segment's first", () => {
  const s = SEGMENTS;
  assert.equal(seekPoint(2, s), 2); // inside a segment: the time itself
  assert.equal(seekPoint(1.002, s), 1.002);
  assert.equal(seekPoint(1, s), 1 + SEAM_S); // at a start
  assert.equal(seekPoint(0.9995, s), 1 + SEAM_S); // a hair before it
  assert.equal(seekPoint(1.0005, s), 1 + SEAM_S); // a hair after it
  assert.equal(seekPoint(4, s), 5 + SEAM_S); // in a cut
  assert.equal(seekPoint(0, s), 1 + SEAM_S); // before the first segment
  assert.equal(seekPoint(7, s), 6); // after the last: its end
  // a start rounded down to the millisecond: frame 145 is at 4.8333 s, its segment starts at 4.833
  assert.ok(seekPoint(4.833, [[4.833, 6]]) > 145 / 30);
  assert.ok(seekPoint(4, [[4.833, 6]]) > 145 / 30);
  // a start rounded up: frame 146 is at 4.8667 s, its segment starts at 4.867; the point stays on that frame
  assert.ok(seekPoint(4.867, [[4.867, 6]]) < 147 / 30);
});

test("captionAt finds the caption on screen at a reel time", () => {
  const first = { start: 0, end: 1.5, pieces: [{ text: "今天", t: 0 }] };
  const second = { start: 1.5, end: 3, pieces: [{ text: "我們", t: 1.5 }] };
  const third = { start: 4, end: 5, pieces: [{ text: "來聊", t: 4 }] };
  const captions = [first, second, third];
  assert.equal(captionAt(0, captions), first);
  assert.equal(captionAt(1.49, captions), first);
  assert.equal(captionAt(1.5, captions), second); // a caption ends where the next starts
  assert.equal(captionAt(3.5, captions), null); // between two captions nothing shows
  assert.equal(captionAt(4.2, captions), third);
  assert.equal(captionAt(5, captions), null);
  assert.equal(captionAt(-1, captions), null);
  assert.equal(captionAt(1, []), null);
});

test("litPieces counts the pieces already spoken in word mode", () => {
  const caption = {
    start: 1,
    end: 3,
    pieces: [
      { text: "「", t: null },
      { text: "今天", t: 1.2 },
      { text: "，", t: null },
      { text: "我們", t: 1.8 },
      { text: "來", t: 2.4 },
      { text: "」", t: null },
    ],
  };
  assert.equal(litPieces(caption, 0.5), 0); // before the caption shows
  // the first timed piece lights at the caption's start (as the \k tags of the render do), with the marks before it
  assert.equal(litPieces(caption, 1), 3);
  assert.equal(litPieces(caption, 1.79), 3); // a piece without a time goes with the one before it
  assert.equal(litPieces(caption, 1.8), 4);
  assert.equal(litPieces(caption, 2.4), 6);
  assert.equal(litPieces(caption, 2.9), 6);
  assert.equal(litPieces({ start: 0, end: 1, pieces: [{ text: "。", t: null }] }, 0.5), 1);
});

test("sentenceAt finds the kept sentence that holds a source time", () => {
  const sentences = [
    { id: "a", state: "kept", start: 0, end: 1.6 },
    { id: "b", state: "removed", start: 1.6, end: 2.4 },
    { id: "c", state: "kept", start: 2.6, end: 3.4 },
    { id: "d", state: "unspoken", start: 3.4, end: 3.4 },
  ];
  assert.equal(sentenceAt(0.5, sentences).id, "a");
  assert.equal(sentenceAt(1.6, sentences), null); // a removed sentence is never the one playing
  assert.equal(sentenceAt(2.5, sentences), null);
  assert.equal(sentenceAt(2.6, sentences).id, "c");
  assert.equal(sentenceAt(3.4, sentences), null);
});

test("leadIn stops a little before a time on the reel", () => {
  const s = SEGMENTS;
  assert.equal(leadIn(5.5, s, 1), 2.5); // reel 2.5; one second earlier is reel 1.5, which is source 2.5
  assert.equal(leadIn(4, s, 0.5), 2.5); // a cut maps to the joint at reel 2; half a second before is source 2.5
  assert.equal(leadIn(1.2, s, 1), 1); // never before the start of the reel
});

test("pollVersion loads quietly after the page's own change and says so after Claude's", () => {
  // the page changed the edit: its data is that version already, and the poll sees it
  assert.deepEqual(pollVersion({ shown: "v2", failed: false, polled: "v2", own: "v2", outdated: false }),
    { load: false, notice: false, own: null });
  // its own change, before the page loaded it (the load after the change failed): load, no notice
  assert.deepEqual(pollVersion({ shown: "v1", failed: true, polled: "v2", own: "v2", outdated: false }),
    { load: true, notice: false, own: null });
  // Claude changed the edit
  assert.deepEqual(pollVersion({ shown: "v2", failed: false, polled: "v3", own: null, outdated: false }),
    { load: true, notice: true, own: null });
  assert.deepEqual(pollVersion({ shown: "v2", failed: false, polled: "v3", own: "v2", outdated: false }),
    { load: true, notice: true, own: null });
  // nothing changed
  assert.deepEqual(pollVersion({ shown: "v2", failed: false, polled: "v2", own: null, outdated: false }),
    { load: false, notice: false, own: null });
});

test("pollVersion says so when Claude puts back the bytes of an earlier version the page made", () => {
  // the page made v2 (a poll saw it), then v3; Claude puts edit.json back as it was at v2
  const seen = pollVersion({ shown: "v2", failed: false, polled: "v2", own: "v2", outdated: false });
  assert.equal(seen.own, null);
  assert.deepEqual(pollVersion({ shown: "v3", failed: false, polled: "v2", own: "v3", outdated: false }),
    { load: true, notice: true, own: null });
  // Claude puts back the version from before the page's last change, before any poll saw that change
  assert.deepEqual(pollVersion({ shown: "v2", failed: false, polled: "v1", own: "v2", outdated: false }),
    { load: true, notice: true, own: null });
});

test("pollVersion drops an answer sent before the page's last change landed", () => {
  // the poll left with v1 on the page; the change made v2 and the page loaded it; then the answer came, with v1
  assert.deepEqual(pollVersion({ shown: "v2", failed: false, polled: "v1", own: "v2", outdated: true }),
    { load: false, notice: false, own: "v2" });
  // the next poll sees v2 and lets the own version go
  assert.deepEqual(pollVersion({ shown: "v2", failed: false, polled: "v2", own: "v2", outdated: false }),
    { load: false, notice: false, own: null });
});

test("pollVersion loads the first data without a notice, and tries again after a failed load", () => {
  assert.deepEqual(pollVersion({ shown: null, failed: true, polled: "v1", own: null, outdated: false }),
    { load: true, notice: false, own: null });
  // Claude was changing the edit and put it back as it was: load again (the banner goes), no notice
  assert.deepEqual(pollVersion({ shown: "v1", failed: true, polled: "v1", own: null, outdated: false }),
    { load: true, notice: false, own: null });
  assert.deepEqual(pollVersion({ shown: "v1", failed: true, polled: "v4", own: null, outdated: false }),
    { load: true, notice: true, own: null });
});

test("placePopover opens below a mark, above it when there is no room below, else scrolls the panel", () => {
  const view = { top: 100, left: 0, width: 600, height: 400 }; // the visible part of the panel's content
  const size = { width: 340, height: 150 };
  // room below
  assert.deepEqual(placePopover({ anchor: { top: 150, bottom: 170, left: 40 }, size, view }),
    { top: 176, left: 40, scrollTop: 100 });
  // a mark low in the panel: above it
  assert.deepEqual(placePopover({ anchor: { top: 440, bottom: 460, left: 40 }, size, view }),
    { top: 284, left: 40, scrollTop: 100 });
  // no room either way: below it, and the panel scrolls so the whole popover shows
  const tall = { width: 340, height: 300 };
  const placed = placePopover({ anchor: { top: 300, bottom: 320, left: 40 }, size: tall, view });
  assert.equal(placed.top, 326);
  assert.equal(placed.scrollTop, 326 + 300 + 6 - 400);
  assert.ok(placed.scrollTop <= 300 - 6, "the mark stays in view");
  // near the right edge: inside the panel
  assert.equal(placePopover({ anchor: { top: 150, bottom: 170, left: 500 }, size, view }).left, 600 - 340 - 8);
  assert.equal(placePopover({ anchor: { top: 150, bottom: 170, left: -20 }, size, view }).left, 8);
});

test("nearestUnit picks the free unit of a sentence closest to a time", () => {
  const tokens = [
    { k: "unit", text: "今", start: 0, end: 0.2, trim: null },
    { k: "trim", trim: "x", text: "嗯" },
    { k: "unit", text: "天", start: 0.2, end: 0.4, trim: "y" },
    { k: "char", text: "，" },
    { k: "unit", text: "好", start: 0.6, end: 0.8, trim: null },
    { k: "unit", text: "嗎", start: null, end: null, trim: null },
  ];
  assert.equal(nearestUnit(tokens, 0), 0);
  assert.equal(nearestUnit(tokens, 0.1), 0);
  assert.equal(nearestUnit(tokens, 0.3), 0); // inside 天, which is a candidate's; 今 ends 0.1 away, 好 starts 0.3 away
  assert.equal(nearestUnit(tokens, 0.45), 4); // 今 ends 0.25 away, 好 starts 0.15 away
  assert.equal(nearestUnit(tokens, 0.7), 4);
  assert.equal(nearestUnit(tokens, 9), 4);
  assert.equal(nearestUnit([{ k: "char", text: "。" }], 1), -1);
});

test("changedRuns marks the characters of the edited caption that are not the original's", () => {
  const runs = (original, edited) => changedRuns(original, edited).map(({ text, changed }) => [text, changed]);
  assert.deepEqual(runs("大概多成兩小時吧", "大概多成兩小時吧"), [["大概多成兩小時吧", false]]);
  assert.deepEqual(runs("大概多成兩小時吧", "大概多撐兩小時吧"), [["大概多", false], ["撐", true], ["兩小時吧", false]]);
  assert.deepEqual(runs("大概多成兩小時吧", "大概多撐了兩小時吧"),
    [["大概多", false], ["撐了", true], ["兩小時吧", false]]);
  assert.deepEqual(runs("大概多成兩小時吧", "喔大概多成兩小時"), [["喔", true], ["大概多成兩小時", false]]);
  assert.deepEqual(runs("大概多成兩小時吧", "大概兩小時"), [["大概兩小時", false]]); // a deletion leaves no mark
  assert.deepEqual(runs("大概多成兩小時吧", "差不多三個鐘頭"), [["差不", true], ["多", false], ["三個鐘頭", true]]);
  assert.deepEqual(runs("今天好好", "今天好好好"), [["今天好好", false], ["好", true]]);
  assert.deepEqual(runs("", "新的字"), [["新的字", true]]);
  assert.deepEqual(runs("原本的字", ""), []);
});

test("changedRuns keeps a character outside the basic plane whole", () => {
  assert.deepEqual(changedRuns("今天好", "今天\u{1F600}好"), [
    { text: "今天", changed: false }, { text: "\u{1F600}", changed: true }, { text: "好", changed: false },
  ]);
  assert.deepEqual(changedRuns("\u{1F600}a", "\u{1F603}a"), [
    { text: "\u{1F603}", changed: true }, { text: "a", changed: false },
  ]);
});

test("changedRuns compares a text too long for the table by its common head and tail", () => {
  const original = "甲".repeat(1500) + "乙" + "丙".repeat(1500);
  const edited = "甲".repeat(1500) + "丁" + "丙".repeat(1500);
  assert.ok(3001 * 3001 > DIFF_CELLS); // the whole texts would not fit the table; their middles do
  assert.deepEqual(changedRuns(original, edited), [
    { text: "甲".repeat(1500), changed: false }, { text: "丁", changed: true }, { text: "丙".repeat(1500), changed: false },
  ]);
  // no common head or tail, and a middle of 1600 by 1600 characters: past the table, every character counts as changed
  assert.ok(1600 * 1600 > DIFF_CELLS);
  assert.deepEqual(changedRuns("丙乙".repeat(800), "乙丙".repeat(800)), [{ text: "乙丙".repeat(800), changed: true }]);
});

test("previewLine shares the time of the sentence equally among its lines", () => {
  const lines = ["第一行", "第二行"];
  assert.equal(previewLine(10, 10, 14, lines), "第一行");
  assert.equal(previewLine(11.99, 10, 14, lines), "第一行");
  assert.equal(previewLine(12, 10, 14, lines), "第二行");
  assert.equal(previewLine(13.99, 10, 14, lines), "第二行");
  assert.equal(previewLine(14, 10, 14, lines), null); // after the sentence
  assert.equal(previewLine(9.99, 10, 14, lines), null); // before it
  assert.equal(previewLine(11, 10, 14, []), null);
  assert.equal(previewLine(10, 10, 10, lines), null); // a sentence with no time in the reel
});

test("captionToSave: nothing for the saved caption, null for the sentence's own text, else the text", () => {
  const own = "這招能幫你省很多事";
  const edited = "這招能幫你省很多時間";
  // a sentence that shows its own text
  assert.equal(captionToSave(own, own, own), undefined);
  assert.equal(captionToSave(edited, own, own), edited);
  // a sentence with a caption text
  assert.equal(captionToSave(edited, edited, own), undefined);
  assert.equal(captionToSave(own, edited, own), null);
  assert.equal(captionToSave("這招省時間", edited, own), "這招省時間");
  assert.equal(captionToSave("", edited, own), ""); // the server says why an empty text is not saved
  // Claude changed the caption while the editor was open: what is saved is Claude's text now
  const claude = "這招幫你省下時間";
  assert.equal(captionToSave(claude, claude, own), undefined); // typed back what is saved: nothing to send
  assert.equal(captionToSave(edited, claude, own), edited); // the caption from before Claude's change is sent
  assert.equal(captionToSave(own, claude, own), null);
});

test("hasUnsavedCaption: the field holds text that closing the editor without saving would lose", () => {
  const own = "這招能幫你省很多事";
  const edited = "這招能幫你省很多時間";
  // a sentence that shows its own text
  assert.equal(hasUnsavedCaption(own, own, own), false); // nothing typed
  assert.equal(hasUnsavedCaption(edited, own, own), true);
  assert.equal(hasUnsavedCaption("", own, own), true); // an emptied field is a change too (the server says why not)
  // a sentence with a caption text
  assert.equal(hasUnsavedCaption(edited, edited, own), false);
  assert.equal(hasUnsavedCaption(own, edited, own), true); // the sentence's own text again: its caption text goes
  assert.equal(hasUnsavedCaption("這招省時間", edited, own), true);
  // the same test as the one that saving makes: a text captionToSave sends nothing for is nothing to lose
  for (const [text, saved] of [[own, own], [edited, edited], [edited, own], [own, edited], ["", edited]]) {
    assert.equal(hasUnsavedCaption(text, saved, own), captionToSave(text, saved, own) !== undefined);
  }
});

test("fieldAfterReload: an untouched field follows a caption Claude changed, a typed one stays", () => {
  const before = "這招能幫你省很多事";
  const claude = "這招幫你省下時間";
  const typed = "這招能幫你省很多時間";
  // nothing typed (the field still holds the saved caption of before): it takes the new saved caption
  assert.equal(fieldAfterReload(before, before, claude), claude);
  // the user typed something: it stays, whatever the saved caption became
  assert.equal(fieldAfterReload(typed, before, claude), typed);
  assert.equal(fieldAfterReload("", before, claude), ""); // an emptied field is typed text too
  assert.equal(fieldAfterReload(claude, before, claude), claude); // typed what Claude wrote: unchanged either way
  // the saved caption did not change: nothing moves
  assert.equal(fieldAfterReload(before, before, before), before);
  assert.equal(fieldAfterReload(typed, before, before), typed);
  // once it follows, there is nothing to save: no 儲存 writes the old text back, no dropped notice, no refusal to open
  const shown = fieldAfterReload(before, before, claude);
  assert.equal(captionToSave(shown, claude, before), undefined);
  assert.equal(hasUnsavedCaption(shown, claude, before), false);
});

test("keepsOpenEditor: the open editor stays when it is of the same sentence, or of another one and holds text that is not saved", () => {
  const own = "這招能幫你省很多事";
  const edited = "這招能幫你省很多時間";
  const open = (text, saved = own) => ({ id: "s02", text, saved, original: own });
  // nothing open: the editor of any sentence opens
  assert.equal(keepsOpenEditor(null, "s03"), false);
  // another sentence's editor holds text to save: it stays, the new one does not open
  assert.equal(keepsOpenEditor(open(edited), "s03"), true);
  assert.equal(keepsOpenEditor(open(""), "s03"), true);
  assert.equal(keepsOpenEditor(open(own, edited), "s03"), true); // the sentence's own text again is a change to save
  // another sentence's editor holds nothing to save: the new one opens
  assert.equal(keepsOpenEditor(open(own), "s03"), false);
  assert.equal(keepsOpenEditor(open(edited, edited), "s03"), false);
  // the same sentence: the open editor stays whatever its field holds, so that asking for it never drops typed text
  assert.equal(keepsOpenEditor(open(edited), "s02"), true);
  assert.equal(keepsOpenEditor(open(""), "s02"), true);
  assert.equal(keepsOpenEditor(open(own), "s02"), true);
  assert.equal(keepsOpenEditor(open(edited, edited), "s02"), true);
});

test("pageState takes the state, step, outputs and error text from the state keys of the server's answer", () => {
  // the keys GET /api/data carries for the first view are those GET /api/state has, so one reading serves both
  const working = { state: "working", step: "approve", error: null, outputs: null };
  assert.deepEqual(pageState(working), { state: "working", step: "approve", outputs: null, failedText: null });
  const failed = { state: "failed", step: null, error: "輸出失敗了。", outputs: null };
  assert.deepEqual(pageState(failed), { state: "failed", step: null, outputs: null, failedText: "輸出失敗了。" });
  const outputs = { final: "out/final.mp4", preview: null, folder: "out" };
  const done = { state: "done", step: null, error: null, outputs };
  assert.deepEqual(pageState(done), { state: "done", step: null, outputs, failedText: null });
  // keys the answer does not have are null, never undefined
  assert.deepEqual(pageState({ state: "editing" }), { state: "editing", step: null, outputs: null, failedText: null });
});


test("shotTransform scales the picture about the anchor of the shot that plays; nothing at the base framing", () => {
  const shots = [[0, 2, 1, 0.5, 0.5], [3, 5, 1.1, 0.2, 0.43], [5, 6, 1.2, 0.18, 0.4]];
  const second = { transform: "scale(1.1)", origin: "20% 43%" };
  const last = { transform: "scale(1.2)", origin: "18% 40%" };
  assert.equal(shotTransform(1, shots), null); // the base framing
  assert.deepEqual(shotTransform(3, shots), second);
  assert.deepEqual(shotTransform(4.99, shots), second);
  assert.deepEqual(shotTransform(5, shots), last); // the next shot from its start
  // in a cut, which the player is skipping, and before the first shot: the shot it goes to, so that no frame on
  // the way shows another framing
  assert.deepEqual(shotTransform(2.5, shots), second);
  assert.deepEqual(shotTransform(3 - SEAM_S / 2, shots), second);
  assert.deepEqual(shotTransform(-1, [[1, 2, 1.1, 0.5, 0.5]]), { transform: "scale(1.1)", origin: "50% 50%" });
  // at and after the end of the reel: the last shot, where the player stops
  assert.deepEqual(shotTransform(6, shots), last);
  assert.deepEqual(shotTransform(60, shots), last);
  assert.equal(shotTransform(1, []), null);
});

test("sceneAt is the scene whose source span holds the time, from its start up to its end", () => {
  const first = { start: 2, end: 4.5, label: "動畫：數字卡　兩小時／一天多撐" };
  const second = { start: 6, end: 8, label: "動畫：重點字　省電" };
  const scenes = [first, second];
  assert.equal(sceneAt(1.99, scenes), null);
  assert.equal(sceneAt(2, scenes), first);
  assert.equal(sceneAt(4.49, scenes), first);
  assert.equal(sceneAt(4.5, scenes), null);
  assert.equal(sceneAt(7, scenes), second);
  assert.equal(sceneAt(8, scenes), null);
  assert.equal(sceneAt(3, []), null);
});

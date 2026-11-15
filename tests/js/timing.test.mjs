// Tests for scenes/src/timing.js, the timing the animation cards share (no Remotion, no browser).
// Run from the repo root: node --test tests/js/*.test.mjs (tests/test_review_page.py runs them under pytest).
// A card is `duration` frames long; frame 0 is its first frame and duration - 1 its last.

import test from "node:test";
import assert from "node:assert/strict";
import { enterProgress, exitProgress, inOut, stagger, tickProgress, TICK_FRAMES } from "../../scenes/src/timing.js";

test("a card of 30 frames or more enters in 12 frames and leaves in 8", () => {
  for (const duration of [30, 31, 45, 90, 300]) {
    assert.deepEqual(inOut(duration), { enter: 12, exit: 8 }, `duration ${duration}`);
  }
});

test("under 30 frames the entrance and the exit shrink in proportion", () => {
  assert.deepEqual(inOut(15), { enter: 6, exit: 4 });
  assert.deepEqual(inOut(20), { enter: 8, exit: 5 });
  assert.deepEqual(inOut(10), { enter: 4, exit: 3 });
  assert.deepEqual(inOut(1), { enter: 0, exit: 0 });
  assert.deepEqual(inOut(0), { enter: 0, exit: 0 });
});

test("the entrance and the exit never overlap", () => {
  for (let duration = 0; duration <= 60; duration += 1) {
    const { enter, exit } = inOut(duration);
    assert.ok(enter + exit <= duration, `duration ${duration}: ${enter} + ${exit}`);
  }
});

test("the entrance runs from 0 at the first frame to 1 at frame `enter`, and stays 1", () => {
  assert.equal(enterProgress(0, 90), 0);
  assert.equal(enterProgress(6, 90), 0.5);
  assert.equal(enterProgress(12, 90), 1);
  assert.equal(enterProgress(50, 90), 1);
  assert.equal(enterProgress(-3, 90), 0);
});

test("the exit is 0 until its 8 frames start, and 1 at the last frame", () => {
  assert.equal(exitProgress(0, 90), 0);
  assert.equal(exitProgress(81, 90), 0);
  assert.equal(exitProgress(85, 90), 0.5);
  assert.equal(exitProgress(89, 90), 1);
});

test("a card too short for an entrance or an exit is shown whole from its first frame to its last", () => {
  assert.equal(enterProgress(0, 1), 1);
  assert.equal(exitProgress(0, 1), 0);
});

test("the items of a list appear one by one from the end of the entrance to the middle of the card", () => {
  assert.deepEqual(stagger(2, 90), [12, 45]);
  assert.deepEqual(stagger(3, 90), [12, 29, 45]);
  assert.deepEqual(stagger(4, 90), [12, 23, 34, 45]);
});

test("in a short card the items still appear in order, no earlier than the end of the entrance", () => {
  assert.deepEqual(stagger(3, 15), [6, 7, 7]);
  assert.deepEqual(stagger(4, 2), [1, 1, 1, 1]);
});

test("a tick is drawn in the 6 frames after its item appears", () => {
  assert.equal(TICK_FRAMES, 6);
  assert.equal(tickProgress(29, 29), 0);
  assert.equal(tickProgress(32, 29), 0.5);
  assert.equal(tickProgress(35, 29), 1);
  assert.equal(tickProgress(20, 29), 0);
});

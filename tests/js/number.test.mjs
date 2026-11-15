// Tests for scenes/src/number.js, the number a bar-chart value starts with. The examples in bar_numbers.json are
// the ones tests/test_scenes.py gives pipeline.scenes.bar_number, so that both read a value the same way.
// Run from the repo root: node --test tests/js/*.test.mjs (tests/test_review_page.py runs them under pytest).

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { barNumber } from "../../scenes/src/number.js";

const EXAMPLES = JSON.parse(readFileSync(new URL("./bar_numbers.json", import.meta.url), "utf8"));

test("a value's number is read as pipeline.scenes.bar_number reads it", () => {
  for (const [value, number] of EXAMPLES) {
    assert.equal(barNumber(value), number, JSON.stringify(value));
  }
});

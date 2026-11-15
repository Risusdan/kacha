// DOM tests of the review page (pipeline/review_page): the page's modules run as they are on the fake browser of
// fake_browser.mjs, against a fake review server. Each test opens the page anew (review.js starts: the first load,
// then the poll loop) and drives it as the user and Claude would: presses, typing, and polls after Claude changed the
// edit or the state. They cover what the tests of the pure functions (logic.test.mjs) cannot: the confirm dialog
// approves the version it showed, and text typed in the caption editor is never lost without a word.
// Run from the repo root: node --test tests/js/*.test.mjs. The sentences are made up.

import { afterEach, describe, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { FakeEvent, installBrowser, settle } from "./fake_browser.mjs";

const PAGE_DIR = new URL("../../pipeline/review_page/", import.meta.url);
const INDEX_HTML = readFileSync(new URL("index.html", PAGE_DIR), "utf8");
const POLL_MS = 3000; // review.js asks GET api/state this often

// The page's texts, word for word.
const UPDATED_TEXT = "Claude 改了剪接設定，頁面已更新。";
const CONFLICT_TEXT = "決定剛被更新過，請再看一次。";
const REFUSED_TEXT = "這個改動會讓剪接設定出錯，沒有存。請回對話請 Claude 看看。";
const UNSAVED_CAPTION_TEXT = "改的字幕還沒存，先按「儲存」或「取消」，再按同意。";
const UNSAVED_OPEN_TEXT = "改的字幕還沒存，先按「儲存」或「取消」，再改別句。";
const CAPTION_DROPPED_TEXT = "剪接設定剛被更新，你改到一半的字幕沒有存。";

// ---- the data and the server ------------------------------------------------------------------------------------

const STYLE = {
  size_px: 56, color: "#FFFFFF", highlight: "#FFD400", outline_color: "#000000", outline_px: 4, bold: true,
  top_px: 1158, canvas_w: 1080, canvas_h: 1920,
};

// A kept sentence as GET api/data gives it: `text`, said from `start` at 0.2 s a character (the full stop has no time).
function sentence(id, time, start, text, more = {}) {
  const chars = Array.from(text);
  const tokens = chars.map((char, i) => (char === "。" ? { k: "char", text: char }
    : { k: "unit", text: char, start: start + i * 0.2, end: start + (i + 1) * 0.2, trim: null }));
  return {
    id, state: "kept", time, start, end: start + (chars.length - 1) * 0.2, asr: false, recognised: false,
    caption_edited: false, text, caption: text, original: text, script: text, note: null, removal: null, tokens,
    ...more,
  };
}

function pageData() {
  return {
    project: "2026-11-14-demo", version: "v1", mode: "line",
    source_s: 10, source_clock: "0:10.0", duration_s: 10, duration_clock: "0:10.0",
    segments: [[0, 10]], shots: [[0, 10, 1, 0.5, 0.5]], scenes: [], captions: [], caption_style: STYLE,
    sentences: [
      sentence("s01", "0:00.0", 0, "今天來聊三個小技巧。"),
      sentence("s02", "0:02.5", 2.5, "第一是把亮度調低。"),
      // its captions show what was recognised, which is not what the script says: it has the typo card
      sentence("s03", "0:05.0", 5, "第二是把對筆調高。", { asr: true, recognised: true, script: "第二是把對比調高。" }),
      sentence("s04", "0:07.5", 7.5, "最後記得存檔。"),
    ],
    trims: [], cuts: 0, nav: { decisions: [], listen: [], asr: ["s03"] },
  };
}

// The state of the server as GET api/state gives it, without the version.
function statusOf(state, step = null) {
  const outputs = state === "done" ? { final: "out/final.mp4", preview: "out/preview.mp4" } : null;
  return { state, step, error: null, outputs };
}

// The review server as the page sees it: GET api/data and api/state from `data` and `status`, the lines of a caption
// text (one line: the text), a change of the page applied to `data` (a test can put another answer in `change`), and
// an approval that starts the job. Every request is noted in `requests`, and every answer is a copy, as JSON is.
class FakeServer {
  data = pageData();
  status = statusOf("editing");
  requests = [];
  change = (body) => this.applyChange(body.change);

  fetch = async (url, init = {}) => {
    const method = init.method ?? "GET";
    const body = init.body === undefined ? undefined : JSON.parse(init.body);
    this.requests.push({ route: `${method} ${url}`, body });
    const [status, answer] = this.answer(`${method} ${url}`, body);
    return { status, json: async () => structuredClone(answer) };
  };

  answer(route, body) {
    if (route === "GET api/data") return [200, { ...this.data, ...this.status }];
    if (route === "GET api/state") return [200, { version: this.data.version, ...this.status }];
    if (route === "POST api/caption-lines") return [200, { lines: [body.text] }];
    if (route === "POST api/change") return this.change(body);
    if (route === "POST api/approve") {
      this.status = statusOf("working", "storyboard");
      return [202, { version: this.data.version, ...this.status }];
    }
    throw new Error(`the fake server has no route ${route}`);
  }

  // The bodies of the requests sent to `route`, in order.
  sent(route) {
    return this.requests.filter((request) => request.route === route).map((request) => request.body);
  }

  sentence(id) {
    return this.data.sentences.find((item) => item.id === id);
  }

  newVersion() {
    this.data.version = `v${Number(this.data.version.slice(1)) + 1}`;
    return this.data.version;
  }

  // Claude changes the edit from the chat: `change` edits the data, and the version moves on.
  claude(change) {
    change(this);
    this.newVersion();
  }

  // A change the page sends, applied as the server would: a sentence's caption text, or its removal.
  applyChange(change) {
    const item = this.sentence(change.sentence);
    if (change.type === "caption") {
      Object.assign(item, { caption: change.text ?? item.original, caption_edited: change.text !== null });
    } else if (change.type === "remove") {
      Object.assign(item, { state: "removed", removal: { by_page: true, reason: "在審閱頁拿掉" } });
    } else {
      throw new Error(`the fake server does not apply ${change.type}`);
    }
    return [200, { version: this.newVersion() }];
  }
}

// What Claude does from the chat, for server.claude.
const removes = (id) => (server) => Object.assign(server.sentence(id), {
  state: "removed", removal: { by_page: false, reason: "和主題無關" },
});
const transcribesAgain = (id) => (server) => Object.assign(server.sentence(id), {
  text: "第一是把亮度調低一點。", original: "第一是把亮度調低一點。", caption: "第一是把亮度調低一點。",
});
const changesCaption = (id, text) => (server) => Object.assign(server.sentence(id), {
  caption: text, caption_edited: true,
});

// ---- the page ---------------------------------------------------------------------------------------------------

let browser = null;
let runs = 0;

// Open the page on a new fake browser with `server` behind its fetch. review.js is imported anew (the query makes it
// another module), so it takes this browser's elements and starts: the first load, then the first poll. The modules it
// imports are the same for the whole file (it imports them by their plain URLs), so the page state of shared.js is set
// back to the one a new copy of shared.js declares.
async function openPage(server = new FakeServer()) {
  browser = installBrowser({ html: INDEX_HTML, fetch: server.fetch });
  runs += 1;
  const { page } = await import(new URL("shared.js", PAGE_DIR).href);
  Object.assign(page, (await import(new URL(`shared.js?run=${runs}`, PAGE_DIR).href)).page);
  await import(new URL(`review.js?run=${runs}`, PAGE_DIR).href);
  await settle();
  assert.equal(page.state, "editing");
  return { server, page };
}

afterEach(() => {
  browser?.restore();
  browser = null;
});

const $ = (id) => browser.document.getElementById(id);
const focused = () => browser.document.activeElement;
const notice = () => $("notice");
const row = (id) => $("sentences").querySelector(`.sentence[data-sentence="${id}"]`);
const editors = () => $("sentences").querySelectorAll(".edit");

function editorNode() {
  const found = editors();
  assert.equal(found.length, 1, "one caption editor is open");
  return found[0];
}

const field = () => editorNode().querySelector(".field-input");

// The one button under `root` whose text is `text`.
function buttonIn(root, text) {
  const found = root.querySelectorAll("button").filter((node) => node.textContent === text);
  assert.equal(found.length, 1, `one button ${text}`);
  return found[0];
}

// A press of `node` with the mouse: the focus goes to it, then it is clicked.
async function press(node) {
  node.focus();
  node.click();
  await settle();
}

// Typing `text` over the text of `input`: the field holds it, with the caret at its end.
function type(input, text) {
  input.focus();
  input.value = text;
  input.dispatchEvent(new FakeEvent("input"));
}

// The time of one poll goes by: the page asks the state, and loads the data again if the version moved on.
async function poll() {
  await browser.clock.advance(POLL_MS);
}

async function openEditorFromMenu(id) {
  await press(row(id).querySelector(".more"));
  await press(buttonIn($("pop"), "改字幕"));
}

async function openEditorFromTypoCard(id) {
  await press(row(id).querySelector("[data-typo]"));
  await press(buttonIn($("pop"), "改字幕"));
}

// The texts the notice showed after the first `from` (a count of notice().texts).
const noticesSince = (from) => notice().texts.slice(from);

// That `actual` is the element `expected` (see FakeElement.toString for why not assert.equal).
function assertSame(actual, expected, what) {
  assert.ok(actual === expected, `${what} is ${actual}, not ${expected}`);
}

function assertShowsError(text) {
  assert.equal(notice().textContent, text);
  assert.equal(notice().hidden, false);
  assert.equal(notice().classList.contains("error"), true);
}

// ---- the confirm dialog -----------------------------------------------------------------------------------------

describe("the confirm dialog approves the version it showed", () => {
  test("同意 opens it; a poll of the same version leaves it open, and 確定 approves the version it showed", async () => {
    const { server } = await openPage();
    await press($("approve"));
    assert.equal($("confirm").open, true);
    assert.equal($("confirm-body").textContent, "新長 0:10.0，剪掉 0 段。0:05.0 那一句的字幕照辨識的字出，錯字會留在成品裡。");
    await poll();
    assert.equal($("confirm").open, true);
    await press($("confirm-go"));
    assert.equal($("confirm").open, false);
    assert.deepEqual(server.sent("POST api/approve"), [{ version: "v1" }]);
  });

  test("a load of another version closes it and approves nothing; opened again, it approves the new version", async () => {
    const { server } = await openPage();
    await press($("approve"));
    server.claude(changesCaption("s02", "第一是把亮度調暗。"));
    await poll();
    assert.equal($("confirm").open, false);
    assert.equal(notice().textContent, UPDATED_TEXT);
    assert.equal(notice().hidden, false);
    await press($("confirm-go")); // the version the closed dialog showed went with it: there is nothing to approve
    assert.deepEqual(server.sent("POST api/approve"), []);
    await press($("approve"));
    await press($("confirm-go"));
    assert.deepEqual(server.sent("POST api/approve"), [{ version: "v2" }]);
  });
});

// ---- 同意 and 儲存 with the caption editor -------------------------------------------------------------------------

describe("同意 and 儲存 with the caption editor", () => {
  test("同意 while the editor holds text to save says so, keeps the editor with the focus, and opens no dialog", async () => {
    const { server } = await openPage();
    await openEditorFromMenu("s02");
    const node = editorNode();
    type(field(), "第一是把亮度調暗。");
    await press($("approve"));
    assertShowsError(UNSAVED_CAPTION_TEXT);
    assert.equal($("confirm").open, false);
    assertSame(editorNode(), node, "the open editor");
    assert.equal(field().value, "第一是把亮度調暗。");
    assertSame(focused(), field(), "the focus");
    assert.deepEqual(server.sent("POST api/approve"), []);
  });

  test("同意 with an editor that holds nothing to save closes it and opens the dialog", async () => {
    await openPage();
    await openEditorFromMenu("s02");
    await press($("approve"));
    assert.equal(editors().length, 0);
    assert.equal(row("s02").hidden, false);
    assert.equal($("confirm").open, true);
    assert.deepEqual(noticesSince(0), []);
  });

  test("儲存 sends the caption with the version shown; saved, the editor closes and the notice that it was not goes", async () => {
    const { server } = await openPage();
    await openEditorFromMenu("s02");
    type(field(), "第一是把亮度調暗。");
    await press($("approve"));
    assertShowsError(UNSAVED_CAPTION_TEXT);
    await press(buttonIn(editorNode(), "儲存"));
    assert.deepEqual(server.sent("POST api/change"), [
      { version: "v1", change: { type: "caption", sentence: "s02", text: "第一是把亮度調暗。" } },
    ]);
    assert.equal(editors().length, 0);
    assert.equal(row("s02").hidden, false);
    assert.ok(buttonIn(row("s02"), "已改字幕　復原"));
    assert.equal(notice().hidden, true);
  });

  test("a 儲存 the server refuses keeps the editor with its text, and the notice says why", async () => {
    const server = new FakeServer();
    server.change = () => [422, { error: REFUSED_TEXT }];
    await openPage(server);
    await openEditorFromMenu("s02");
    const node = editorNode();
    type(field(), "第一是把亮度調暗。");
    await press(buttonIn(node, "儲存"));
    assert.equal(server.sent("POST api/change").length, 1);
    assertSame(editorNode(), node, "the open editor");
    assert.equal(field().value, "第一是把亮度調暗。");
    assertShowsError(REFUSED_TEXT);
  });
});

// ---- an editor the page closes on its own -----------------------------------------------------------------------

describe("an editor the page closes on its own says so when it drops typed text", () => {
  for (const [what, change] of [["removes", removes("s02")], ["transcribes again", transcribesAgain("s02")]]) {
    test(`a poll after Claude ${what} its sentence drops typed text with CAPTION_DROPPED_TEXT, not UPDATED_TEXT`, async () => {
      const { server } = await openPage();
      await openEditorFromMenu("s02");
      type(field(), "第一是把亮度調暗。");
      const from = notice().texts.length;
      server.claude(change);
      await poll();
      assert.deepEqual(noticesSince(from), [CAPTION_DROPPED_TEXT]);
      assertShowsError(CAPTION_DROPPED_TEXT);
      assert.equal(editors().length, 0);
      assert.equal(row("s02").hidden, false);
    });
  }

  test("with nothing typed, the same poll closes the editor and says only that Claude changed the edit", async () => {
    const { server } = await openPage();
    await openEditorFromMenu("s02");
    server.claude(removes("s02"));
    await poll();
    assert.deepEqual(noticesSince(0), [UPDATED_TEXT]);
    assert.equal(notice().classList.contains("error"), false);
    assert.equal(editors().length, 0);
    assert.equal(row("s02").hidden, false);
  });

  for (const state of ["approved", "working", "done"]) {
    test(`a poll that finds the state ${state} drops typed text with CAPTION_DROPPED_TEXT`, async () => {
      const { server } = await openPage();
      await openEditorFromMenu("s02");
      type(field(), "第一是把亮度調暗。");
      server.status = statusOf(state, state === "working" ? "render" : null);
      await poll();
      assert.deepEqual(noticesSince(0), [CAPTION_DROPPED_TEXT]);
      assertShowsError(CAPTION_DROPPED_TEXT);
      assert.equal(editors().length, 0);
      assert.equal($("review").hidden, true);
    });

    test(`a poll that finds the state ${state} closes an editor with nothing typed without a notice`, async () => {
      const { server } = await openPage();
      await openEditorFromMenu("s02");
      server.status = statusOf(state, state === "working" ? "render" : null);
      await poll();
      assert.deepEqual(noticesSince(0), []);
      assert.equal(notice().hidden, true);
      assert.equal(editors().length, 0);
    });
  }

  test("a change whose reload finds the editor's sentence removed by Claude says CAPTION_DROPPED_TEXT, not UPDATED_TEXT", async () => {
    const server = new FakeServer();
    server.change = (body) => {
      const answer = server.applyChange(body.change);
      server.claude(removes("s02")); // after the change, before the page loads the data again
      return answer;
    };
    await openPage(server);
    await openEditorFromMenu("s02");
    type(field(), "第一是把亮度調暗。");
    await press(row("s04").querySelector(".more"));
    await press(buttonIn($("pop"), "拿掉這一句"));
    assert.deepEqual(server.sent("POST api/change"), [{ version: "v1", change: { type: "remove", sentence: "s04" } }]);
    assert.deepEqual(noticesSince(0), [CAPTION_DROPPED_TEXT]);
    assertShowsError(CAPTION_DROPPED_TEXT);
    assert.equal(editors().length, 0);
  });

  test("a change refused as a conflict whose reload drops typed text says CAPTION_DROPPED_TEXT, not the conflict", async () => {
    const server = new FakeServer();
    server.change = () => {
      server.claude(removes("s02"));
      return [409, { error: CONFLICT_TEXT, conflict: true }];
    };
    await openPage(server);
    await openEditorFromMenu("s02");
    type(field(), "第一是把亮度調暗。");
    await press(row("s04").querySelector(".more"));
    await press(buttonIn($("pop"), "拿掉這一句"));
    assert.equal(server.sent("POST api/change").length, 1);
    assert.deepEqual(noticesSince(0), [CAPTION_DROPPED_TEXT]);
    assertShowsError(CAPTION_DROPPED_TEXT);
    assert.equal(editors().length, 0);
  });

  test("the user's own 儲存 and 取消 never say CAPTION_DROPPED_TEXT", async () => {
    const { server } = await openPage();
    await openEditorFromMenu("s02");
    type(field(), "第一是把亮度調暗。");
    await press(buttonIn(editorNode(), "儲存"));
    assert.equal(server.sent("POST api/change").length, 1);
    assert.equal(editors().length, 0);
    await openEditorFromMenu("s04");
    type(field(), "最後記得要存檔。");
    await press(buttonIn(editorNode(), "取消"));
    assert.equal(editors().length, 0);
    assert.equal(server.sent("POST api/change").length, 1);
    assert.equal(notice().texts.includes(CAPTION_DROPPED_TEXT), false);
  });
});

// ---- the editor of another sentence -----------------------------------------------------------------------------

describe("an open caption editor stays when asking for an editor would drop its typed text", () => {
  const ways = [["the ⋯ menu", openEditorFromMenu, "s02"], ["the typo card", openEditorFromTypoCard, "s03"]];
  for (const [how, openOther, other] of ways) {
    test(`asked from ${how}, the other editor does not open, and the open one keeps its text and the focus`, async () => {
      const { server } = await openPage();
      await openEditorFromMenu("s01");
      const node = editorNode();
      const input = field();
      type(input, "今天來聊三個好技巧。");
      const sent = server.requests.length;
      await openOther(other);
      assert.deepEqual(noticesSince(0), [UNSAVED_OPEN_TEXT]);
      assertShowsError(UNSAVED_OPEN_TEXT);
      assertSame(editorNode(), node, "the open editor");
      assert.equal(input.value, "今天來聊三個好技巧。");
      assertSame(focused(), input, "the focus");
      assert.equal(row(other).hidden, false);
      assert.equal($("pop").hidden, true);
      assert.equal(server.requests.length, sent);
    });
  }

  test("an open editor with nothing to save closes, and the other one opens, without a notice", async () => {
    await openPage();
    await openEditorFromMenu("s01");
    await openEditorFromMenu("s02");
    assert.equal(field().value, "第一是把亮度調低。");
    assert.equal(row("s01").hidden, false);
    assert.equal(row("s02").hidden, true);
    assert.deepEqual(noticesSince(0), []);
  });

  test("the editor of the same sentence asked for again stays as it is, with its typed text, selection and focus, and no notice", async () => {
    const { server } = await openPage();
    await openEditorFromTypoCard("s03");
    const old = editorNode();
    const input = field();
    type(input, "第二是把對比調高。");
    input.setSelectionRange(3, 5);
    const asked = server.sent("POST api/caption-lines").length;
    await press($("asr")); // the navigation goes to the sentence of the typo, and opens its card
    await press(buttonIn($("pop"), "改字幕"));
    assertSame(editorNode(), old, "the open editor"); // no new editor opened, and the old one was not closed
    assert.equal(old.isConnected, true);
    assert.equal(row("s03").hidden, true);
    assertSame(field(), input, "the field");
    assert.equal(input.value, "第二是把對比調高。");
    assert.deepEqual([input.selectionStart, input.selectionEnd], [3, 5]);
    assertSame(focused(), input, "the focus");
    assert.equal($("pop").hidden, true);
    assert.deepEqual(noticesSince(0), []);
    assert.equal(server.sent("POST api/caption-lines").length, asked); // no new editor asked for lines
    await press(buttonIn(editorNode(), "儲存")); // the kept text is what 儲存 sends
    assert.deepEqual(server.sent("POST api/change"), [
      { version: "v1", change: { type: "caption", sentence: "s03", text: "第二是把對比調高。" } },
    ]);
  });

  test("the editor of the same sentence asked for again with nothing typed stays too, and takes the focus back", async () => {
    await openPage();
    await openEditorFromTypoCard("s03");
    const old = editorNode();
    $("slow").focus();
    await openEditorFromTypoCard("s03");
    assertSame(editorNode(), old, "the open editor");
    assert.equal(field().value, "第二是把對筆調高。");
    assertSame(focused(), field(), "the focus");
    assert.deepEqual(noticesSince(0), []);
  });

  test("after the notice, 儲存 of the open editor saves it and takes the notice away", async () => {
    const { server } = await openPage();
    await openEditorFromMenu("s01");
    type(field(), "今天來聊三個好技巧。");
    await openEditorFromMenu("s02");
    assertShowsError(UNSAVED_OPEN_TEXT);
    await press(buttonIn(editorNode(), "儲存"));
    assert.deepEqual(server.sent("POST api/change"), [
      { version: "v1", change: { type: "caption", sentence: "s01", text: "今天來聊三個好技巧。" } },
    ]);
    assert.equal(editors().length, 0);
    assert.equal(notice().hidden, true);
  });
});

// ---- the caption Claude changed under an open editor ------------------------------------------------------------

describe("an open editor whose sentence's caption Claude changed", () => {
  const NEW = "今天來聊三個小撇步。";

  // The editor of s01 is open with nothing typed in its field, which has the focus and the caret at `caret`; Claude
  // changes the caption of s01, and a poll loads it.
  async function followed({ caret = [2, 2], focusElsewhere = false } = {}) {
    const opened = await openPage();
    await openEditorFromMenu("s01");
    field().setSelectionRange(...caret);
    assertSame(focused(), field(), "the focus");
    if (focusElsewhere) $("slow").focus();
    opened.server.claude(changesCaption("s01", NEW));
    await poll();
    return opened;
  }

  test("a field nothing was typed in takes the new caption with its marks and the caret at its end, and asks its lines", async () => {
    const { server } = await followed();
    const input = field();
    assert.equal(input.value, NEW);
    const marks = editorNode().querySelector(".field-marks");
    assert.equal(marks.textContent, NEW);
    assert.deepEqual(marks.querySelectorAll("mark").map((mark) => mark.textContent), ["撇步"]);
    assert.deepEqual([input.selectionStart, input.selectionEnd], [NEW.length, NEW.length]);
    assertSame(focused(), input, "the focus");
    assert.deepEqual(server.sent("POST api/caption-lines").at(-1), { sentence: "s01", text: NEW });
    assert.deepEqual(noticesSince(0), [UPDATED_TEXT]);
  });

  test("a field without the focus takes the new caption but not the focus", async () => {
    await followed({ focusElsewhere: true });
    assert.equal(field().value, NEW);
    assertSame(focused(), $("slow"), "the focus");
  });

  test("then 儲存 sends nothing, and the editor closes", async () => {
    const { server } = await followed();
    await press(buttonIn(editorNode(), "儲存"));
    assert.deepEqual(server.sent("POST api/change"), []);
    assert.equal(editors().length, 0);
  });

  test("then a poll that finds the state approved closes the editor without CAPTION_DROPPED_TEXT", async () => {
    const { server } = await followed();
    server.status = statusOf("approved");
    await poll();
    assert.equal(editors().length, 0);
    assert.equal(notice().texts.includes(CAPTION_DROPPED_TEXT), false);
  });

  test("then the editor of another sentence opens", async () => {
    await followed();
    await openEditorFromMenu("s02");
    assert.equal(field().value, "第一是把亮度調低。");
    assert.equal(row("s01").hidden, false);
    assert.equal(notice().texts.includes(UNSAVED_OPEN_TEXT), false);
  });

  test("a field the user typed in keeps its text, the focus and the selection, and still holds text to save", async () => {
    const { server, page } = await openPage();
    await openEditorFromMenu("s01");
    const input = field();
    type(input, "今天來聊三個好技巧。");
    input.setSelectionRange(3, 5);
    server.claude(changesCaption("s01", NEW));
    await poll();
    assertSame(field(), input, "the field");
    assert.equal(input.value, "今天來聊三個好技巧。");
    assert.deepEqual([input.selectionStart, input.selectionEnd], [3, 5]);
    assertSame(focused(), input, "the focus");
    assert.equal(page.editor.saved, NEW); // what the field is compared with now: the caption Claude saved
    await press($("approve"));
    assertShowsError(UNSAVED_CAPTION_TEXT);
    assert.equal($("confirm").open, false);
    await press(buttonIn(editorNode(), "儲存"));
    assert.deepEqual(server.sent("POST api/change"), [
      { version: "v2", change: { type: "caption", sentence: "s01", text: "今天來聊三個好技巧。" } },
    ]);
  });
});

// ---- the running clock ------------------------------------------------------------------------------------------

describe("the player's clock never reads past the length it is shown with", () => {
  test("at the end of a reel whose unrounded length sits at an x.x5 edge, the running part is the length string too", async () => {
    const server = new FakeServer();
    // 12.3496 s unrounded: the server writes 0:12.3, but its length rounded to the millisecond (12.35) is 0:12.4 to clock
    Object.assign(server.data, { source_s: 13, source_clock: "0:13.0", duration_s: 12.35, duration_clock: "0:12.3", segments: [[0, 12.35]] });
    await openPage(server);
    const video = $("video");
    video.currentTime = 12.34;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal($("clock").textContent, "0:12.3 / 0:12.3");
    video.currentTime = 12.35;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal($("clock").textContent, "0:12.3 / 0:12.3");
    video.currentTime = 5;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal($("clock").textContent, "0:05.0 / 0:12.3");
  });
});

// ---- the punch-in ---------------------------------------------------------------------------------------------

describe("the player shows the punch-in of the shot that plays", () => {
  test("a punched-in shot scales the video about its anchor; a shot at the base framing leaves it as it is", async () => {
    const server = new FakeServer();
    Object.assign(server.data, {
      segments: [[0, 4], [5, 10]], shots: [[0, 4, 1, 0.5, 0.5], [5, 10, 1.1, 0.2, 0.43]],
      duration_s: 9, duration_clock: "0:09.0",
    });
    await openPage(server);
    const video = $("video");
    video.currentTime = 6;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(video.style.transform, "scale(1.1)");
    assert.equal(video.style.transformOrigin, "20% 43%");
    video.currentTime = 1;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(video.style.transform, "");
    assert.equal(video.style.transformOrigin, "");
  });

  test("past the end of the reel the video keeps the framing of the last shot", async () => {
    const server = new FakeServer();
    Object.assign(server.data, {
      segments: [[0, 4], [5, 10]], shots: [[0, 4, 1, 0.5, 0.5], [5, 10, 1.1, 0.2, 0.43]],
      duration_s: 9, duration_clock: "0:09.0",
    });
    await openPage(server);
    const video = $("video");
    video.currentTime = 1;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(video.style.transform, "");
    video.currentTime = 11;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(video.style.transform, "scale(1.1)");
    assert.equal(video.style.transformOrigin, "20% 43%");
  });

  test("a reload that changes the shots frames the paused video anew", async () => {
    const server = new FakeServer();
    Object.assign(server.data, {
      segments: [[0, 4], [5, 10]], shots: [[0, 4, 1, 0.5, 0.5], [5, 10, 1.1, 0.2, 0.43]],
      duration_s: 9, duration_clock: "0:09.0",
    });
    await openPage(server);
    const video = $("video");
    video.currentTime = 6;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(video.style.transform, "scale(1.1)");
    server.claude((s) => { s.data.shots = [[0, 4, 1.1, 0.5, 0.5], [5, 10, 1, 0.2, 0.43]]; });
    await poll();
    assert.equal(notice().textContent, UPDATED_TEXT);
    assert.equal(video.style.transform, "");
    assert.equal(video.style.transformOrigin, "");
  });
});

// ---- the animation cards --------------------------------------------------------------------------------------

describe("the player shows the label of the animation that plays", () => {
  const LABEL = "動畫：數字卡　兩小時／一天多撐";

  test("the label is over the video while its scene plays, and gone before and after", async () => {
    const server = new FakeServer();
    server.data.scenes = [{ start: 2.5, end: 4.5, label: LABEL }];
    await openPage(server);
    const video = $("video");
    const label = $("scene");
    video.currentTime = 1;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(label.hidden, true);
    video.currentTime = 3;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(label.hidden, false);
    assert.equal(label.textContent, LABEL);
    video.currentTime = 4.5;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal(label.hidden, true);
    assert.equal(label.textContent, "");
  });

  test("a reload that takes the scene away hides its label on the paused video", async () => {
    const server = new FakeServer();
    server.data.scenes = [{ start: 2.5, end: 4.5, label: LABEL }];
    await openPage(server);
    const video = $("video");
    video.currentTime = 3;
    video.dispatchEvent(new FakeEvent("timeupdate"));
    assert.equal($("scene").hidden, false);
    server.claude((s) => { s.data.scenes = []; });
    await poll();
    assert.equal(notice().textContent, UPDATED_TEXT);
    assert.equal($("scene").hidden, true);
  });
});

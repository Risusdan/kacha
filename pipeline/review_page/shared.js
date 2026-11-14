// What the modules of the review page share: the page's state (`page`), the helpers that build elements, the requests
// to the review server (pipeline/review_server.py), the notice, and the questions about the state that both review.js
// (the views, the player, the changes and the start) and editor.js (the caption editor) ask. This module imports
// nothing, and it only declares: what it does with `page` and the DOM it does when a function is called, never when
// the module is loaded, so importing it starts nothing. The one thing a request does that is not here, what the page
// does when the server is gone, is review.js's, which hands it over with onServerGone before the page starts.

const NOTICE_MS = 4000;
const ERROR_MS = 10000; // an error stays this long

// The text of review_server.py, word for word: the page shows it itself when the server answers with something the
// page cannot read, and as the text of a refusal that has none.
export const SERVER_ERROR_TEXT = "審閱頁的程式出了問題，請回對話請 Claude 看看。";
export const EDITING = ["editing", "failed"]; // the states in which the page changes the edit

export const $ = (id) => document.getElementById(id);

export const page = {
  data: null, // the last GET api/data
  trims: new Map(), // candidate id -> its row of data.trims
  sentences: new Map(), // sentence id -> its row of data.sentences
  state: null, // the state of the last GET api/state: editing, failed, working, approved or done
  step: null,
  outputs: null,
  failedText: null,
  waitText: null, // why the data could not be loaded (Claude is changing the edit)
  needsLoad: false, // the last load failed: load again on the next poll
  loadSeq: 0, // the number of the last load; an answer to an older one is dropped
  ownVersion: null, // the version this page's last change made, until a poll has seen it or another one loads
  confirmVersion: null, // the version the open confirm dialog was built from: the one the approval is for
  changes: 0, // goes up when a change or the approval starts and when it lands: a poll that saw it go up is outdated
  busy: false, // a change or the approval is on its way
  previewSet: false, // the done view's video has its source
  stopped: false, // the server is gone: no more requests
  seen: new Set(), // the items whose card was opened
  seenLoaded: false,
  at: { decision: -1, listen: -1, asr: -1 }, // where the navigation is in each list
  pop: null, // the open popover: {kind: "trim" | "typo" | "menu", id}, or {kind: "listen", id, which, start}
  editor: null, // the open caption editor (see openEditor in editor.js)
  numbered: new Set(), // the decisions whose number the transcript shows already (while it is built)
  stopAtReel: null, // a clip plays until this reel time
  shown: { caption: null, lit: -1 }, // what the caption box shows
  nowSentence: null, // the sentence lit as playing
  drops: 0, // how many times the page closed a caption editor that held text to save (see notifyUnlessDropped)
  pollTimer: null,
  noticeTimer: null,
};

export class Stopped extends Error {} // the server did not answer, or refused the key: the banner says so

// ---- building elements ------------------------------------------------------------------------

// An element with `props` (class, data, style, disabled, hidden, or any attribute) and `children` (nodes or text).
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "data") {
      for (const [name, item] of Object.entries(value)) if (item !== null && item !== undefined) node.dataset[name] = item;
    } else if (key === "style") Object.assign(node.style, value);
    else if (key === "disabled" || key === "hidden") node[key] = Boolean(value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export function button(text, props, onClick) {
  const node = el("button", { type: "button", class: "btn", ...props }, text);
  node.addEventListener("click", onClick);
  return node;
}

// ---- requests ---------------------------------------------------------------------------------

// What happens when the server is gone (the banner, the views, polling): review.js says, through onServerGone. Until
// it has, a request that finds the server gone throws, as an editor hook that was not connected does.
let serverGone = () => {
  throw new Error("onServerGone was not called");
};

export function onServerGone(handler) {
  serverGone = handler;
}

// {status, body} of a request to the server. A request that gets no answer, or 403 (the key is gone), means the
// server is gone: the banner says so, polling stops, and Stopped is thrown. A body that is not JSON is {}.
export async function request(method, url, body) {
  if (page.stopped) throw new Stopped();
  const init = { method, cache: "no-store" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    serverGone();
    throw new Stopped();
  }
  if (response.status === 403) {
    serverGone();
    throw new Stopped();
  }
  let answer = {};
  if (response.status !== 204) {
    try {
      answer = await response.json();
    } catch (error) {
      answer = {};
    }
  }
  return { status: response.status, body: answer !== null && typeof answer === "object" ? answer : {} };
}

// The Chinese text of a refusal: the server's "error", or SERVER_ERROR_TEXT when there is none.
export function errorText(answer) {
  const text = answer.body.error;
  return typeof text === "string" && text !== "" ? text : SERVER_ERROR_TEXT;
}

// ---- the notice -------------------------------------------------------------------------------

export function notify(text, isError = false) {
  const notice = $("notice");
  notice.textContent = text;
  notice.classList.toggle("error", isError);
  notice.hidden = false;
  clearTimeout(page.noticeTimer);
  page.noticeTimer = setTimeout(() => {
    notice.hidden = true;
  }, isError ? ERROR_MS : NOTICE_MS);
}

// Hide the notice now if it shows `text`, which no longer holds; any other notice stays until its own time.
export function hideNotice(text) {
  const notice = $("notice");
  if (notice.hidden || notice.textContent !== text) return;
  clearTimeout(page.noticeTimer);
  notice.hidden = true;
}

// ---- what the state allows, and the transcript's rows -----------------------------------------

export function editable() {
  return !page.stopped && page.data !== null && EDITING.includes(page.state);
}

// Whether a button that changes something works now: the edit can be changed and nothing is on its way.
export function canChange() {
  return editable() && !page.busy;
}

// The row of sentence `id` in the transcript.
export function rowOf(id) {
  return $("sentences").querySelector(`.sentence[data-sentence="${CSS.escape(id)}"]`);
}

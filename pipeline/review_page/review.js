// The review page: the views, the player and the requests to the review server (pipeline/review_server.py), and the
// start of it all. The script is four modules, which index.html does not load one by one: this one imports the
// others, and a module that is imported never imports this one. logic.js holds the pure functions (times on the
// source and on the reel, the caption on screen), shared.js the page's state, the requests and the notice, and
// editor.js the caption editor. Only this module binds the page to the DOM and starts it (start, the last thing it
// does), so the others can be imported with no page.
//
// The page loads GET api/data (review_data.page_data: segments and the transcript in source seconds, captions on the
// reel, and the state with its step, error text and outputs, which the first view shows) and asks GET api/state
// every POLL_MS. It plays the review video, which is the whole source, and skips what the edit cuts: on every frame
// (requestVideoFrameCallback, on the time of the frame on screen, a frame ahead) and on every timeupdate it asks
// skipTarget where to go, and scales the video as the render frames the shot that plays (shotTransform: the punch-in;
// the captions are drawn over it unscaled). Every change goes to POST api/change with the version of the edit the page shows; the page
// then loads the data again. A version the page did not cause (Claude changed the edit from the chat) loads the data
// again with a notice: pollVersion decides. While a change or the approval is on its way, the buttons that change
// something are disabled and no poll goes out. Once the state is working, approved or done, the page shows that
// state and offers no change. The caption editor (editor.js) changes a sentence's caption text: it asks POST
// api/caption-lines for the lines as the user types and saves through sendChange, below.
//
// Everything the page shows is Chinese and comes either from the page's modules or from the server's data and
// answers; ids travel only in data attributes and in the bodies of requests. URLs are relative: the server answers
// on one port.

import {
  captionAt,
  clock,
  leadIn,
  litPieces,
  nearestUnit,
  pageState,
  placePopover,
  playerClock,
  pollVersion,
  reelTime,
  sceneAt,
  seekPoint,
  sentenceAt,
  shotTransform,
  skipTarget,
  sourceTime,
} from "./logic.js";
import {
  $,
  EDITING,
  SERVER_ERROR_TEXT,
  Stopped,
  button,
  canChange,
  editable,
  el,
  errorText,
  notify,
  onServerGone,
  page,
  request,
  rowOf,
} from "./shared.js";
import {
  UNSAVED_CAPTION_TEXT,
  closeEditor,
  connectEditor,
  dropEditor,
  editorLine,
  notifyUnlessDropped,
  openEditor,
  placeEditor,
  unsavedText,
} from "./editor.js";

const POLL_MS = 3000;
const FRAME_S = 1 / 30; // the lookahead of the skip on a frame: one frame of the review video
const LEAD_S = 1; // going to an item leaves the video this many reel seconds before it
const AROUND_S = 1; // ▶ 播這一段前後 plays this many reel seconds before and after the item
const SLOW_RATE = 0.5;
const SEEN_KEY = "review-seen:"; // + the project: the items opened in this tab (sessionStorage)

// The texts of review_server.py, word for word (SERVER_ERROR_TEXT is in shared.js): the page shows them itself when
// the server cannot answer.
const DENIED_TEXT = "審閱頁的程式已經停了，請回對話請 Claude 重新打開。";
const UPDATED_TEXT = "Claude 改了剪接設定，頁面已更新。";
const SUGGESTED = "（建議）";
const PLAY_TEXT = "▶ 播放";
const PAUSE_TEXT = "❚❚ 暫停";
const STEPS = ["storyboard", "approve", "render"]; // the steps of the approval job, in order
const STEP_TEXTS = { storyboard: "正在重做分鏡稿", approve: "正在記下同意", render: "正在輸出成品" };

const video = $("video");
const player = $("player");
const captionBox = $("caption");
const track = $("track");
const playhead = $("playhead");
const script = $("script");
const sentencesBox = $("sentences");
const pop = $("pop");
const confirmDialog = $("confirm");

// ---- the server and its data ------------------------------------------------------------------

function serverGone() {
  if (page.stopped) return;
  page.stopped = true;
  clearTimeout(page.pollTimer);
  closePop();
  if (confirmDialog.open) confirmDialog.close();
  renderBanner();
  renderView();
  if (page.data) renderTranscript();
}

// Load the data again and show it. False when the server did not give it (Claude is changing the edit: the banner
// says so, and the next poll tries again) or when a later load has started meanwhile.
async function loadData() {
  const seq = ++page.loadSeq;
  const answer = await request("GET", "api/data");
  if (seq !== page.loadSeq) return false;
  if (answer.status !== 200) {
    page.needsLoad = true;
    page.waitText = errorText(answer);
    renderBanner();
    return false;
  }
  page.needsLoad = false;
  page.waitText = null;
  page.data = answer.body;
  // a version this page did not make: a later write of the same bytes (by Claude) is not the page's own either
  if (page.data.version !== page.ownVersion) page.ownVersion = null;
  // the confirm dialog shows another version than this one: close it (the notice would sit under its backdrop)
  if (confirmDialog.open && page.data.version !== page.confirmVersion) {
    confirmDialog.close();
    page.confirmVersion = null;
  }
  // the first view follows the state that came with the data, with its step, error text and outputs, as applyStatus
  // takes them from a poll; after that the polls say it
  if (page.state === null && typeof page.data.state === "string") Object.assign(page, pageState(page.data));
  page.trims = new Map(page.data.trims.map((trim) => [trim.id, trim]));
  page.sentences = new Map(page.data.sentences.map((sentence) => [sentence.id, sentence]));
  if (!page.seenLoaded) loadSeen();
  renderData();
  renderBanner();
  renderView();
  return true;
}

// ---- what was seen: sessionStorage, which may be missing or refuse ----------------------------

function loadSeen() {
  page.seenLoaded = true;
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(SEEN_KEY + page.data.project) ?? "[]");
    if (Array.isArray(saved)) for (const id of saved) if (typeof id === "string") page.seen.add(id);
  } catch (error) {
    // nothing was seen yet as far as this page can tell
  }
}

function markSeen(id) {
  if (page.seen.has(id)) return;
  page.seen.add(id);
  try {
    window.sessionStorage.setItem(SEEN_KEY + page.data.project, JSON.stringify([...page.seen]));
  } catch (error) {
    // the page keeps it until it is closed
  }
  renderNav();
}

// ---- the banner -------------------------------------------------------------------------------

function renderBanner() {
  const banner = $("banner");
  let text = null;
  if (page.stopped) text = DENIED_TEXT;
  else if (page.waitText) text = page.waitText;
  else if (page.state === "failed") text = page.failedText || SERVER_ERROR_TEXT;
  banner.textContent = text ?? "";
  banner.hidden = text === null;
}

// ---- the views --------------------------------------------------------------------------------

// The buttons that change something (data-change, the 同意 button and the confirm dialog's) follow canChange.
function refreshControls() {
  const off = !canChange();
  for (const node of [...sentencesBox.querySelectorAll("[data-change]"), ...pop.querySelectorAll("[data-change]")]) {
    node.disabled = off;
  }
  $("approve").disabled = off;
  $("confirm-go").disabled = off;
}

function setBusy(on) {
  page.busy = on;
  document.body.classList.toggle("busy", on);
  refreshControls();
}

function renderView() {
  const editing = page.data !== null && EDITING.includes(page.state);
  $("review").hidden = !editing;
  $("nav").hidden = !editing;
  $("foot").hidden = !editing;
  $("approve").hidden = !editable();
  $("working").hidden = page.state !== "working";
  $("approved").hidden = page.state !== "approved";
  $("done").hidden = page.state !== "done";
  if (!editing) {
    closePop();
    dropEditor();
    if (confirmDialog.open) confirmDialog.close();
    video.pause();
  }
  if (page.state === "working") renderWorking();
  if (page.state === "done") renderDone();
  refreshControls();
}

function renderWorking() {
  const index = Math.max(0, STEPS.indexOf(page.step));
  $("working-step").textContent = `${STEP_TEXTS[STEPS[index]]}…`;
  $("working-count").textContent = `第 ${index + 1} 步，共 ${STEPS.length} 步。這時不能改決定。`;
}

// The done view: the preview plays, both paths show, and 打開所在的資料夾 asks the server to show the reel. The
// preview gets its source once, the first time the outputs name it.
function renderDone() {
  const outputs = page.outputs ?? {};
  $("final-path").textContent = outputs.final ?? "";
  $("preview-path").textContent = outputs.preview ?? "";
  $("preview-line").hidden = !outputs.preview;
  const preview = $("preview");
  preview.hidden = !outputs.preview;
  if (outputs.preview && !page.previewSet) {
    page.previewSet = true;
    preview.src = "media/preview.mp4";
    preview.load();
  }
}

async function reveal() {
  try {
    const answer = await request("POST", "api/reveal", {}); // JSON, as the server asks of every POST
    if (answer.status !== 204) notify(errorText(answer), true);
  } catch (error) {
    if (!(error instanceof Stopped)) notify(SERVER_ERROR_TEXT, true);
  }
}

// ---- the top bar ------------------------------------------------------------------------------

function renderTop() {
  const data = page.data;
  $("project").textContent = data.project;
  $("lengths").textContent = `原長 ${data.source_clock} → 新長 ${data.duration_clock}`;
  $("cuts").textContent = `剪掉 ${data.cuts} 段`;
}

// ---- the navigation bar -----------------------------------------------------------------------

function renderNav() {
  if (!page.data) return;
  const nav = page.data.nav;
  const unseen = nav.decisions.filter((id) => !page.seen.has(id)).length;
  $("decisions").textContent = `要你決定 ${nav.decisions.length} 項（還沒看 ${unseen} 項）`;
  $("prev").disabled = nav.decisions.length === 0;
  $("next").disabled = nav.decisions.length === 0;
  $("listen").textContent = `建議聽一下 ${nav.listen.length} 處`;
  $("listen").hidden = nav.listen.length === 0;
  $("asr").textContent = `字幕可能有錯字 ${nav.asr.length} 句`;
  $("asr").hidden = nav.asr.length === 0;
}

// The next item of a list, one `by` further (from the first or the last when none was visited yet), in a ring.
function stepIn(list, which, by) {
  if (list.length === 0) return null;
  const at = page.at[which];
  const next = at < 0 || at >= list.length ? (by > 0 ? 0 : list.length - 1) : (at + by + list.length) % list.length;
  page.at[which] = next;
  return list[next];
}

function goToDecision(by) {
  const id = stepIn(page.data.nav.decisions, "decision", by);
  if (id !== null) goToTrim(id);
}

function goToListen() {
  const item = stepIn(page.data.nav.listen, "listen", 1);
  if (item === null) return;
  if (item.trim) {
    goToTrim(item.trim);
    return;
  }
  holdAt(leadIn(item.start, page.data.segments, LEAD_S));
  showInPanel(listenAnchor(page.data.nav.listen.indexOf(item)) ?? rowOf(item.sentence), true);
  openListen(item);
}

function goToTypo() {
  const id = stepIn(page.data.nav.asr, "asr", 1);
  if (id === null || !page.sentences.has(id)) return;
  holdAt(leadIn(page.sentences.get(id).start, page.data.segments, LEAD_S));
  showInPanel(rowOf(id), true);
  openTypo(id);
}

// Go to an item: the video waits a little before it, the transcript shows it, and its card opens.
function goToTrim(id) {
  const trim = page.trims.get(id);
  if (!trim) return;
  holdAt(leadIn(trim.start, page.data.segments, LEAD_S));
  showInPanel(trimAnchor(id), true);
  openTrim(id);
}

// Scroll the transcript panel (never the window) so `node` shows: in the middle when `centre`, else just inside.
function showInPanel(node, centre) {
  if (!node) return;
  const box = script.getBoundingClientRect();
  const spot = node.getBoundingClientRect();
  const top = spot.top - box.top - script.clientTop + script.scrollTop; // in the coordinates of the panel's content
  const bottom = top + spot.height;
  if (centre) script.scrollTop = Math.max(0, top - (script.clientHeight - spot.height) / 2);
  else if (top < script.scrollTop) script.scrollTop = top;
  else if (bottom > script.scrollTop + script.clientHeight) script.scrollTop = bottom - script.clientHeight;
}

// ---- the player -------------------------------------------------------------------------------

function play() {
  const playing = video.play();
  if (playing) playing.catch(() => {});
}

// Put the video at source time `t`, moved into the reel when `t` is in a cut or past its end, and never on a
// segment's start itself (seekPoint: a hair past it, so the frame shown is the segment's first).
function seekTo(t) {
  const segments = page.data.segments;
  if (segments.length === 0) return;
  video.currentTime = seekPoint(t, segments);
}

function holdAt(t) {
  page.stopAtReel = null;
  video.pause();
  seekTo(t);
}

function playFrom(t) {
  page.stopAtReel = null;
  seekTo(t);
  play();
}

// Play from `pad` reel seconds before source time `start` to `pad` after source time `end`, then stop.
function playSpan(start, end, pad) {
  const segments = page.data.segments;
  seekTo(sourceTime(Math.max(0, reelTime(start, segments) - pad), segments));
  page.stopAtReel = reelTime(end, segments) + pad;
  play();
}

function togglePlay() {
  if (!page.data || page.data.segments.length === 0) return;
  if (!video.paused) {
    video.pause();
    return;
  }
  page.stopAtReel = null;
  // at the end of the reel (the frame skip pauses on its last frame), play from the start
  const segments = page.data.segments;
  if (skipTarget(video.currentTime, segments, FRAME_S) === -1) video.currentTime = seekPoint(segments[0][0], segments);
  play();
}

function toggleSlow() {
  const slow = video.playbackRate !== SLOW_RATE;
  video.playbackRate = slow ? SLOW_RATE : 1;
  $("slow").classList.toggle("on", slow);
  $("slow").setAttribute("aria-pressed", String(slow));
}

function renderPlay() {
  $("play").textContent = video.paused ? PLAY_TEXT : PAUSE_TEXT;
}

// Every frame (and every timeupdate, and after a seek): skip what is cut, stop at the end of the reel or of a clip,
// and show the clock, the playhead, the caption and the sentence that plays. `t` is the source time the skip
// decides on, `ahead` its lookahead (see skipTarget): on a frame, the time of the frame on screen and one frame.
function tick(t = video.currentTime, ahead = 0) {
  if (!page.data) return;
  const segments = page.data.segments;
  const target = skipTarget(t, segments, ahead);
  if (target === -1) {
    if (!video.paused) video.pause();
  } else if (target !== null && !video.seeking) {
    video.currentTime = target; // a hair past the next segment's start (see skipTarget)
  }
  if (page.stopAtReel !== null && reelTime(video.currentTime, segments) >= page.stopAtReel) {
    page.stopAtReel = null;
    video.pause();
  }
  // while it skips, the framing stays until a frame of the next shot shows; at the end it is the last shot's
  if (target === null || target === -1) showFraming(t);
  showTime();
}

// The punch-in of the shot of the frame at source time `t` (shotTransform), on the video alone.
function showFraming(t) {
  const framing = shotTransform(t, page.data.shots);
  video.style.transform = framing ? framing.transform : "";
  video.style.transformOrigin = framing ? framing.origin : "";
}

function watchFrames() {
  if (typeof video.requestVideoFrameCallback === "function") {
    const onFrame = (now, metadata) => {
      try {
        if (metadata && typeof metadata.mediaTime === "number") tick(metadata.mediaTime, FRAME_S);
        else tick();
      } finally {
        video.requestVideoFrameCallback(onFrame);
      }
    };
    video.requestVideoFrameCallback(onFrame);
  }
  for (const name of ["timeupdate", "seeked", "loadedmetadata"]) video.addEventListener(name, () => tick());
}

function showTime() {
  const data = page.data;
  const t = video.currentTime;
  const r = reelTime(t, data.segments);
  $("clock").textContent = `${playerClock(t, data.segments, data.duration_s, data.duration_clock)} / ${data.duration_clock}`;
  playhead.style.left = `${Math.min(100, (t / (data.source_s || 1)) * 100)}%`;
  showCaption(r);
  showScene(t);
  showSentence(t);
}

// The animation card that plays at source time `t` (sceneAt), over the video: the still of the card (the card at rest
// where the reel puts it, as big as the player and not zoomed with the punch-in, under the captions) when the server
// has one, else its label over the top. The still's address is set only when it changes, so the image loads once; the
// image shows only once that still has loaded (until then it would show the one before), and a still that does not
// load gives its scene the label instead.
let shownStill = null; // the still the image holds, or is loading
let loadedStill = null; // the still the image has finished loading
const brokenStills = new Set(); // stills that did not load
const stillImage = $("scene-still");
stillImage.addEventListener("load", () => {
  loadedStill = shownStill;
  showScene(video.currentTime);
});
stillImage.addEventListener("error", () => {
  if (shownStill !== null) brokenStills.add(shownStill);
  showScene(video.currentTime);
});
function showScene(t) {
  const scene = sceneAt(t, page.data.scenes);
  const wanted = scene?.still ?? null;
  const still = wanted !== null && !brokenStills.has(wanted) ? wanted : null;
  if (still !== null && still !== shownStill) {
    shownStill = still;
    loadedStill = null;
    stillImage.src = still;
  }
  stillImage.alt = scene ? scene.label : "";
  stillImage.hidden = still === null || loadedStill !== still;
  const label = $("scene");
  label.textContent = scene && still === null ? scene.label : "";
  label.hidden = !scene || still !== null;
}

// The caption on screen at reel time `r`, in word mode with the spoken pieces in the highlight colour; over the
// time of a sentence in the caption editor, the line of its new text (editorLine).
function showCaption(r) {
  const data = page.data;
  const line = editorLine(r);
  const caption = line === null ? captionAt(r, data.captions) : null;
  const lit = caption && data.mode === "word" ? litPieces(caption, r) : -1;
  const shown = line ?? caption;
  if (shown === page.shown.caption && lit === page.shown.lit) return;
  page.shown = { caption: shown, lit };
  if (!shown) {
    captionBox.replaceChildren();
    return;
  }
  const style = data.caption_style;
  captionBox.replaceChildren(...(line === null ? caption.pieces : [{ text: line }]).map((piece, i) => (
    el("span", { style: { color: i < lit ? style.highlight : style.color } }, piece.text)
  )));
}

// Draw the caption again, after what it shows has changed.
function refreshCaption() {
  page.shown = { caption: null, lit: -1 };
  if (page.data) showTime();
}

// The caption's size, place (its top at top_px, as the render's \pos) and outline, scaled from the canvas of the
// style to the width of the player.
function sizeCaption() {
  if (!page.data) return;
  const style = page.data.caption_style;
  const scale = player.clientWidth / style.canvas_w;
  captionBox.style.top = `${style.top_px * scale}px`;
  captionBox.style.fontSize = `${style.size_px * scale}px`;
  captionBox.style.setProperty("-webkit-text-stroke", `${2 * style.outline_px * scale}px ${style.outline_color}`);
}

function showSentence(t) {
  const sentence = sentenceAt(t, page.data.sentences);
  const id = sentence ? sentence.id : null;
  if (id === page.nowSentence) return;
  sentencesBox.querySelector(".sentence.now")?.classList.remove("now");
  page.nowSentence = id;
  const row = id === null ? null : rowOf(id);
  if (!row) return;
  row.classList.add("now");
  if (!video.paused && page.pop === null) showInPanel(row, false);
}

// ---- the timeline: kept, cut and decision, over the whole source -------------------------------

function renderTrack() {
  const data = page.data;
  const total = data.source_s || 1;
  const bar = (kind, start, end) => el("div", {
    class: kind, style: { left: `${(start / total) * 100}%`, width: `${((end - start) / total) * 100}%` },
  });
  const kept = data.segments.map(([start, end]) => bar("kept", start, end));
  const asked = data.trims.filter((trim) => trim.decision).map((trim) => bar("ask", trim.start, trim.end));
  track.replaceChildren(...kept, ...asked, playhead);
  $("track-start").textContent = clock(0);
  $("track-end").textContent = data.duration_clock;
}

function seekOnTrack(event) {
  if (!page.data) return;
  const box = track.getBoundingClientRect();
  page.stopAtReel = null;
  seekTo(((event.clientX - box.left) / box.width) * page.data.source_s);
}

// ---- the transcript ---------------------------------------------------------------------------

function trimAnchor(id) {
  return sentencesBox.querySelector(`[data-trim="${CSS.escape(id)}"]`);
}

// The underlined unit of the item at `index` of nav.listen (a cut point at a sentence's edge), if it has one.
function listenAnchor(index) {
  return index < 0 ? null : sentencesBox.querySelector(`[data-listen="${index}"]`);
}

// The units underlined for the cut points at the edges of sentences (the cut_point items of nav.listen): for each
// sentence, token index -> the item's index in nav.listen. The unit is the one of the sentence nearest the cut point
// (nearestUnit); when two cut points pick the same unit, it opens the first.
function edgeUnits() {
  const edges = new Map();
  page.data.nav.listen.forEach((item, index) => {
    if (item.kind !== "cut_point" || !page.sentences.has(item.sentence)) return;
    const at = nearestUnit(page.sentences.get(item.sentence).tokens, item.start);
    if (at < 0) return;
    if (!edges.has(item.sentence)) edges.set(item.sentence, new Map());
    if (!edges.get(item.sentence).has(at)) edges.get(item.sentence).set(at, index);
  });
  return edges;
}

function renderTranscript() {
  const editor = page.editor;
  // the redraw takes the focus away from the editor's field: it gets it back, with its selection
  const caret = editor !== null && document.activeElement === editor.input
    ? [editor.input.selectionStart, editor.input.selectionEnd] : null;
  page.numbered = new Set();
  const edges = edgeUnits();
  sentencesBox.replaceChildren(...page.data.sentences.map((sentence) => sentenceRow(sentence, edges.get(sentence.id) ?? new Map())));
  page.nowSentence = null;
  placeEditor(caret);
}

// Whether `sentence` has the typo card: its captions show the recognised text (asr), or did before its caption text
// was changed (the card stays, for another change or a new recording).
function hasTypoCard(sentence) {
  return Boolean(sentence) && (sentence.asr || (sentence.state === "kept" && sentence.caption_edited && sentence.recognised));
}

// One sentence: kept (its time, its words and marks, 已改字幕　復原 when its caption text was changed, the ⋯ menu),
// removed (grey, with 復原) or not spoken. `edges` are its units underlined for a cut point (see edgeUnits).
function sentenceRow(sentence, edges) {
  const row = el("p", { class: "sentence", data: { sentence: sentence.id } });
  if (sentence.state === "kept") {
    const words = tokenNodes(sentence.tokens, edges);
    const look = sentence.asr ? "asr" : sentence.caption_edited ? "edited" : null;
    row.append(...[
      el("span", { class: "ts" }, sentence.time),
      hasTypoCard(sentence) ? el("span", { class: look, data: { typo: sentence.id }, role: "button", tabindex: 0 }, words)
        : el("span", { class: look }, words),
      sentence.caption_edited ? el("button", { class: "tag edited-tag", type: "button", disabled: !canChange(),
        data: { uncaption: sentence.id, change: "" } }, "已改字幕　復原") : null,
      el("button", { class: "more", type: "button", data: { sentence: sentence.id }, "aria-label": "這一句的選單" }, "⋯"),
    ].filter((node) => node !== null));
  } else if (sentence.state === "removed") {
    row.append(
      el("span", { class: "gone" }, edges.size > 0 ? removedNodes(sentence.tokens, edges) : sentence.text),
      el("button", { class: "tag", type: "button", data: { restore: sentence.id, change: "" }, disabled: !canChange() },
        "拿掉了這一句　復原"),
    );
    if (sentence.removal && !sentence.removal.by_page) row.append(el("span", { class: "why" }, `原因：${sentence.removal.reason}`));
  } else {
    row.append(el("span", { class: "unspoken" }, sentence.text), el("span", { class: "tag" }, "沒講到，不出字幕"));
  }
  return row;
}

// The words of a sentence: a unit is a word to click (it plays from there; one underlined for a cut point opens its
// card), the units a candidate takes and a candidate's own text are its mark, a pause is its label, and a mark of
// punctuation is text.
function tokenNodes(tokens, edges) {
  const nodes = [];
  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i];
    if (token.k === "unit" && token.trim) {
      let text = "";
      while (i < tokens.length && tokens[i].k === "unit" && tokens[i].trim === token.trim) {
        text += tokens[i].text;
        i += 1;
      }
      nodes.push(markNode(token.trim, text, false));
      continue;
    }
    if (token.k === "trim" || token.k === "pause") nodes.push(markNode(token.trim, token.text, token.k === "pause"));
    else if (token.k === "unit" && edges.has(i)) nodes.push(edgeNode(token, edges.get(i)));
    else if (token.k === "unit") nodes.push(el("span", { class: "w", data: { t: token.start === null ? null : String(token.start) } }, token.text));
    else nodes.push(token.text);
    i += 1;
  }
  return nodes;
}

// The text of a removed sentence (its units and the characters between them) with its underlined units.
function removedNodes(tokens, edges) {
  return tokens.flatMap((token, i) => {
    if (token.k === "unit") return [edges.has(i) ? edgeNode(token, edges.get(i)) : token.text];
    return token.k === "char" ? [token.text] : [];
  });
}

// A unit underlined (dotted) for the cut point at `index` of nav.listen.
function edgeNode(token, index) {
  return el("span", { class: "w edge", data: { listen: String(index) }, role: "button", tabindex: 0 }, token.text);
}

// The mark of a candidate: red and struck out when cut, an orange frame and its number when it is a decision, a
// dotted underline when its cut point needs listening.
function markNode(id, text, isPause) {
  const trim = page.trims.get(id);
  if (!trim) return text;
  const classes = ["mark", trim.cut ? "cut" : "kept"];
  if (isPause) classes.push("pause");
  if (trim.decision) classes.push("ask");
  if (trim.listen) classes.push("listen");
  const numbered = trim.number !== null && !page.numbered.has(id);
  if (numbered) page.numbered.add(id);
  return el("span", { class: classes.join(" "), data: { trim: id }, role: "button", tabindex: 0 },
    numbered ? el("span", { class: "num" }, String(trim.number)) : null, text);
}

function onTranscriptClick(event) {
  const target = event.target;
  const restore = target.closest("button[data-restore]");
  if (restore) return sendChange({ type: "restore", sentence: restore.dataset.restore });
  const uncaption = target.closest("button[data-uncaption]");
  if (uncaption) return sendChange({ type: "caption", sentence: uncaption.dataset.uncaption, text: null });
  const more = target.closest("button.more");
  if (more) {
    const id = more.dataset.sentence;
    const open = page.pop && page.pop.kind === "menu" && page.pop.id === id;
    return open ? closePop() : openPop({ kind: "menu", id });
  }
  const mark = target.closest("[data-trim]");
  if (mark) return openTrim(mark.dataset.trim);
  const edge = target.closest("[data-listen]");
  if (edge) return openListen(page.data.nav.listen[Number(edge.dataset.listen)]);
  const typo = target.closest("[data-typo]");
  if (typo) return openTypo(typo.dataset.typo);
  const word = target.closest("[data-t]");
  if (word) playFrom(Number(word.dataset.t));
  return undefined;
}

function onTranscriptKey(event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  const target = event.target.closest("[role=button]");
  if (!target) return;
  event.preventDefault();
  target.click();
}

// ---- popovers: the card of an item, and the ⋯ menu ---------------------------------------------

function openTrim(id) {
  if (!page.trims.has(id)) return;
  markSeen(id);
  const index = page.data.nav.decisions.indexOf(id);
  if (index >= 0) page.at.decision = index;
  openPop({ kind: "trim", id });
}

function openTypo(id) {
  markSeen(id);
  const index = page.data.nav.asr.indexOf(id);
  if (index >= 0) page.at.asr = index;
  openPop({ kind: "typo", id });
}

// The card of an item of nav.listen about a sentence.
function openListen(item) {
  if (!item || item.trim) return;
  page.at.listen = page.data.nav.listen.indexOf(item);
  openPop({ kind: "listen", id: item.sentence, which: item.kind, start: item.start });
}

function openPop(spec) {
  page.pop = spec;
  renderPop();
}

function closePop() {
  page.pop = null;
  pop.hidden = true;
  pop.replaceChildren();
}

// Draw the open popover again (after the data changed), beside its mark; close it when its item is gone.
function renderPop() {
  const spec = page.pop;
  if (!spec || !page.data) return closePop();
  let built = null;
  if (spec.kind === "trim" && page.trims.has(spec.id)) {
    built = { kind: "", anchor: trimAnchor(spec.id), nodes: trimCard(page.trims.get(spec.id)) };
  } else if (spec.kind === "typo" && hasTypoCard(page.sentences.get(spec.id))) {
    built = { kind: "typo", anchor: rowOf(spec.id)?.querySelector("[data-typo]"), nodes: typoCard(page.sentences.get(spec.id)) };
  } else if (spec.kind === "listen") {
    const index = page.data.nav.listen.findIndex((item) => (
      item.sentence === spec.id && item.kind === spec.which && item.start === spec.start));
    if (index >= 0 && page.sentences.has(spec.id)) {
      built = { kind: "", anchor: listenAnchor(index) ?? rowOf(spec.id), nodes: listenCard(page.data.nav.listen[index]) };
    }
  } else if (spec.kind === "menu" && page.sentences.get(spec.id)?.state === "kept") {
    built = { kind: "menu", anchor: rowOf(spec.id)?.querySelector(".more"), nodes: menuItems(page.sentences.get(spec.id)) };
  }
  if (built === null) return closePop();
  pop.className = built.kind ? `pop ${built.kind}` : "pop";
  pop.replaceChildren(...built.nodes);
  pop.hidden = false;
  placePop(built.anchor ?? null);
  return undefined;
}

// Put the popover beside `anchor`, inside the transcript (it scrolls with it), where all of it shows (placePopover):
// below the mark, above it, or below it with the panel scrolled. Only the panel scrolls, never the window.
function placePop(anchor) {
  const box = script.getBoundingClientRect();
  const view = { top: script.scrollTop, left: script.scrollLeft, width: script.clientWidth, height: script.clientHeight };
  let spot = { top: view.top, bottom: view.top, left: view.left };
  if (anchor) {
    const rect = anchor.getBoundingClientRect();
    const down = script.scrollTop - box.top - script.clientTop; // from the window to the panel's content
    spot = { top: rect.top + down, bottom: rect.bottom + down, left: rect.left + script.scrollLeft - box.left - script.clientLeft };
  }
  pop.style.left = "0px"; // measured where it has the panel's whole width (the menu's width follows the room it has)
  const placed = placePopover({ anchor: spot, size: { width: pop.offsetWidth, height: pop.offsetHeight }, view });
  pop.style.top = `${placed.top}px`;
  pop.style.left = `${placed.left}px`;
  if (placed.scrollTop !== script.scrollTop) script.scrollTop = placed.scrollTop;
}

// The card of a candidate: its number, time, kind and text; the suggestion; 剪掉 and 保留 with the chosen one filled
// and （建議） on the suggested one; ▶ 播這一段前後.
function trimCard(trim) {
  const head = [trim.time, trim.label].filter((part) => part !== "").join("　") + (trim.text ? `「${trim.text}」` : "");
  const choice = (cut, text) => button(text + (trim.suggest_cut === cut ? SUGGESTED : ""), {
    class: `btn ${cut ? "cut-choice" : "keep-choice"}${trim.cut === cut ? " on" : ""}`,
    "aria-pressed": String(trim.cut === cut),
    data: { change: "" },
    disabled: !canChange(),
  }, () => {
    if (trim.cut !== cut) sendChange({ type: "cut", trim: trim.id, cut });
  });
  return [
    el("h4", {}, trim.number !== null ? el("span", { class: "num" }, String(trim.number)) : null,
      trim.number !== null ? " " : null, head),
    el("div", { class: "rec" }, trim.note),
    trim.listen_note ? el("div", { class: "rec" }, trim.listen_note) : null,
    el("div", { class: "row" },
      choice(true, "剪掉"),
      choice(false, "保留"),
      button("▶ 播這一段前後", {}, () => playSpan(trim.start, trim.end, AROUND_S))),
    el("div", { class: "hint" }, "切換後影片馬上照新的決定播。"),
  ].filter((node) => node !== null);
}

// A sentence to put inside 「」: without its final full stop.
function quoted(text) {
  return text.endsWith("。") ? text.slice(0, -1) : text;
}

// The typo card (see hasTypoCard): what was recognised and what the script says, Claude's note when there is one
// (else ASR_NOTE while the captions show the recognised text), 改字幕, and recording again through the chat. Once
// the caption text was changed, the card says so and no longer offers to keep the typo.
function typoCard(sentence) {
  const fixed = sentence.caption_edited;
  return [
    el("h4", {}, `${sentence.time}　${fixed ? "已改字幕" : "字幕可能有錯字"}`),
    el("div", { class: "said" }, fixed
      ? `字幕改成了「${quoted(sentence.caption)}」。辨識出來的是「${quoted(sentence.original)}」，腳本是「${quoted(sentence.script)}」。`
      : `字幕用的是辨識出來的字：「${quoted(sentence.original)}」。腳本是「${quoted(sentence.script)}」。`),
    sentence.note ? el("div", { class: "rec" }, sentence.note) : null,
    el("div", { class: "row" },
      button("改字幕", { class: "btn primary" }, () => openEditor(sentence.id)),
      button("拿掉這一句", { data: { change: "" }, disabled: !canChange() },
        () => sendChange({ type: "remove", sentence: sentence.id })),
      fixed ? null : button("保留（錯字會留在成品裡）", {}, closePop),
      button("▶ 播這一句", {}, () => playSpan(sentence.start, sentence.end, 0))),
    el("div", { class: "hint" }, `要重錄：回到 Claude 對話說「${sentence.time} 那句要重錄」。`),
  ].filter((node) => node !== null);
}

// The card of an item of nav.listen about a sentence (a cut point of the reel, which may be at the edge of a removed
// sentence, or a sentence that matches the script poorly): its time and the sentence, the item's note (Claude's
// for the sentence, or why to listen), and ▶ 播這一段前後, which plays the item's span (the cut point, or the whole
// sentence) with AROUND_S before and after.
function listenCard(item) {
  const sentence = page.sentences.get(item.sentence);
  return [
    el("h4", {}, `${item.time}　「${quoted(sentence.text)}」`),
    el("div", { class: "rec" }, item.note),
    el("div", { class: "row" }, button("▶ 播這一段前後", {}, () => playSpan(item.start, item.end, AROUND_S))),
  ];
}

function menuItems(sentence) {
  const item = (text, props, act) => {
    const node = el("button", { type: "button", ...props }, text);
    node.addEventListener("click", () => {
      closePop();
      act();
    });
    return node;
  };
  return [
    item("改字幕", {}, () => openEditor(sentence.id)),
    item("拿掉這一句", { data: { change: "" }, disabled: !canChange() },
      () => sendChange({ type: "remove", sentence: sentence.id })),
    item("從這裡播放", {}, () => playFrom(sentence.start)),
  ];
}

// A press outside the popover, the transcript's marks and the player closes it.
function onPointerDown(event) {
  if (page.pop === null) return;
  const target = event.target;
  if (pop.contains(target) || target.closest(".left, [data-trim], [data-listen], [data-typo], button.more, #nav")) return;
  closePop();
}

// ---- changes ----------------------------------------------------------------------------------

// Send a change with the version the page shows. Done: the data loads again (with the notice when it is not the
// version the change made: Claude changed the edit meanwhile). Refused (409, 422, 423 ...): the server's text
// shows; on a conflict the data loads again first, and on 423 the page asks the state. True when it was done.
async function sendChange(change) {
  if (!canChange()) return false;
  page.changes += 1;
  setBusy(true);
  let done = false;
  try {
    const answer = await request("POST", "api/change", { version: page.data.version, change });
    if (answer.status === 200 && typeof answer.body.version === "string") {
      done = true;
      const made = answer.body.version;
      page.ownVersion = made;
      const drops = page.drops;
      if ((await loadData()) && page.data.version !== made) notifyUnlessDropped(drops, UPDATED_TEXT);
    } else {
      const drops = page.drops;
      if (answer.status === 409 && answer.body.conflict === true) await loadData();
      notifyUnlessDropped(drops, errorText(answer), true);
      if (answer.status === 423) await checkState();
    }
  } catch (error) {
    if (!(error instanceof Stopped)) notify(SERVER_ERROR_TEXT, true);
  } finally {
    page.changes += 1;
    setBusy(false);
  }
  return done;
}

// ---- the confirm dialog and the approval -------------------------------------------------------

// The dialog lists the new length and the cuts, every decision whose card was not opened with what it will be,
// and every sentence whose captions keep the recognised text. It keeps the version it was built from: the approval
// is for that version, and a load of another one closes the dialog (loadData). It does not open while the caption
// editor has text to save (the approval would drop it): the notice says so and the editor shows. An editor with
// nothing to save closes.
function openConfirm() {
  if (!canChange()) return;
  closePop();
  const editor = page.editor;
  if (editor !== null && unsavedText(editor)) {
    notify(UNSAVED_CAPTION_TEXT, true);
    showInPanel(editor.node, false);
    editor.input.focus({ preventScroll: true });
    return;
  }
  closeEditor();
  const data = page.data;
  page.confirmVersion = data.version;
  const body = $("confirm-body");
  body.replaceChildren(el("p", {}, `新長 ${data.duration_clock}，剪掉 ${data.cuts} 段。`));
  const unseen = data.nav.decisions.filter((id) => !page.seen.has(id)).map((id) => page.trims.get(id)).filter(Boolean);
  if (unseen.length > 0) {
    body.append(
      el("p", {}, `還有 ${unseen.length} 項你沒點開看，會這樣處理：`),
      el("ul", {}, unseen.map((trim) => el("li", {}, `${trim.time}「${trim.text}」${trim.cut ? "剪掉" : "保留"}`))),
    );
  }
  for (const id of data.nav.asr) {
    const sentence = page.sentences.get(id);
    if (sentence) body.append(el("p", { class: "warn" }, `${sentence.time} 那一句的字幕照辨識的字出，錯字會留在成品裡。`));
  }
  confirmDialog.showModal();
}

// Approve the version the dialog showed. Refused because the edit changed since (409): the data loads again and the
// server's text says so; the dialog is closed already.
async function approve() {
  confirmDialog.close();
  const version = page.confirmVersion;
  page.confirmVersion = null;
  if (version === null || !canChange()) return;
  page.changes += 1;
  setBusy(true);
  try {
    const answer = await request("POST", "api/approve", { version });
    if (answer.status === 202) {
      await applyStatus(answer.body);
    } else {
      if (answer.status === 409 && answer.body.conflict === true) await loadData();
      notify(errorText(answer), true);
      if (answer.status === 423) await checkState();
    }
  } catch (error) {
    if (!(error instanceof Stopped)) notify(SERVER_ERROR_TEXT, true);
  } finally {
    page.changes += 1;
    setBusy(false);
  }
}

// ---- polling ----------------------------------------------------------------------------------

// A state from the server: pollVersion decides whether to load the data again and whether to say that Claude
// changed the edit; the view follows the state. A new state draws the transcript again: whether it offers changes
// depends on the state. An `outdated` answer (a change of the page's started or landed while it was out) is dropped.
async function applyStatus(status, outdated = false) {
  const decided = pollVersion({
    shown: page.data === null ? null : page.data.version,
    failed: page.needsLoad,
    polled: status.version,
    own: page.ownVersion,
    outdated,
  });
  page.ownVersion = decided.own;
  if (outdated) return;
  const drops = page.drops;
  if (decided.load && (await loadData()) && decided.notice) notifyUnlessDropped(drops, UPDATED_TEXT);
  const changed = status.state !== page.state;
  Object.assign(page, pageState(status));
  renderView();
  renderBanner();
  if (changed && page.data) {
    renderTranscript();
    renderPop();
  }
}

async function checkState() {
  const changes = page.changes;
  const answer = await request("GET", "api/state");
  if (answer.status === 200) await applyStatus(answer.body, changes !== page.changes);
  else notify(errorText(answer), true);
}

// Ask the state every POLL_MS; not while a change or the approval is on its way (it asks itself when it needs to).
async function pollLoop() {
  try {
    if (!page.busy) await checkState();
  } catch (error) {
    if (!(error instanceof Stopped)) notify(SERVER_ERROR_TEXT, true);
  } finally {
    if (!page.stopped) page.pollTimer = setTimeout(pollLoop, POLL_MS);
  }
}

// ---- drawing the data -------------------------------------------------------------------------

function renderData() {
  const nav = page.data.nav;
  for (const [which, list] of [["decision", nav.decisions], ["listen", nav.listen], ["asr", nav.asr]]) {
    if (page.at[which] >= list.length) page.at[which] = -1;
  }
  renderTop();
  renderNav();
  renderTrack();
  renderTranscript();
  sizeCaption();
  page.shown = { caption: null, lit: -1 };
  tick();
  renderPop();
}

// ---- start ------------------------------------------------------------------------------------

// What the other modules need of this one, before anything can call it: what a request does when the server is gone
// (shared.js), and the popover, the panel, the player, the changes and the caption on screen for the editor.
function connect() {
  onServerGone(serverGone);
  connectEditor({ closePop, holdAt, playSpan, refreshCaption, sendChange, showInPanel });
}

function bind() {
  $("approve").addEventListener("click", openConfirm);
  $("confirm-back").addEventListener("click", () => confirmDialog.close());
  $("confirm-go").addEventListener("click", approve);
  $("prev").addEventListener("click", () => goToDecision(-1));
  $("next").addEventListener("click", () => goToDecision(1));
  $("listen").addEventListener("click", goToListen);
  $("asr").addEventListener("click", goToTypo);
  $("play").addEventListener("click", togglePlay);
  $("slow").addEventListener("click", toggleSlow);
  $("reveal").addEventListener("click", reveal);
  player.addEventListener("click", togglePlay);
  track.addEventListener("click", seekOnTrack);
  sentencesBox.addEventListener("click", onTranscriptClick);
  sentencesBox.addEventListener("keydown", onTranscriptKey);
  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePop();
  });
  video.addEventListener("play", renderPlay);
  video.addEventListener("pause", renderPlay);
  new ResizeObserver(sizeCaption).observe(player);
  watchFrames();
}

async function start() {
  connect();
  bind();
  renderPlay();
  try {
    await loadData();
  } catch (error) {
    if (!(error instanceof Stopped)) notify(SERVER_ERROR_TEXT, true);
  }
  if (!page.stopped) pollLoop();
}

start();

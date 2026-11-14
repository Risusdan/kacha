// The caption editor of the review page: it changes the caption text of one kept sentence. It asks POST
// api/caption-lines for the lines the server would break the text into as the user types, and saves through the
// page's change (sendChange, in review.js, which sends POST api/change). What it decides is in logic.js (what is
// marked, what is saved, what the field holds after a reload, whether another editor may open); what it does to the
// page is here. The open editor is page.editor (shared.js), drawn in place of its sentence's row.
//
// What the editor needs from review.js (the popover, the transcript panel, the player, the changes and the caption on
// screen) it gets through connectEditor, before the page starts: review.js imports this module, so this module cannot
// import review.js. This module only declares; importing it starts nothing.

import {
  captionToSave,
  changedRuns,
  fieldAfterReload,
  hasUnsavedCaption,
  keepsOpenEditor,
  previewLine,
  reelTime,
} from "./logic.js";
import { SERVER_ERROR_TEXT, Stopped, button, canChange, el, errorText, hideNotice, notify, page, request, rowOf } from "./shared.js";

const PREVIEW_MS = 300; // the caption editor asks for the lines this long after the typing stops
export const UNSAVED_CAPTION_TEXT = "改的字幕還沒存，先按「儲存」或「取消」，再按同意。"; // 同意 while the editor has text to save
const CAPTION_DROPPED_TEXT = "剪接設定剛被更新，你改到一半的字幕沒有存。"; // the page closed an editor that had text to save
const UNSAVED_OPEN_TEXT = "改的字幕還沒存，先按「儲存」或「取消」，再改別句。"; // the editor of another sentence while one has text to save

// What review.js gives the editor: closePop() (the popover), showInPanel(node, centre) (the transcript panel),
// holdAt(t) and playSpan(start, end, pad) (the player), sendChange(change) (a change of the edit; true when it was
// done) and refreshCaption() (draws the caption on screen again).
let closePop;
let holdAt;
let playSpan;
let refreshCaption;
let sendChange;
let showInPanel;

export function connectEditor(calls) {
  ({ closePop, holdAt, playSpan, refreshCaption, sendChange, showInPanel } = calls);
}

// Show `text` for an update of the page (a load of the data) unless that update dropped the text of a caption editor,
// that is, page.drops is not `drops` any more, the count before the update's load: CAPTION_DROPPED_TEXT shows then, and
// says the same (the settings were just updated), so a second notice of the update would only replace it.
export function notifyUnlessDropped(drops, text, isError = false) {
  if (page.drops === drops) notify(text, isError);
}

// Open the caption editor of the kept sentence `id` in place of its row: what the sentence says (recognised, or the
// script's; read only), the field with the characters that differ from that in green (changedRuns), the lines the
// server would break the text into (askLines, PREVIEW_MS after the typing stops, not while an input method is
// composing), and 儲存, 取消, 復原成原本的字, ▶ 播這一句. The video holds at the sentence, and while the editor is
// open the caption over it shows those lines (editorLine). One editor is open at a time: an open editor of another
// sentence that holds text to save stays, with its text and the focus, and UNSAVED_OPEN_TEXT says why the new one did
// not open (keepsOpenEditor), as 同意 does with UNSAVED_CAPTION_TEXT (openConfirm); asked for again, the open editor of
// the same sentence stays as it is (its text, its selection) and only shows and takes the focus, with no notice, so
// that nothing typed in it is lost; any other open editor closes.
export function openEditor(id) {
  const sentence = page.sentences.get(id);
  if (!sentence || sentence.state !== "kept") return;
  const open = page.editor;
  closePop();
  const field = open === null ? null : { id: open.id, text: open.input.value, saved: open.saved, original: open.original };
  if (keepsOpenEditor(field, id)) {
    if (open.id !== id) notify(UNSAVED_OPEN_TEXT, true);
    showInPanel(open.node, false);
    open.input.focus({ preventScroll: true });
    return;
  }
  closeEditor();
  const input = el("input", { type: "text", class: "field-input", spellcheck: "false", autocomplete: "off",
    "aria-label": "字幕要顯示的字" });
  input.value = sentence.caption;
  const editor = {
    id, input, original: sentence.original, saved: sentence.caption, lines: null, seq: 0, timer: null, composing: false,
    loaded: page.data, // the data the lines were asked with: a later load asks again (placeEditor)
    marks: el("div", { class: "field-marks", "aria-hidden": "true" }),
    linesBox: el("div", { class: "lines" }),
  };
  const said = sentence.recognised ? "講了什麼（辨識的結果）" : "原本的字幕（取自腳本）";
  editor.node = el("div", { class: "edit" },
    el("div", { class: "lbl" }, `${sentence.time}　${said}`),
    el("div", { class: "heard" }, sentence.original),
    el("div", { class: "lbl" }, "字幕要顯示"),
    el("div", { class: "field" }, editor.marks, input),
    el("div", { class: "lbl" }, "會斷成這幾行"),
    editor.linesBox,
    el("div", { class: "row" },
      button("儲存", { class: "btn primary", data: { change: "" }, disabled: !canChange() }, () => saveEditor(editor)),
      button("取消", {}, closeEditor),
      button("復原成原本的字", {}, () => {
        input.value = editor.original;
        onEditorInput(editor);
      }),
      button("▶ 播這一句", {}, () => {
        const now = page.sentences.get(id);
        if (now) playSpan(now.start, now.end, 0);
      })),
    el("div", { class: "note" }, "改過的字用綠色標出來。字幕出現的時間照講話的時間；新加的字借旁邊的字的時間。"));
  input.addEventListener("input", () => onEditorInput(editor));
  input.addEventListener("compositionstart", () => {
    editor.composing = true;
  });
  input.addEventListener("compositionend", () => {
    editor.composing = false;
    onEditorInput(editor);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229) saveEditor(editor);
  });
  for (const name of ["scroll", "keyup", "mouseup", "select"]) {
    input.addEventListener(name, () => {
      editor.marks.scrollLeft = input.scrollLeft;
    });
  }
  page.editor = editor;
  placeEditor(null);
  showMarks(editor);
  showInPanel(editor.node, false);
  input.focus({ preventScroll: true });
  input.setSelectionRange(input.value.length, input.value.length);
  holdAt(sentence.start);
  askLines(editor);
}

// Put the open editor in place of its sentence's row (again each time the transcript is drawn), with the focus and
// the selection `caret` ([start, end]) back in its field when it had them; close it (dropEditor: the notice says so
// when it held text to save) when the sentence is not in the reel any more, or its own text changed (it was
// transcribed again). The caption it compares the field with is the one the data now holds (Claude may have changed
// it); a field nothing was typed in follows that caption (fieldAfterReload), with the caret at its end if it had the
// focus (it never takes a focus it did not have), so that a later 儲存 does not write the old text back over Claude's
// change and the field does not count as holding text to save; a field the user typed in stays as it is. Data loaded
// since the lines were asked asks them again (the style may have changed, or the edit become ready). A redraw takes
// the field out of the page, which can end an input method's composition without a compositionend.
export function placeEditor(caret) {
  const editor = page.editor;
  if (editor === null) return;
  const row = rowOf(editor.id);
  const sentence = page.sentences.get(editor.id);
  if (sentence?.state !== "kept" || sentence.original !== editor.original || !row) {
    dropEditor();
    return;
  }
  const text = fieldAfterReload(editor.input.value, editor.saved, sentence.caption);
  let place = caret;
  if (text !== editor.input.value) {
    editor.input.value = text;
    showMarks(editor);
    if (caret) place = [text.length, text.length]; // the old selection no longer fits; no focus it did not have
  }
  editor.saved = sentence.caption;
  editor.composing = false;
  row.hidden = true;
  row.after(editor.node);
  if (place) {
    editor.input.focus({ preventScroll: true });
    editor.input.setSelectionRange(...place);
  }
  if (editor.loaded !== page.data) {
    editor.loaded = page.data;
    askLines(editor);
  }
}

export function closeEditor() {
  const editor = page.editor;
  if (editor === null) return;
  page.editor = null;
  clearTimeout(editor.timer);
  editor.node.remove();
  const row = rowOf(editor.id);
  if (row) row.hidden = false;
  refreshCaption();
}

// Close the open editor because the page does, not the user (儲存, 取消, or 同意 with nothing to save): its sentence
// went, or was transcribed again (placeEditor), or the page left the editing states (renderView). Text typed in it that
// is not saved goes with it, so an error notice says so: CAPTION_DROPPED_TEXT, which an update's own notice that
// Claude changed the edit does not replace (notifyUnlessDropped).
export function dropEditor() {
  const editor = page.editor;
  if (editor === null) return;
  const lost = unsavedText(editor);
  closeEditor();
  if (!lost) return;
  page.drops += 1;
  notify(CAPTION_DROPPED_TEXT, true);
}

// The field's backdrop: the field's text with the changed characters marked, scrolled as the field is.
function showMarks(editor) {
  editor.marks.replaceChildren(...changedRuns(editor.original, editor.input.value).map((run) => (
    run.changed ? el("mark", {}, run.text) : run.text)));
  editor.marks.scrollLeft = editor.input.scrollLeft;
}

function onEditorInput(editor) {
  showMarks(editor);
  clearTimeout(editor.timer);
  if (!editor.composing) editor.timer = setTimeout(() => askLines(editor), PREVIEW_MS);
}

// Ask the server for the lines of the field's text, and show them, or why the text would not be saved (the caption
// over the video then shows the saved caption). An answer that comes after a later question was asked, or after the
// editor closed, is dropped: it is for older text.
async function askLines(editor) {
  clearTimeout(editor.timer);
  editor.seq += 1;
  const seq = editor.seq;
  let lines = null;
  let refusal = SERVER_ERROR_TEXT;
  try {
    const answer = await request("POST", "api/caption-lines", { sentence: editor.id, text: editor.input.value });
    if (answer.status === 200 && Array.isArray(answer.body.lines)) lines = answer.body.lines.map(String);
    else refusal = errorText(answer);
  } catch (error) {
    if (error instanceof Stopped) return; // the banner says so
  }
  if (page.editor !== editor || seq !== editor.seq) return;
  editor.lines = lines;
  editor.linesBox.replaceChildren(...(lines === null ? [el("div", { class: "error" }, refusal)]
    : lines.map((line) => el("span", {}, line))));
  refreshCaption();
}

// Save the field's text as the sentence's caption text, or null when it is the sentence's own text again; nothing
// goes out when it is the caption already (captionToSave). Saved: the editor closes, and so do the notices that
// it had text to save (UNSAVED_CAPTION_TEXT, UNSAVED_OPEN_TEXT), if one still shows. Refused: it stays open, and the
// notice says why.
async function saveEditor(editor) {
  if (page.editor !== editor || !canChange()) return;
  const text = textToSave(editor);
  if (text === undefined) {
    closeEditor();
    hideNotice(UNSAVED_CAPTION_TEXT);
    hideNotice(UNSAVED_OPEN_TEXT);
    return;
  }
  const saved = await sendChange({ type: "caption", sentence: editor.id, text });
  if (saved && page.editor === editor) {
    closeEditor();
    hideNotice(UNSAVED_CAPTION_TEXT);
    hideNotice(UNSAVED_OPEN_TEXT);
  }
}

function textToSave(editor) {
  return captionToSave(editor.input.value, editor.saved, editor.original);
}

// Whether the editor's field holds text that closing the editor would lose: what 同意 waits for (openConfirm), and
// what the page says it dropped when it closes the editor itself (dropEditor).
export function unsavedText(editor) {
  return hasUnsavedCaption(editor.input.value, editor.saved, editor.original);
}

// The line of the open editor on screen at reel time `r`: its lines share the sentence's time in the reel
// (previewLine); null elsewhere, and while the editor has no lines to show.
export function editorLine(r) {
  const editor = page.editor;
  const sentence = editor !== null && editor.lines !== null ? page.sentences.get(editor.id) : null;
  if (!sentence) return null;
  const segments = page.data.segments;
  return previewLine(r, reelTime(sentence.start, segments), reelTime(sentence.end, segments), editor.lines);
}

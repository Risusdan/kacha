# Interactive Review Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local review page for gate 1 that plays the cut reel by skipping cut ranges, lets the user toggle cuts, remove or restore sentences and edit caption text, and approves and renders from the page; plus a static gate page whose clips play on an iPhone.

**Architecture:** `pipeline review <project>` makes a 540×960 review video once, then runs a standard-library HTTP server on 127.0.0.1 with a random key. The page (vanilla JS, no build step) gets segments, captions and the transcript from the server, skips cut ranges while it plays, and sends every change to the server, which validates the edit in memory, writes edit.json atomically and records the validate pass. Approval runs storyboard → approve → render in a background thread of the server.

**Tech Stack:** Python 3.12 standard library (`http.server`, `threading`, `secrets`, `difflib`), ffmpeg (as the render uses it), plain ES modules in the browser, `node --test` for the page's pure functions (dev only; skipped when node is missing).

**Spec:** `docs/superpowers/specs/2026-11-12-interactive-review-design.md` (cited below as "spec §N"). It changes the gate-1 flow of `docs/superpowers/specs/2026-11-12-phase1-revision-design.md`.

## Global Constraints

- Everything the page and the static page show the user is Traditional Chinese (Taiwan usage). No sentence or candidate id (`s01`, `t01`) and no English reason text is ever shown; ids travel only in data attributes and API payloads.
- Times shown are in-reel times, `m:ss.s`, computed as `storyboard.clock(edit.map_time(t, segments))` (and its JS twin `clock`/`reelTime` in `logic.js`).
- Every suggestion text (Claude's notes and program-made ones) starts with `建議` and contains `。因為`.
- The server binds `127.0.0.1` only, on a port the OS picks (port 0). Every request needs the key: `?key=<key>` on `GET /` (which sets the cookie and redirects), the cookie `review_key_<port>` (`COOKIE_PREFIX` + the port, so two servers on one host do not overwrite each other's key) everywhere else. A request whose `Host` header is not exactly `127.0.0.1:<port>` gets 403.
- The server only ever serves fixed routes; no part of a request path reaches the filesystem.
- No new Python package. The page loads nothing from the network: no CDN, no external font; `:root { color-scheme: light; }` and an explicit light background.
- edit.json is written only by `review_actions.write_edit` (temp file in `work/`, then `os.replace`) after `edit.validate_edit` returned no error for the new edit; then `edit.validate_project(p)` runs so `state.json` records the pass.
- The server writes only inside the project folder (`work/edit.json`, `work/review-undo.json`, `work/review.mp4`, `work/review.log`, and what storyboard and render already write). It deletes nothing of its own; storyboard and render keep their existing delete paths.
- Tests never open a browser or a file-manager window: `serve_review` takes `open_browser` and `ReviewServer` takes `reveal` as injectables, and the CLI has `--no-browser`.
- Free-space threshold: `assets.MIN_FREE_GB` (15) GiB, measured with `shutil.disk_usage(p.dir).free / 2**30`.
- The server stops after `REVIEW_IDLE_S = 1800` seconds without a request, never while an approval job runs. The page polls `GET /api/state` every `POLL_MS = 3000` ms.
- Example sentences in tests and docs are made up; no user material, script or transcript goes into the repo.
- Commit messages are plain sentences with no AI or tool attribution; git commands that would open an editor get `-m` or `--no-edit`.
- `uv run pytest` must stay green after every task.

## Review Focus

1. Browser range requests as Chrome and Safari send them (`bytes=0-1`, `bytes=1000-`, `bytes=-500`, a start past the end) must get 206 with the right bytes, or 416; Safari will not play a video otherwise. Test in Task 7.
2. A page from another site, or a DNS-rebinding host name, must not reach the API: wrong `Host` → 403, no cookie → 403. Test in Task 7.
3. Validate errors are English; a change the user makes in the page must be refused in Chinese. Removing the last kept sentence → `至少要留下一句，這個改動沒有存。`; any other error → `這個改動會讓剪接設定出錯，沒有存。請回對話請 Claude 看看。` Test in Task 6.
4. Claude re-running transcribe or trim, or writing an edit.json that has not passed validate, while the page is open: `GET /api/data` answers 409 with `Claude 正在更新剪接設定，請稍候。`, changes are refused the same way, and the page keeps polling. Test in Task 7.
5. A change sent while an approval runs, or after the gate was approved in the chat, is refused with 423 and `正在輸出，不能改決定。` (or `已經同意了。要再改，請回對話跟 Claude 說。`). Test in Task 8.

---

### Task 1: Spike — skip playback precision and pops (controller task, throwaway)

Run by the controller, not a subagent: it needs the browser and the user's ears. Everything lives in the session scratchpad; nothing goes into the repo except the results paragraph in spec §11.

**Files:**
- Create (scratchpad only): `spike/review.mp4`, `spike/segments.json`, `spike/index.html`, `spike/serve.py`
- Modify: `docs/superpowers/specs/2026-11-12-interactive-review-design.md` (§11 results)

- [ ] **Step 1:** From the isolated real-material project `scratchpad/t11/studio/projects/2026-11-12-revision-check` (run with `scratchpad/t11-env.sh`, which points HOME, XDG_* and TMPDIR into the scratchpad), make `spike/review.mp4` with ffmpeg, writing out by hand the arguments Task 4 specifies for `review_video_args` (Task 4 is not built yet), from `probe.json`'s `video_source` and `work/audio48k.wav`. Write `spike/segments.json` from `build_segments` of the approved edit. Record encode time and size.
- [ ] **Step 2:** Write `spike/serve.py`: a standard-library server on 127.0.0.1 with Range support (Python's `SimpleHTTPRequestHandler` has none, and Safari needs it), and `spike/index.html`: plays the video, skips cut ranges, and logs for every jump the target segment start, the first presented frame's `mediaTime` after the seek (`requestVideoFrameCallback`), and the last frame's `mediaTime` before the jump. Two buttons switch the trigger between `timeupdate` and `requestVideoFrameCallback`; a "joints" mode plays 2 s before each joint for 4 s. The page shows a table: joint, early or late by how many ms.
- [ ] **Step 3:** Run it in Chrome yourself if the tab can play (it may report `visibilityState` hidden); then send the user the URL and ask them to open it in Chrome and Safari, read off the table, and listen to joints 1–5 for pops or stalls. Do not wait: go on with Task 2 and record the answer when it comes.
- [ ] **Step 4:** Write the measurements into spec §11 (a short results list, dated), and adjust `REVIEW_GOP_FRAMES` / bitrate in Task 4's constants if the numbers call for it. If the jump error is over one frame (0.033 s) or the user hears pops, stop and ask the user about the "輸出真的預覽" button (spec §5.3); that is their decision.
- [ ] **Step 5:** Commit the spec change: `git commit -m "Spec 11 records the skip-playback measurements"`.

### Task 2: `notes` in edit.json

**Files:**
- Modify: `pipeline/edit.py` (`TOP_KEYS` loop in `validate_edit`, new `_check_notes`)
- Test: `tests/test_edit.py`

**Interfaces:**
- Produces: `edit.OPTIONAL_KEYS = ("notes",)`; `edit["notes"]` is `{trim id or spoken sentence id: str}`; `edit.NOTE_PREFIX = "建議"`, `edit.NOTE_REASON = "。因為"`.

- [ ] **Step 1: Write the failing tests** (build edits with `helpers.default_edit(T, TR)`; pick ids from `TR["trims"]` and from spoken / unspoken sentences of `T`)

```python
def test_notes_are_optional(T, TR):
    assert validate_edit(default_edit(T, TR), T, TR) == []

def test_notes_accept_a_trim_and_a_spoken_sentence(T, TR):
    edit = {**default_edit(T, TR), "notes": {TR["trims"][0]["id"]: "建議剪掉。因為這是贅字。",
                                            spoken_id(T): "建議聽一下。因為字幕用的是辨識出來的字。"}}
    assert validate_edit(edit, T, TR) == []

def test_notes_refuse_unknown_and_unspoken_ids(T, TR):
    edit = {**default_edit(T, TR), "notes": {"t999": "建議剪掉。因為重複。", unspoken_id(T): "建議保留。因為好。"}}
    assert validate_edit(edit, T, TR) == [
        "notes.t999: not a trim or a spoken sentence",
        f"notes.{unspoken_id(T)}: not a trim or a spoken sentence",
    ]

@pytest.mark.parametrize("text", ["剪掉。因為重複。", "建議剪掉", "建議剪掉，因為重複。", ""])
def test_a_note_starts_with_a_suggestion_and_gives_a_reason(T, TR, text):
    tid = TR["trims"][0]["id"]
    edit = {**default_edit(T, TR), "notes": {tid: text}}
    assert validate_edit(edit, T, TR) == [f"notes.{tid}: must start with 建議 and contain 。因為"]

def test_notes_must_be_an_object_of_strings(T, TR):
    assert validate_edit({**default_edit(T, TR), "notes": []}, T, TR) == ["notes: must be an object, got an array"]
    tid = TR["trims"][0]["id"]
    assert validate_edit({**default_edit(T, TR), "notes": {tid: 3}}, T, TR) == [f"notes.{tid}: must be a string, got a number"]
```

(If `T` has no unspoken sentence, the test makes one by setting `spoken` to False on a copy and removing its `sentences` entry; match the exact wording `_describe` produces for "an array" / "a number" by reading `_describe`.)

- [ ] **Step 2:** Run `uv run pytest tests/test_edit.py -k notes -v` — FAIL (`notes: unknown key`).
- [ ] **Step 3:** In `validate_edit`, accept keys in `OPTIONAL_KEYS` without requiring them; add `_check_notes(notes, table, trims, errors)` called when `"notes" in edit`, using `_is_object` and the messages above.
- [ ] **Step 4:** Run `uv run pytest tests/test_edit.py -v` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "edit.json takes optional notes: a suggestion for a trim or a spoken sentence, starting with 建議 and giving a reason with 。因為"`.

### Task 3: Suggestion texts for the page (`review_text.py`)

**Files:**
- Create: `pipeline/review_text.py`
- Modify: `pipeline/trim.py` (pause reason helpers)
- Test: `tests/test_review_text.py`, `tests/test_trim.py`

**Interfaces:**
- Produces: `trim.pause_reason(gap: float, left: float) -> str` (the existing `f"pause {gap:.2f}s -> {left:.2f}s"`, now used by `_pauses`); `trim.pause_lengths(reason: str) -> tuple[float, float] | None`.
- Produces: `review_text.trim_label(trim: dict) -> str`; `review_text.default_note(trim: dict) -> str`; `review_text.ASR_NOTE: str`; `review_text.LISTEN_NOTE: str`; `review_text.suggested_cut(note: str) -> bool | None`; `review_text.REMOVED_REASON = "在審閱頁拿掉"` (the `cuts.extra` reason of a sentence removed in the page; Tasks 5 and 6 use it).

Exact copy (spec §6.3, §6.5):

| kind | `trim_label` | `default_note` |
|---|---|---|
| `pause` | 停頓 | `建議剪掉。因為這是 {gap:.1f} 秒的停頓，剪成 {left:.1f} 秒。` |
| `filler` | 贅字 | `建議剪掉。因為「{text}」是贅字。` |
| `repeat` | 重複 | `建議剪掉。因為「{text}」是重複的字。` |
| `retake`, suggest `cut` | NG | `建議剪掉。因為這一段講錯了，後面有重講的一次。` |
| `retake`, suggest `list` | 改詞重講 | `建議剪掉。因為後面有照腳本講的一次；這一次有腳本沒有的內容的話，就選保留。` |
| `extra` | 腳本外的話 | `建議保留。因為這是腳本外的話，臨場的話常是自然的口語。` |

`ASR_NOTE = "建議聽一下這一句。因為字幕用的是辨識出來的字，可能有錯字。"`, `LISTEN_NOTE = "建議聽一下前後。因為剪接處不在靜音裡，可能切到字。"` (Ruling 11). `suggested_cut`: True for a note starting `建議剪掉`, False for `建議保留`, else None.

- [ ] **Step 1: Write the failing tests**

```python
def test_pause_reason_round_trips():                      # tests/test_trim.py
    assert pause_lengths(pause_reason(0.8, 0.37)) == (0.8, 0.37)
    assert pause_lengths("filler 嗯") is None

@pytest.mark.parametrize("trim, label, note", [...one row per line of the table above, text 「嗯」/「我我」,
    pause reason pause_reason(0.82, 0.37) giving "建議剪掉。因為這是 0.8 秒的停頓，剪成 0.4 秒。"...])
def test_label_and_default_note_by_kind(trim, label, note):
    assert trim_label(trim) == label and default_note(trim) == note

def test_every_default_note_has_the_note_shape():          # passes edit's own rule
    for trim in ALL_KINDS: assert default_note(trim).startswith("建議") and "。因為" in default_note(trim)

@pytest.mark.parametrize("note, cut", [("建議剪掉。因為…", True), ("建議保留。因為…", False), ("建議聽一下。因為…", None)])
def test_suggested_cut_reads_the_start_of_the_note(note, cut):
    assert suggested_cut(note) is cut
```


- [ ] **Step 2:** Run `uv run pytest tests/test_review_text.py tests/test_trim.py -k "pause_reason or label or note or suggested" -v` — FAIL (import errors).
- [ ] **Step 3:** Add `pause_reason`/`pause_lengths` to `trim.py` next to `_pauses` (which now calls `pause_reason`); write `review_text.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_review_text.py tests/test_trim.py -v` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "review_text gives each candidate kind its Chinese name and a default suggestion; trim builds and reads the pause reason in one place"`.

### Task 4: Review video (`review_video.py`)

**Files:**
- Create: `pipeline/review_video.py`
- Test: `tests/test_review_video.py`

**Interfaces:**
- Produces: `REVIEW_W, REVIEW_H = 540, 960`; `REVIEW_FPS = 30`; `REVIEW_GOP_FRAMES = 15`; `REVIEW_VIDEO_BITRATE = "1500k"`; `REVIEW_AUDIO_BITRATE = "96k"`; `review_video_path(p: Project) -> Path` (`p.work / "review.mp4"`); `review_video_args(video_src: Path, audio: Path, out: Path) -> list[str]`; `make_review_video(p: Project, force: bool = False, runner: Callable = run_ffmpeg) -> dict` returning `{"video": str, "skipped": bool}`.

`review_video_args` (spec §5.2): inputs `video_src` (probe.json `video_source`, the file the render uses) and `audio` (`work/audio48k.wav`); `-map 0:v:0 -map 1:a:0`; video filter `setpts=PTS-STARTPTS,fps=30,scale=540:960:force_original_aspect_ratio=increase,crop=540:960,format=yuv420p`; `-c:v` `render.VIDEO_ENCODER`, `-b:v 1500k`, `-g 15`, `-force_key_frames expr:gte(t,n_forced*0.5)`; `-c:a aac -b:a 96k -ac 2 -ar 48000`; `-movflags +faststart`; `-y -hide_banner -loglevel error`. `make_review_video` runs it through `run_step(p, "review_video", {"video": ..., "audio": ..., "probe": ...}, [path], ...)`, writing `work/review.part.mp4` and `os.replace`-ing it onto `review.mp4`; the part file is unlinked in a `finally`.

- [ ] **Step 1: Write the failing tests** (a project from `helpers.edited_project_from_script(tmp_path, "今天來聊三個小技巧。第一是把亮度調低。")`, whose prep ran on a gray-counter clip: frame N has gray `16 + 4 * (N % 50)`)

```python
def test_review_video_is_540x960_at_30_fps_with_sound(tmp_path): ...  # ffprobe: width, height, r_frame_rate "30/1", an aac stream
def test_a_key_frame_at_least_every_half_second(tmp_path): ...        # ffprobe -skip_frame nokey frame times: gaps <= 0.5 + 1/30
def test_frame_n_of_the_review_video_is_frame_n_of_the_source(tmp_path):
    # media_fixtures.frame_luma(review, n) is within 3 of 16 + 4 * (n % 50) for n in (0, 45, 77)
def test_review_video_lasts_as_long_as_the_source_video(tmp_path): ...  # within 1/30 + 0.02 s of probe.json duration_s
def test_second_run_is_skipped_and_force_remakes_it(tmp_path): ...
def test_no_part_file_is_left_when_ffmpeg_fails(tmp_path): ...  # runner raising PipelineError; review.part.mp4 absent, error raised
```

- [ ] **Step 2:** Run `uv run pytest tests/test_review_video.py -v` — FAIL (module missing).
- [ ] **Step 3:** Implement `pipeline/review_video.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_review_video.py -v` — PASS. If the key-frame test fails with the hardware encoder, keep `-force_key_frames` and report it; do not switch encoders without a ruling.
- [ ] **Step 5:** Commit: `git commit -m "review_video makes work/review.mp4: the render's picture cropped to 540x960 at 30 fps with a key frame every half second, and audio48k.wav as its sound, so its times are the transcript's"`.

### Task 5: What the page shows (`review_data.py`)

**Files:**
- Create: `pipeline/review_data.py`
- Test: `tests/test_review_data.py`

**Interfaces:**
- Consumes: `review_text.*` (Task 3), `edit.build_segments/map_time/total_duration/kept_sentences/require_valid`, `project.edit_stage/edit_file_hashes`, `captions.build_events`, `review_items.review_items/trims_in_reel`, `storyboard.clock`, `style.load_style/font_path`, `captions.caption_top_px`.
- Produces: `class NotReady(PipelineError)` with `NOT_READY_TEXT = "Claude 正在更新剪接設定，請稍候。"`; `edit_version(p: Project) -> str` (the sha256 of `json.dumps([edit, transcript, trim])` over the three hashes of `project.edit_file_hashes(p)`, `null` for a missing file: a rerun of `transcribe` or `trim` is a new version even when `edit.json` is unchanged); `page_data(p: Project) -> dict`; `sentence_tokens(sentence: dict, trims: list[dict]) -> list[dict]`.

`page_data` raises `NotReady` when `edit_stage(p)` is not None or `require_valid` raises. Its result (JSON-serialisable; every time in seconds, rounded to 3 places):

```
project, version, mode ("line"|"word"), source_s, duration_s,
segments: [[start, end], ...]                      # source time
captions: [{start, end, pieces: [{text, t}]}]       # reel time; t null for punctuation
caption_style: {size_px, color, highlight, outline_color, outline_px, bold, top_px, canvas_w, canvas_h}
sentences: [{id, state ("kept"|"removed"|"unspoken"), time ("m:ss.s", "" unless kept),
             start, end, tokens, asr (bool), text, script, note (str|null),
             removal: null | {reason, by_page (reason == review_text.REMOVED_REASON)}}]
trims: [{id, label, text, start, end, time, cut, in_reel, decision (review_items made a reworded or extra decision of it),
         listen (needs_listen and cut and in reel), number (1.. over decisions in time order, else null),
         note (edit.notes[id] or default_note), suggest_cut, listen_note (LISTEN_NOTE or null)}]
nav: {decisions: [trim id...], listen: [{trim|sentence, start}...], asr: [sentence id...]}
```

`note` of an asr sentence is `edit["notes"][id]` or `ASR_NOTE`. `nav.listen` holds review_items' listen items (cut points, accepted candidates not in silence, low-match sentences) in their order; `nav.asr` the asr_text decisions; `nav.decisions` the reworded and extra decisions (spec §6.1 counts them apart from the typo sentences, so the page numbers them 1..N itself).

`sentence_tokens` (spec §6.2): the sentence text as tokens in order — `{"k": "unit", "text", "start", "end", "trim": id|None}` per unit span, `{"k": "char", "text"}` per character outside every unit — and each of `trims` owned by the sentence placed by time: a candidate whose span covers whole units (unit start ≥ trim start − 0.001 and unit end ≤ trim end + 0.001) sets `"trim"` on those units; any other is inserted as `{"k": "trim", "trim": id, "text": trim text}` (a pause as `{"k": "pause", "trim": id}`) before the first unit that starts at or after the candidate's start, or at the end.

- [ ] **Step 1: Write the failing tests** with documents built by `helpers.transcript_and_trims` / `timed_sentence` (made-up sentences), written into a project with `helpers.edited_project_from_documents`:

```python
def test_page_data_refuses_until_edit_json_passed_validate(tmp_path): ...   # write edit.json without validate → NotReady, message NOT_READY_TEXT
def test_segments_and_duration_are_build_segments(tmp_path): ...
def test_captions_are_build_events_on_the_reel(tmp_path): ...               # same start/end/text as build_events
def test_a_filler_is_inserted_where_it_was_said(): ...                      # script sentence, filler between units 2 and 3
def test_a_candidate_over_recognised_units_marks_them(): ...                # asr sentence: units inside the span carry the trim id
def test_a_pause_becomes_a_pause_token(): ...
def test_sentence_times_are_in_reel_clock_and_removed_ones_say_why(tmp_path): ...  # removal.reason == the cuts.extra reason
def test_decisions_are_numbered_from_one_in_time_order(tmp_path): ...
def test_a_note_in_edit_json_wins_over_the_default(tmp_path): ...
def test_nothing_shown_holds_an_english_reason(tmp_path):
    data = page_data(p); shown = [t["label"] for t in data["trims"]] + [t["note"] for t in data["trims"]]
    assert not any(re.search(r"[A-Za-z]{3,}", s) for s in shown)
```

- [ ] **Step 2:** Run `uv run pytest tests/test_review_data.py -v` — FAIL.
- [ ] **Step 3:** Implement `pipeline/review_data.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_review_data.py -v` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "review_data gives the review page the segments, the captions on the reel, the transcript with each candidate where it was said, and a Chinese suggestion for each item"`.

### Task 6: Changes from the page (`review_actions.py`)

**Files:**
- Create: `pipeline/review_actions.py`
- Test: `tests/test_review_actions.py`

**Interfaces:**
- Consumes: `review_data.edit_version`, `review_data.NotReady`, `review_text.REMOVED_REASON`, `edit.validate_edit/validate_project/require_valid/build_segments`, `review_items.trims_in_reel`, `project.edit_stage/file_sha256`.
- Produces:
  - `UNDO_FILE = "review-undo.json"`, `DEFAULT_SEM, DEFAULT_WEIGHT = "claim", "main"`;
  - `CONFLICT_TEXT = "決定剛被更新過，請再看一次。"`, `NOTHING_LEFT_TEXT = "至少要留下一句，這個改動沒有存。"`, `REFUSED_TEXT = "這個改動會讓剪接設定出錯，沒有存。請回對話請 Claude 看看。"`;
  - `class VersionConflict(PipelineError)` (message `CONFLICT_TEXT`), `class ChangeRefused(PipelineError)` (message is Chinese text for the page);
  - `write_edit(p: Project, edit: dict) -> None`;
  - `set_cut(edit: dict, trim_id: str, cut: bool) -> dict`;
  - `remove_sentence(edit: dict, transcript: dict, trims: dict, sid: str) -> tuple[dict, dict]` → (new edit, undo entry `{"sentences": [entry], "accepted": [trim ids]}`);
  - `restore_sentence(edit: dict, transcript: dict, sid: str, undo: dict) -> dict` (`undo` maps sentence id → undo entry);
  - `apply_change(p: Project, version: str, change: dict) -> dict` → `{"version": new version}`.

Rules (spec §5.4): a change is `{"type": "cut", "trim", "cut": bool}`, `{"type": "remove", "sentence"}` or `{"type": "restore", "sentence"}` (Task 9 adds `"caption"`). `apply_change` refuses with `NotReady` when `edit_stage(p)` is not None, with `VersionConflict` when `version != edit_version(p)`, builds the new edit with the functions above (never mutating the loaded one), and when `validate_edit` returns errors raises `ChangeRefused(NOTHING_LEFT_TEXT)` if any error contains `nothing is left`, else `ChangeRefused(REFUSED_TEXT)`, and appends the English errors to `work/review.log`. Otherwise `write_edit`, `validate_project(p)`, and for remove/restore update `work/review-undo.json`: `{"transcript": sha256, "trim": sha256, "removed": {sid: entry}}`; a file whose two hashes differ from the current files is treated as empty. `remove_sentence` adds `{"from": sid, "to": sid, "reason": review_text.REMOVED_REASON}` to `cuts.extra`, drops the sentence's `sentences` entry, and moves from `reject` to `accept` every candidate with `sentence == sid`, kind `extra` or `retake`, still in the reel (`trims_in_reel` of the new edit). `restore_sentence` drops the `cuts.extra` item whose range holds `sid` (all of it), adds a `sentences` entry for each spoken sentence of the range (the undo entry's, else `{"id", "sem": "claim", "weight": "main"}`), keeps `sentences` in script order, and moves each undo `accepted` id that is still in `accept` back to `reject`. Removing a sentence that is not kept, or restoring one that is not removed, raises `ChangeRefused(REFUSED_TEXT)`.

- [ ] **Step 1: Write the failing tests** (a project from `edited_project_from_documents` with a made-up three-sentence script, an extra-speech candidate before sentence 2, and a reject-by-default extra candidate):

```python
def test_toggling_a_cut_writes_edit_json_and_records_validate(tmp_path):
    out = apply_change(p, edit_version(p), {"type": "cut", "trim": tid, "cut": False})
    assert tid in load_edit(p.edit_path)["cuts"]["reject"] and out["version"] == edit_version(p)
    assert edit_stage(p) is None
def test_a_stale_version_changes_nothing(tmp_path): ...            # VersionConflict, file bytes unchanged
def test_removing_the_last_kept_sentence_is_refused_in_chinese(tmp_path): ...  # NOTHING_LEFT_TEXT, file unchanged, review.log has the English error
def test_any_other_invalid_change_is_refused_in_chinese(tmp_path): ...         # unknown trim id → REFUSED_TEXT
def test_removing_a_sentence_also_cuts_the_extra_speech_before_it(tmp_path): ...
def test_restore_puts_back_the_labels_and_the_decisions(tmp_path): ...        # sem/weight as before, the extra back in reject
def test_restore_without_a_record_uses_claim_and_main(tmp_path): ...           # a cuts.extra Claude wrote
def test_restore_brings_back_the_whole_range(tmp_path): ...                    # from s1 to s2 in one item
def test_an_undo_record_of_an_older_transcript_is_not_used(tmp_path): ...     # change transcript.json bytes → defaults
def test_changes_are_refused_while_edit_json_is_not_validated(tmp_path): ...  # NotReady
def test_write_edit_leaves_no_temp_file(tmp_path): ...
```

- [ ] **Step 2:** Run `uv run pytest tests/test_review_actions.py -v` — FAIL.
- [ ] **Step 3:** Implement `pipeline/review_actions.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_review_actions.py -v` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "review_actions applies a change from the review page: it checks the version, validates the new edit in memory, refuses in Chinese, writes edit.json atomically and keeps what a removed sentence needs to come back"`.

### Task 7: The review server (`review_server.py`): key, routes, ranges, data and changes

**Files:**
- Create: `pipeline/review_server.py`, `pipeline/review_page/index.html` (a placeholder page with `<title>審閱頁</title>`; Task 10 fills it)
- Test: `tests/test_review_server.py`

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces: `REVIEW_IDLE_S = 1800`; `PAGE_DIR = Path(__file__).parent / "review_page"`; `DENIED_TEXT = "審閱頁的程式已經停了，請回對話請 Claude 重新打開。"`; `class ReviewServer` with `__init__(self, p: Project, *, key: str | None = None, idle_s: float = REVIEW_IDLE_S)` (Task 8 adds the keyword arguments `steps` and `reveal`), `start(self) -> str` (returns the URL `http://127.0.0.1:<port>/?key=<key>`), `port: int`, `url: str`, `wait(self) -> str` (blocks; returns `"idle"` or `"stopped"`), `stop(self) -> None`.

Routes (all JSON responses `application/json; charset=utf-8`, every response `Cache-Control: no-store`):

| route | answer |
|---|---|
| `GET /?key=K` | K right: `Set-Cookie: review_key_<port>=K; HttpOnly; SameSite=Strict; Path=/`, 303 to `/`. Wrong: 403 |
| `GET /` (cookie) | `review_page/index.html` |
| `GET /review.js`, `/logic.js`, `/review.css` | those files of `PAGE_DIR` (fixed names; anything else 404) |
| `GET /font/caption.ttf` | the style's caption font (`font_path(style, "bold" if bold else "regular")`) |
| `GET /media/review.mp4`, `/media/preview.mp4` | `work/review.mp4`, `out/preview.mp4`, with ranges |
| `GET /api/data` | `page_data` + `"state"` (Task 8); `NotReady` → 409 `{"error": NOT_READY_TEXT}` |
| `GET /api/state` | `{"version", "state", ...}` (Task 8 fills the states; here `"editing"`) |
| `POST /api/change` | body `{"version", "change"}` → 200 `{"version"}`; 409 `{"error", "conflict": true}`; 409 NotReady; 422 `{"error"}` for `ChangeRefused` |

Every request without the cookie (except `GET /?key=`) gets 403 `{"error": DENIED_TEXT}`; a `Host` other than `127.0.0.1:<port>` gets 403 before anything else. A POST needs `Content-Type: application/json` and a body of at most 65536 bytes (else 400). Range: `bytes=a-b`, `bytes=a-`, `bytes=-n` → 206 with `Content-Range`, `Accept-Ranges: bytes`; a start at or past the size → 416 with `Content-Range: bytes */size`; no Range → 200 with the whole file and `Accept-Ranges`. One `threading.Lock` serialises `apply_change` calls. The server is `ThreadingHTTPServer(("127.0.0.1", 0), handler)` with `daemon_threads = True`; request logging goes to `work/review.log`, not stderr.

- [ ] **Step 1: Write the failing tests** (a `ReviewServer` on an edited project from `edited_project_from_script`, after `validate_project` and `make_review_video`; requests with `http.client` so the Host header can be set; the server stopped in a fixture finaliser):

```python
def test_the_key_sets_a_cookie_and_redirects(tmp_path): ...       # 303, Location "/", cookie HttpOnly SameSite=Strict
def test_no_cookie_and_a_wrong_key_are_refused(tmp_path): ...     # 403, error == DENIED_TEXT
def test_another_host_name_is_refused(tmp_path): ...              # Host: localhost:<port> and evil.test → 403
def test_unknown_paths_are_404_and_reach_no_file(tmp_path): ...   # /../state.json, /review_page/x, /media/../edit.json
@pytest.mark.parametrize("header, status, first, last", [
    ("bytes=0-1", 206, 0, 1), ("bytes=1000-", 206, 1000, SIZE - 1), ("bytes=-500", 206, SIZE - 500, SIZE - 1)])
def test_ranges_of_the_review_video(tmp_path, header, status, first, last): ...  # body == file bytes[first:last+1]
def test_a_range_past_the_end_is_416(tmp_path): ...
def test_data_is_page_data_with_the_state(tmp_path): ...
def test_data_while_edit_json_is_not_validated_is_409_in_chinese(tmp_path): ...
def test_a_change_round_trip(tmp_path): ...                       # 200, new version, /api/data shows it
def test_a_change_with_an_old_version_is_409_conflict(tmp_path): ...
def test_a_refused_change_is_422_in_chinese(tmp_path): ...
def test_a_post_that_is_not_json_is_400(tmp_path): ...
```

- [ ] **Step 2:** Run `uv run pytest tests/test_review_server.py -v` — FAIL.
- [ ] **Step 3:** Implement routes, key, Host check and ranges in `pipeline/review_server.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_review_server.py -v` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "review_server serves the review page on 127.0.0.1 behind a random key: fixed routes, byte ranges for the video, the page data and changes"`.

### Task 8: Approval from the page, states, idle stop and the `review` command

**Files:**
- Modify: `pipeline/review_server.py`, `pipeline/cli.py`
- Test: `tests/test_review_server.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `storyboard.build_storyboard`, `project.approve_gate/gate_ok/step_done`, `render.render`, `assets.MIN_FREE_GB`, `review_video.make_review_video`, `edit.require_valid`, `storyboard.OLDER_FILES_REFUSAL/NOT_VALIDATED_REFUSAL`.
- Produces: `@dataclass class Steps: storyboard: Callable = build_storyboard; approve: Callable = approve_gate; render: Callable = render`; `ReviewServer.__init__` gains `steps: Steps | None = None` and `reveal: Callable[[Path], None] = reveal_in_folder`; `reveal_in_folder(path: Path) -> None` (`open -R` on darwin, `explorer /select,<path>` on win32, `xdg-open <folder>` elsewhere; `subprocess.run(..., check=False)`); texts `DISK_TEXT = "磁碟可用空間不到 15 GB，請先清出空間再輸出。"`, `BUSY_TEXT = "正在輸出，不能改決定。"`, `APPROVED_TEXT = "已經同意了。要再改，請回對話跟 Claude 說。"`, `FAILED_TEXT = "輸出失敗。請回對話請 Claude 看記錄檔。"`; `serve_review(p: Project, *, open_browser: Callable[[str], object], announce: Callable[[dict], None], server_factory=ReviewServer) -> dict`; CLI command `review` with `name` and `--no-browser`.

States of `GET /api/state` → `{"version", "state", "step", "error", "outputs"}`:
- `"working"` while the approval job runs, `"step"` one of `"storyboard"`, `"approve"`, `"render"`;
- `"failed"` after the job failed, `"error"` = `FAILED_TEXT` (the English message goes to `work/review.log`); a later successful change or approval clears it;
- with no job: `"done"` when `gate_ok(p, "gate1")` and `step_done(p, "render")` and `out/final.mp4` exists (`"outputs": {"final": path, "preview": path or None, "folder": out path}`); `"approved"` when `gate_ok(p, "gate1")` only (the user approved in the chat; Claude is rendering); else `"editing"`.

`POST /api/approve` body `{"version"}`: 409 conflict when the version is stale; 423 `BUSY_TEXT` when a job runs, `APPROVED_TEXT` when the state is `approved` or `done`; 507 `DISK_TEXT` when free space < `MIN_FREE_GB` GiB; else 202 and a non-daemon thread runs `steps.storyboard(p)`, `steps.approve(p, "gate1")`, `steps.render(p)` in that order, holding the change lock, so no change lands meanwhile. `POST /api/change` answers 423 with `BUSY_TEXT` / `APPROVED_TEXT` in those states. `POST /api/reveal` calls `reveal(out/final.mp4)` when it exists → 204, else 404. `wait()` returns `"idle"` once no request came for `idle_s` and no job runs.

`serve_review` refuses like the storyboard does (`require_valid`; `edit_stage` "edit" → `OLDER_FILES_REFUSAL`, "validate" → `NOT_VALIDATED_REFUSAL`), runs `make_review_video(p)`, starts the server, calls `announce({"url", "port", "video"})` then `open_browser(url)`, and returns `{"stopped": server.wait()}`. The CLI handler passes `announce=lambda info: (_emit({"ok": True, "serving": True, **info}), sys.stdout.flush())` and `open_browser=webbrowser.open` unless `--no-browser`, installs a SIGTERM handler that calls `server.stop()` (and puts the previous handler back before it returns), and returns the result (a second JSON line). A running approval thread is non-daemon, so a stopped server still finishes its render before the process exits.

- [ ] **Step 1: Write the failing tests** (fake `Steps` that record calls and can raise; `idle_s` small; `shutil.disk_usage` monkeypatched for the disk case):

```python
def test_approve_runs_storyboard_approve_render_in_order(tmp_path): ...       # 202; the fakes ran once each, in order, with (p), (p, "gate1"), (p); then state is not "working"
def test_approve_with_an_old_version_runs_nothing(tmp_path): ...              # 409, fakes not called
def test_approve_without_free_space_runs_nothing(tmp_path, monkeypatch): ...  # 507 DISK_TEXT
def test_changes_wait_for_the_job_and_are_refused_while_it_runs(tmp_path): ...  # fake render blocks on an Event; change → 423 BUSY_TEXT
def test_a_failed_render_is_failed_in_chinese_and_logged(tmp_path): ...       # state failed, error FAILED_TEXT, review.log has the message
def test_an_approval_in_the_chat_shows_as_approved(tmp_path): ...             # real build_storyboard + approve_gate on the project; state approved; change → 423 APPROVED_TEXT
def test_done_lists_the_outputs_and_reveal_calls_the_injected_function(tmp_path): ...
def test_the_server_stops_when_idle_and_not_while_a_job_runs(tmp_path): ...
def test_serve_review_refuses_an_unvalidated_edit_like_the_storyboard(tmp_path): ...
def test_review_command_announces_one_json_line_first_and_never_opens_a_browser_with_no_browser(tmp_path, capsys, monkeypatch): ...
    # server_factory returns a server whose wait() returns "idle" at once; webbrowser.open monkeypatched to fail the test if called
def test_one_real_approval_renders_the_project(edited_project): ...  # real Steps and real ffmpeg on the short Meijia project: final.mp4 exists and state is done
```

- [ ] **Step 2:** Run `uv run pytest tests/test_review_server.py tests/test_cli.py -v` — FAIL.
- [ ] **Step 3:** Implement the job, states, idle wait, `/api/approve`, `/api/reveal`, `serve_review` and the CLI command.
- [ ] **Step 4:** Run `uv run pytest tests/test_review_server.py tests/test_cli.py -v` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "The review page approves and renders: storyboard, approve and render run in a background job with the change lock held, refusing without 15 GB free; review shows a chat approval, stops when idle, and pipeline review starts it"`.

### Task 9: Static page clips play with Web Audio, and show the notes

**Files:**
- Modify: `pipeline/storyboard.py` (`_audio`, `_item_html`, `_list_md`, `_html` adds one script, `_CSS`)
- Test: `tests/test_storyboard.py`

**Interfaces:**
- Consumes: `edit["notes"]` (Task 2).
- Produces: `storyboard.SILENT_HINT = "沒有聲音的話，請關掉靜音模式"`.

Spec §9.2: `_audio(uri)` becomes `<button type="button" class="play" data-clip="{uri}">▶ 播放</button><span class="hint">沒有聲音的話，請關掉靜音模式</span>`. One inline `<script>` at the end of `<body>`: on a click, create (once) an `AudioContext`, `resume()` it, base64-decode the `data-clip` payload, `decodeAudioData`, play it through a `BufferSource`; while playing, that button reads `■ 停止` and a click stops it; a decode error sets the button text to `播不出來`. This is the method the iPhone test proved (spec §9.1). A list item whose candidate (`item.trim`) or sentence (`asr_text`) has a note in `edit["notes"]` shows it as `<p class="note">{note}</p>` after its details; `storyboard.md` adds the same note as an indented line under the item.

- [ ] **Step 1: Write the failing tests** (rewrite the existing `<audio` assertions of `test_storyboard.py` in terms of `class="play"` — counts, one clip per item and per candidate row, none in sentence rows — keeping what each test checked; then add):

```python
def test_the_page_has_no_audio_element_and_one_player_script(tmp_path):
    html = page(p); assert "<audio" not in html and html.count("<script>") == 1 and "decodeAudioData" in html
def test_every_play_button_has_the_silent_mode_hint(tmp_path):
    assert html.count('class="play"') == html.count(SILENT_HINT) > 0
def test_a_note_in_edit_json_shows_under_its_item_on_the_page_and_in_the_markdown(tmp_path): ...
```

- [ ] **Step 2:** Run `uv run pytest tests/test_storyboard.py -v` — the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest tests/test_storyboard.py -v` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "The storyboard page plays its clips by decoding them in the page, which an iPhone's file preview allows, says to turn silent mode off, and shows Claude's notes under the items"`.

### Task 10: The review page

**Files:**
- Create: `pipeline/review_page/index.html`, `pipeline/review_page/review.css`, `pipeline/review_page/review.js`, `pipeline/review_page/logic.js`
- Test: `tests/js/logic.test.mjs`, `tests/test_review_page.py`

**Interfaces:**
- Consumes: the routes of Tasks 7–8 and the `page_data` shape of Task 5.
- Produces: `logic.js` ES module exports `clock(seconds)`, `reelTime(t, segments)`, `sourceTime(r, segments)`, `skipTarget(t, segments)`, `captionAt(r, captions)`, `litPieces(caption, r)`.

Follow the approved mockup (stage 1, and the popover). Build spec §6 as written: top bar (project name, `原長 → 新長`, `剪掉 N 段`, button `同意，輸出成品`); navigation bar (`要你決定 N 項（還沒看 M 項）`, `‹ 上一項`, `下一項 ›`, `建議聽一下 N 處`, `字幕可能有錯字 N 句`); left the 9:16 video with the captions drawn over it in the served font (`/font/caption.ttf` via `@font-face`, sizes scaled from `caption_style` to the player's width, `top_px` from the top), `▶ 播放`, the in-reel clock, `0.5×`, and a timeline of kept / cut / decision colours; right the transcript (each sentence prefixed by its in-reel time; click a word to play from there; the playing sentence highlighted); the marks and popovers of spec §6.2–§6.3 (buttons `剪掉` / `保留` with `（建議）` on the one `suggest_cut` names and the chosen one filled, `▶ 播這一段前後`; the typo card with `拿掉這一句`, `保留（錯字會留在成品裡）`, `▶ 播這一句` and the line `要改字或重錄：回到 Claude 對話說「1:48 那句我要改」。` with the sentence's own time); the `⋯` menu (`拿掉這一句`, `從這裡播放`); `拿掉了這一句　復原`; the confirm dialog of §6.4 (`回去再看看`, `確定，輸出成品`); the working view with the current step (`正在重做分鏡稿`, `正在記下同意`, `正在輸出成品`), the approved view (`已經在對話裡同意了。輸出的進度請看對話；輸出完成後，這裡會播預覽版。`), and the done view (plays `/media/preview.mp4`, shows both paths, `打開所在的資料夾` → `POST /api/reveal`); footer `想用講的：回到 Claude 對話說「1:15 那段保留」也可以`. A popover item counts as seen once opened (kept in `sessionStorage`; reading and writing wrapped in try/catch).

Playback: on every `requestVideoFrameCallback` (fallback `timeupdate`) call `skipTarget(currentTime, segments)`; when it returns a number, set `currentTime` to it; when it returns `-1` (past the last segment), pause. Use whichever trigger Task 1 found more precise. Polling: `GET /api/state` every 3000 ms; a new `version` that this page did not cause reloads `/api/data` and shows `Claude 改了剪接設定，頁面已更新。` for 4 s; a failed fetch or a 403 shows `DENIED_TEXT` in a banner and stops polling. A 409/422/423 from a change shows its `error`. No id and no English reaches the DOM text.

- [ ] **Step 1: Write the failing tests**

```js
// tests/js/logic.test.mjs  (node:test, node:assert/strict; imports ../../pipeline/review_page/logic.js)
test("clock rounds to the tenth like storyboard.clock", () => { assert.equal(clock(83.25), "1:23.3"); assert.equal(clock(0), "0:00.0"); });
test("reelTime maps like edit.map_time", () => { const s = [[1, 3], [5, 6]]; assert.equal(reelTime(0.5, s), 0); assert.equal(reelTime(2, s), 1); assert.equal(reelTime(4, s), 2); assert.equal(reelTime(5.5, s), 2.5); assert.equal(reelTime(9, s), 3); });
test("sourceTime inverts reelTime", () => { const s = [[1, 3], [5, 6]]; assert.equal(sourceTime(2.5, s), 5.5); assert.equal(sourceTime(0, s), 1); });
test("skipTarget jumps over a cut, stays inside a segment, ends after the last", () => { const s = [[1, 3], [5, 6]]; assert.equal(skipTarget(0.2, s), 1); assert.equal(skipTarget(2, s), null); assert.equal(skipTarget(3.01, s), 5); assert.equal(skipTarget(6.0, s), -1); });
test("captionAt finds the caption on screen at a reel time", () => { ... });
test("litPieces counts the pieces already spoken in word mode", () => { ... });
```

```python
# tests/test_review_page.py
def test_logic_js_passes_its_node_tests(): ...   # pytest.skip when shutil.which("node") is None; subprocess.run(["node", "--test", "tests/js/"]) returncode 0
def test_the_page_loads_nothing_from_the_network():  # no "http://" or "https://" in index.html, review.css, review.js, logic.js
def test_the_page_is_light_and_in_chinese():        # "color-scheme: light" in review.css; html lang="zh-Hant"; the fixed strings above appear in index.html or review.js
def test_the_page_text_has_no_ids(): ...            # no match of r"(?<![A-Za-z0-9])[st]\d{2}(?!\d)" inside quoted strings of review.js / index.html
```

- [ ] **Step 2:** Run `uv run pytest tests/test_review_page.py -v` — FAIL.
- [ ] **Step 3:** Implement the four files.
- [ ] **Step 4:** Run `uv run pytest tests/test_review_page.py -v` — PASS; then start `uv run pipeline review <name> --no-browser` on the project the test helpers make (or Task 12's project) and open it in Chrome once to see that it loads without console errors (record what you saw in the report; the full walk-through is Task 12).
- [ ] **Step 5:** Commit: `git commit -m "The review page: the reel plays with the cuts skipped and the captions drawn over it, the transcript carries every cut and decision, and the page toggles, removes, restores and approves through the server"`.

### Task 11: Caption text in edit.json (stage 2, data)

**Files:**
- Modify: `pipeline/edit.py`, `pipeline/captions.py`, `pipeline/review_items.py`
- Test: `tests/test_edit.py`, `tests/test_captions.py`, `tests/test_review_items.py`, `tests/test_storyboard.py`

**Interfaces:**
- Produces: `edit["captions"]["text"]`: `{spoken sentence id: str}` (optional); `captions.remap_units(old: str, units: list[dict], new: str) -> list[dict]`; `captions.caption_source(sentence: dict, edit: dict) -> tuple[str, list[dict]]`.

Validate (spec §7.2): `captions` takes optional `text`, an object; each key a spoken sentence id (`captions.text.<id>: not a spoken sentence`); each value a string (`must be a string, got …`) that is not empty after `strip()` and holds no line break of `captions._LINE_BREAKS` (`captions.text.<id>: must not be empty` / `must be one line`). A removed sentence may keep its text.

`remap_units` (spec §7.2): `difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()`. Each character of `new` gets an owner unit: `equal` → the unit whose span holds the old character; `replace` (old `i1:i2`, new `j1:j2`) → the unit of old character `i1 + (k * (i2 - i1)) // (j2 - j1)` for new character `j1 + k`; `insert` → none yet. Then a character whose `_kind(new, j)` is not `"other"` (space or punctuation) and that is not between two characters of one unit has no owner; every other ownerless character takes the owner of the nearest owned character before it, or after it at the head. Each unit's new span is `[first, last + 1)` of its characters; a unit with none is dropped; the result keeps each unit's other fields (`start`, `end`, `src`) and sets `text` and `span`. `caption_source` returns `(sentence["text"], sentence["units"])` without an override, else `(override, remap_units(sentence["text"], sentence["units"], override))`. `build_events` uses `caption_source` for both `break_lines` and `_row`, so the storyboard, the render and the review page all get the edited text. `review_items` makes no `asr_text` decision for a sentence with an override (the user has fixed it, spec §7.1).

- [ ] **Step 1: Write the failing tests**

```python
U = units_of("大概多成兩小時", [(0, 2), (2, 3), (3, 4), (4, 7)])   # 大概 / 多 / 成 / 兩小時, made-up times
def test_a_replaced_character_keeps_its_unit_and_time(): r = remap_units("大概多成兩小時", U, "大概多撐兩小時"); assert [u["text"] for u in r] == ["大概", "多", "撐", "兩小時"] and times(r) == times(U)
def test_an_inserted_character_joins_the_unit_before_it(): ... "大概多撐了兩小時" → "撐了"
def test_a_character_inserted_at_the_head_joins_the_first_unit(): ... "喔大概…" → "喔大概"
def test_a_unit_whose_characters_are_all_deleted_is_dropped(): ... "大概兩小時" → no 多/成 units
def test_inserted_punctuation_belongs_to_no_unit(): ... "大概，多撐兩小時" → "，" outside every span
def test_a_longer_replacement_is_spread_over_the_old_units(): ...
def test_validate_takes_caption_text_for_spoken_sentences_only(T, TR): ...
@pytest.mark.parametrize("text, error", [("  ", "must not be empty"), ("一行\n兩行", "must be one line"), (3, "must be a string, got a number")])
def test_validate_refuses_bad_caption_text(T, TR, text, error): ...
def test_build_events_shows_the_edited_text_at_the_spoken_times(T, TR): ...
def test_a_fixed_recognised_sentence_is_no_longer_a_decision(T, TR): ...
def test_the_storyboard_shows_the_edited_caption(tmp_path): ...
```

- [ ] **Step 2:** Run `uv run pytest tests/test_captions.py tests/test_edit.py tests/test_review_items.py tests/test_storyboard.py -k "remap or caption_text or edited or fixed" -v` — FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q` — PASS (whole suite: build_events feeds the render and the storyboard).
- [ ] **Step 5:** Commit: `git commit -m "edit.json captions.text replaces a sentence's caption text; the new characters take the times of the units they came from, so the storyboard, the render and the review page show it on time"`.

### Task 12: Caption editing in the page (stage 2, server and page)

**Files:**
- Modify: `pipeline/review_actions.py`, `pipeline/review_data.py`, `pipeline/review_server.py`, `pipeline/review_page/review.js`, `pipeline/review_page/review.css`
- Test: `tests/test_review_actions.py`, `tests/test_review_data.py`, `tests/test_review_server.py`, `tests/test_review_page.py`

**Interfaces:**
- Consumes: Task 11.
- Produces: change `{"type": "caption", "sentence", "text": str | None}` (None restores the original); `review_actions.GLYPH_TEXT = "字型沒有「{chars}」這幾個字，沒有存。"`; `POST /api/caption-lines` body `{"sentence", "text"}` → 200 `{"lines": [str, ...]}` (the line breaks the text would get, from `break_lines` with `remap_units`, nothing written) or 422 `{"error"}`; `page_data` sentences gain `caption` (the shown caption text), `original` (the sentence text) and `caption_edited` (bool).

A caption change with characters the caption font lacks (`captions.missing_glyphs`) is refused with `GLYPH_TEXT` before validate. The page (spec §7.1, mockup `caption-edit.html` in the same folder as the others): `改字幕` on the typo card and in the `⋯` menu opens the panel under the sentence — read-only `講了什麼（辨識的結果）` for a recognised sentence, `原本的字幕（取自腳本）` for one from the script; an editable field with changed characters marked green; the live lines from `/api/caption-lines` (debounced 300 ms) and the caption over the video updated as you type; `儲存`, `取消`, `復原成原本的字`, `▶ 播這一句`. A saved sentence shows `已改字幕　復原`; a typo item it fixes leaves the navigation and the confirm dialog; the typo card's chat line becomes `要重錄：回到 Claude 對話說「1:48 那句要重錄」。`; the typo card's suggestion is Claude's note when there is one.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_caption_change_writes_captions_text(tmp_path): ...
def test_none_restores_the_original_caption(tmp_path): ...
def test_a_character_the_font_lacks_is_refused_in_chinese(tmp_path): ...   # e.g. "\U0001F600"; GLYPH_TEXT with that character
def test_caption_lines_preview_writes_nothing(tmp_path): ...              # edit.json bytes unchanged; lines == break_lines result
def test_page_data_marks_an_edited_caption(tmp_path): ...
def test_the_page_offers_caption_editing(): ...   # "改字幕", "儲存", "復原成原本的字", "已改字幕" in review.js
```

- [ ] **Step 2:** Run `uv run pytest tests/test_review_actions.py tests/test_review_data.py tests/test_review_server.py tests/test_review_page.py -v` — FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "The review page edits caption text: a panel with the live line breaks, refused in Chinese for a character the font lacks, saved to captions.text and undone with 復原"`.

### Task 13: Docs and skills

**Files:**
- Modify: `CLAUDE.md`, `.claude/skills/reel/SKILL.md`, `.claude/skills/storyboard/SKILL.md`, `FEATURE.md`
- Test: `tests/test_docs.py`

Spec §8 and §14: CLAUDE.md file roles (edit.json written by Claude and by the review page's server, both validated; `work/review-undo.json` and `work/review.mp4` made by `pipeline review`) and gate rules (two kinds of explicit approval; with the page open, a "可以" in the chat needs no second question unless Claude changed edit.json after it; after an approval in the page, Claude does not ask again). Reel skill sections 7–8: at gate 1, at the computer, run `uv run pipeline review <專案>` in the background, read its first JSON line for the URL, and send the short message of spec §8.1; under Remote Control keep the current static page and long message; when the user comes back to the chat run `uv run pipeline status` and read edit.json first; how to stop the server. Storyboard skill: write `notes` for every decision candidate and every recognised-caption sentence (format, examples made up); `captions.text` for a fixed typo instead of rerunning from the script (the script route stays). FEATURE.md: the review page and caption editing as finished work.

- [ ] **Step 1: Write the failing tests**

```python
def test_reel_skill_starts_the_review_page_and_keeps_the_remote_control_route():
    text = DOCS[1].read_text(encoding="utf-8")
    assert "pipeline review" in text and "Remote Control" in text and "pipeline status" in text
def test_claude_md_lets_the_review_page_write_edit_json_and_approve():
    text = DOCS[0].read_text(encoding="utf-8"); assert "審閱頁" in text and "確定" in text
def test_storyboard_skill_explains_notes_and_caption_text():
    text = DOCS[2].read_text(encoding="utf-8"); assert "`notes`" in text and "captions.text" in text and "建議" in text and "。因為" in text
def test_feature_list_has_the_review_page_and_caption_editing(): ...
```

(`test_docs_mention_only_real_commands` already checks that `pipeline review` exists; the id check already runs over the fenced examples.)

- [ ] **Step 2:** Run `uv run pytest tests/test_docs.py -v` — the new tests FAIL.
- [ ] **Step 3:** Edit the four files.
- [ ] **Step 4:** Run `uv run pytest -q` — PASS.
- [ ] **Step 5:** Commit: `git commit -m "CLAUDE.md, the reel and storyboard skills and FEATURE.md describe the review page, notes and caption text"`.

### Task 14: Real-material walk-through (controller task)

Run by the controller. All of it in the scratchpad project with `t11-env.sh` (HOME, XDG_*, TMPDIR and REELS_STUDIO in the scratchpad), never under the real home; check `df` first and warn below 15 GB.

- [ ] **Step 1:** Copy the t11 project's current state; write notes into its edit.json as Claude would; `validate`; `uv run pipeline review 2026-11-12-revision-check --no-browser` in the background; read the URL.
- [ ] **Step 2:** In Chrome (claude-in-chrome, a new tab): load the page, check no console errors, toggle one decision, remove and restore a sentence, edit one caption and restore it, play across three joints, approve; wait for the done view; confirm `out/final.mp4` and `out/preview.mp4` exist and `status` says `done`. Screenshot each state; note anything that looks wrong.
- [ ] **Step 3:** Send the user the regenerated static `review/gate1/index.html` and ask them to open it on the iPhone from the chat with silent mode off and press two play buttons (asynchronous; the answer goes in the ledger).
- [ ] **Step 4:** Stop the server; record the results in the ledger.

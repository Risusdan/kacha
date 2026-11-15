# Card Previews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each animation card as it looks at rest, where render puts it, on the gate-1 page and the review page, before the user approves.

**Architecture:** Remotion renders each card at rest to a transparent 1080x1920 PNG (`render.mjs` still jobs), cached in `work/scenes/<key>.png` by card, params, align, style and card code. The storyboard composites each still over the middle frame of its card's time (framed as the reel frames it) into an embedded 540x960 JPEG; the review server renders missing stills before it answers `/api/data` and serves them, and the page lays the still over the video while the card shows.

**Tech Stack:** Python 3.12, Remotion 4.0.542 (`renderStill`), ffmpeg, plain JS for the review page, pytest and `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-15-card-previews-design.md`

## Global Constraints

- Still: `STILL_FRAMES = 90`, `STILL_FRAME = 81`; 1080x1920 PNG with alpha; written to `<out>.part.png`, then renamed.
- Still key: sha256 of `{"kind": "still", "card", "params", "align", "frames": STILL_FRAMES, "frame": STILL_FRAME, "style", "sources"}`; no reel length in it.
- Still temp folder: `work/scenes/still-tmp-<random>` per run, removed with `safe_rmtree(..., allowed_root=p.dir)`; `still-tmp-*` older than 3600 s removed at the start of a run; log `work/stills.log`.
- Preview: 540x960 JPEG; background = source frame at the middle reel frame of the card (`start + frames // 2`), cropped to the shot's window (`punch.base_window` + `punch.window`), card still over it; no captions.
- Warning code: `card previews not made: <reason>`; Chinese on the page: `動畫卡片的樣張做不出來：<reason>`.
- `work/faces.json` is a storyboard input only when a scene shows.
- Review route `scene-stills/<64 lowercase hex>.png`; anything else 404; key-protected like every route.
- Recursive deletes only through `pipeline.safe_delete.safe_rmtree`.

## Review Focus

- A still render that fails must never fail the storyboard or the review page (warning / log only), but an ffmpeg failure while compositing is a refusal like thumbnails.
- Two still runs at once (review server and storyboard from the chat) must not delete each other's temp folder or half-written PNG.
- A cut toggle that moves a card to the other side must show the still of the new side.
- A card whose middle reel frame falls in a punched-in shot must be composited over that shot's framing.
- A project without scenes must not need node, faces.json or any still.

---

### Task 1: Card stills

**Files:** Modify `scenes/render.mjs`, `pipeline/scene_render.py`; Test `tests/test_scene_render.py`, `tests/test_scene_cards.py`.

**Interfaces — Produces:** `STILL_FRAMES`, `STILL_FRAME`; `still_key(card, params, align, style_hash, source_hash) -> str`; `still_paths(p, placed, style) -> list[tuple[Placed, Path]]`; `render_stills(p, placed, style, run=subprocess.run) -> list[tuple[Placed, Path]]` (renders the missing ones, raises `PipelineError` as `render_scenes` does).

- [ ] Failing tests: the key ignores frames and changes with each other part; `render_stills` writes still jobs (`kind`, `frames` 90, `frame` 81, `align`, `.png` out) only for missing stills, runs node once, removes its temp folder and stale `still-tmp-*` (not fresh ones), raises with `log` when node fails or a still is not written; nothing runs for no scenes.
- [ ] Failing real-render tests: each CARDS card's still is 1080x1920 RGBA; its opaque bbox equals that of frame 81 of a 90-frame clip of the same card within 2 px; a right-aligned still is flush with the zone's right edge.
- [ ] Implement `render.mjs` still jobs (`renderStill`, `imageFormat: "png"`) and the Python side; run; commit.

### Task 2: Previews on the gate-1 page

**Files:** Modify `pipeline/storyboard.py`; Test `tests/test_storyboard.py` (or the module that tests it).

**Interfaces — Consumes:** `render_stills`; `card_place.placed_scenes`; `punch.shots`, `punch.base_window`, `punch.window`.

- [ ] Failing tests: with a scene, the page has the section 「動畫的樣子」 with one 540x960 JPEG per scene and its label line; faces.json is an input with scenes and not without; a still renderer that raises gives the warning and no section, and the storyboard is made; the preview's crop is the window of the shot at the card's middle reel frame (unit test of the crop maths).
- [ ] Implement: placed scenes in the plan; stills through an injectable renderer; composite with ffmpeg (`crop`, `scale=540:960`, `overlay`); HTML section and CSS; run; commit.

### Task 3: Stills on the review page

**Files:** Modify `pipeline/review_data.py`, `pipeline/review_server.py`, `pipeline/review_page/review.js`, `index.html`, `review.css`; Test the review data, server and page tests.

- [ ] Failing tests: `scenes[k].still` is the URL when the PNG exists, else null; the server renders missing stills once before `/api/data` (fake renderer), logs a failure and does not retry it; `scene-stills/` serves a PNG and 404s other names; `serve_review` renders stills before it announces; the page shows the still and hides the label while a scene with a still plays, shows the label without one.
- [ ] Implement; run; commit.

### Task 4: Docs

- [ ] storyboard and reel skills, `docs/features.md`, `README.md`, `FEATURE.md` per spec 5; `tests/test_docs.py`; commit.

### Task 5: Demo, suite, review

- [ ] Scratch copy of test001 with the three demo cards: storyboard, send the page's previews to the user; full suite; one review subagent; fix; stop before merge.

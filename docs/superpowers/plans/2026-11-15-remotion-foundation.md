# Remotion Foundation Implementation Plan

> **For agentic workers:** executed Native (Claude implements every task in this session; one reviewer on the whole branch at the end). Steps use checkbox (`- [ ]`) syntax. The user asked for small committed steps and at most one subagent at a time.

**Goal:** `edit.json` `scenes` puts transparent Remotion cards (keyword-pop, number-slab, bullet-list) over the talking head in the render, under the captions; storyboard and review page show where.

**Architecture:** a pinned Remotion project in `scenes/` renders cards to ProRes 4444 through one Node process per render (`scenes/render.mjs`, one bundle); `pipeline/scenes.py` checks `scenes`, turns its anchors into reel frames, caches renders under `work/scenes/`; `render.py` overlays them after zoompan and before subtitles.

**Tech Stack:** Python 3.12, pytest, ffmpeg 9.0.2, Node 23.7, Remotion 4.0.542, React 19.3.1, Node test runner.

**Spec:** `docs/superpowers/specs/2026-11-15-remotion-foundation-design.md`

## Global Constraints

- Exact pins: `remotion`, `@remotion/bundler`, `@remotion/renderer` 4.0.542; `react`, `react-dom` 19.3.1; `npm ci` from the committed lock.
- Canvas 1080x1920, 30 fps; ProRes 4444, `yuva444p10le`, PNG frames.
- In 12 frames, out 8; under 30 frames both shrink in proportion. Cards sit at the top of `style.scenes.zone`, centred.
- No `Date.now()`, no `Math.random()` in cards.
- A reel without `scenes` never runs Node.
- Example sentences in tests and docs are made up; no user material.
- TDD for behaviour changes; commits carry no AI attribution.
- Any licence that is not clearly commercial-use: stop and ask the user.

## Review Focus

- An edit whose scene sentence the review page removes: validate passes with a warning, render skips the scene.
- A scene crossing a cut point: one continuous clip, its length the reel span.
- A text param with a character the font lacks: render refuses before running Node.
- A second render with nothing changed: no Node run (cache hit).
- Node or the browser missing: a PipelineError that names `pipeline setup`, and only when the edit has scenes.

---

### Task 1: the Remotion project and its licences

- [x] `scenes/package.json` (private, exact pins), `npm ci`, Chrome Headless Shell downloaded with `npx remotion browser ensure`; `df` first.
- [x] Read the licence file shipped with the browser download; read `LICENSE.md` in `node_modules/remotion`. Stop and ask if either is unclear.
- [x] `scripts/third_party.py`: tests first (npm packages of a lock: allowed licence passes, other needs a register row for that version), then implement; `--check` passes; rows in `THIRD_PARTY.md`; README licence statement. Commit.

### Task 2: cards and the Node renderer

- [x] `scenes/src/timing.js` with Node tests first (`inOut`, shrink under 30 frames, `stagger`).
- [x] `scenes/src/index.jsx`, `Root.jsx`, `cards/keyword-pop.jsx`, `number-slab.jsx`, `bullet-list.jsx`; `scenes/render.mjs` (jobs JSON in, one JSON line per clip out).
- [x] A Python test that runs a real render of each card: frame count, transparent corners, opaque card centre, two runs identical. Commit.

### Task 3: style tokens

- [x] Tests first: `load_style` refuses a zone outside the safe area or reaching the two-line caption band; the default style has the tokens. Implement; commit.

### Task 4: `scenes` in validate, and timing

- [x] Tests first (`tests/test_scenes.py`, `tests/test_edit.py`): every error of spec 5, the two warnings, FUTURE_KEYS without scenes, the review page keeps a change that hides a scene; `scene_spans` gives reel start frame and frame count (inside a sentence, across a cut, anchor in a cut).
- [x] Implement in `pipeline/scenes.py` (card registry with params and widths) and `edit.py`; commit.

### Task 5: rendering with a cache; setup and doctor

- [x] Tests first: the cache key changes with each input of spec 7; a hit runs nothing; missing glyph refuses; missing node or modules refuses naming setup; doctor fields; setup runs `npm ci` and the browser download through a stand-in runner.
- [x] Implement; commit.

### Task 6: the composite

- [x] Tests first: graph string with two scenes; a real ffmpeg run on a synthetic picture and a real card: the zone changes during the scene only, captions still drawn, frame count unchanged; render inputs include `scenes/` sources only when there are scenes.
- [x] Implement; commit.

### Task 7: storyboard and review page

- [x] Tests first: 動畫 column and summary in page and md; warnings list hidden scenes; `page_data.scenes`; the player shows the label during the scene (Node).
- [x] Implement; commit.

### Task 8: skills, docs, verify

- [x] storyboard skill section 「scenes：動畫」 (three cards, params, anchors, made-up example), reel skill, CLAUDE.md phase note, workflow spec pointer; `tests/test_docs.py`.
- [x] Full suite and Node tests; one reviewer on the branch; fix.
- [x] Demo: test001 render into the scratchpad with two or three scenes (project read-only), sent to the user; ask before merging.

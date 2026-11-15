# Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** kacha has a program version (`0.<feature>.<fix>`) that the CLI reports and that every project records per step and approval.

**Architecture:** One function, `pipeline.version.kacha_version()`, reads `project.version` from the repo's `pyproject.toml`. The CLI (`--version`, `doctor`, `status`) reports it; `run_step` and `approve_gate` write it into `state.json` as `kacha_version`; `project_status` lists those as `made_with`. Docs carry the bump rule and the publish tag.

**Tech Stack:** Python 3.12 (`tomllib`), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-15-versioning-design.md`

## Global Constraints

- The version lives only in `pyproject.toml` `project.version`; `uv.lock`'s `kacha` entry follows it (`uv lock`).
- Format: three dot-separated numbers, e.g. `0.2.0`.
- The record key is `kacha_version`, never `version` (step records already use `version` for `FORMAT_VERSIONS`).
- The version is a record only: it never makes a step not done, a skip not happen, or an approval invalid.
- Old `state.json` files without `kacha_version` load and report as before; their entries in `made_with` are `null`.
- CLI output stays one line of JSON with `"ok"`.
- Docs that Claude reads talk to the user in Traditional Chinese (Taiwan usage); README text goes through /handout-writing.
- No push and no tag push to GitHub, and no merge, without the user's OK.

## Review Focus

1. A project made before versioning (no `kacha_version` anywhere): `status <project>` works, `made_with` has `null` for each done step, nothing is redone. Test in Task 3.
2. A step recorded by another version with unchanged inputs: still skipped, and its `kacha_version` stays the old one. Test in Task 3.
3. `status` with one unreadable project: the listing still carries the top-level `version`. Test in Task 2.
4. `pipeline --version extra` or `pipeline status --version`: a JSON usage error (exit 1), not a traceback. Test in Task 2.
5. `pyproject.toml` bumped without `uv lock`: a test fails. Test in Task 1.

---

### Task 1: `kacha_version()`

**Files:**
- Create: `pipeline/version.py`
- Test: `tests/test_version.py`

**Interfaces:**
- Produces: `pipeline.version.PYPROJECT: Path` (`repo_root() / "pyproject.toml"`), `pipeline.version.kacha_version() -> str`.

- [ ] **Step 1: Write the failing tests** in `tests/test_version.py`:
  - `test_the_version_is_the_one_in_pyproject`: `kacha_version() == tomllib.load(open(repo_root()/"pyproject.toml","rb"))["project"]["version"]`, and it fullmatches `\d+\.\d+\.\d+`.
  - `test_the_lock_has_the_version_of_pyproject`: the `[[package]]` entry of `uv.lock` (parsed with `tomllib`) whose `name == "kacha"` has `version == kacha_version()`.
  - `test_an_unreadable_pyproject_is_a_pipeline_error` (monkeypatch `pipeline.version.PYPROJECT` to a missing file under `tmp_path`, then to a file without `version`, then to a file that is not TOML): `PipelineError` whose message starts with `cannot read the kacha version from pyproject.toml: `.
- [ ] **Step 2: Run** `uv run pytest tests/test_version.py -q`. Expected: FAIL (no module `pipeline.version`).
- [ ] **Step 3: Implement** `kacha_version()` in `pipeline/version.py`: read `PYPROJECT` at each call (no cache: a bump takes effect at once), return `project.version`; `OSError`, `tomllib.TOMLDecodeError`, a missing key or a value that is not a string → `PipelineError(f"cannot read the kacha version from pyproject.toml: {reason}")`.
- [ ] **Step 4: Run** `uv run pytest tests/test_version.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `pipeline/version.py tests/test_version.py`.

### Task 2: The CLI reports the version

**Files:**
- Modify: `pipeline/cli.py` (`main`/`_parse`, `_status`), `pipeline/assets.py` (`doctor`)
- Test: `tests/test_cli.py`, `tests/test_assets.py`

**Interfaces:**
- Consumes: `kacha_version()` (Task 1).
- Produces: `pipeline --version` → `{"ok": true, "version": "<v>"}`; `doctor()` result has `"version"`; `status` and `status <name>` results have `"version"`.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_cli.py::test_version_prints_the_version`: `main(["--version"]) == 0` and the one output line is `{"ok": True, "version": kacha_version()}`.
  - `tests/test_cli.py::test_version_with_other_arguments_is_a_usage_error`: for `["--version", "status"]` and `["status", "--version"]`, `main(...) == 1` and the output is one JSON line with `"ok": False`.
  - `tests/test_cli.py::test_status_reports_the_version`: with `REELS_STUDIO` at a `tmp_path` studio holding one project made by `create_project` and one folder whose `state.json` is not JSON, `main(["status"])` gives `"version": kacha_version()` next to `"projects"` (both projects listed, the broken one with `error`), and `main(["status", <the good one>])` gives `"version"` too.
  - `tests/test_assets.py::test_doctor_reports_the_version`: `doctor()["version"] == kacha_version()`.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py tests/test_assets.py -q -k "version"`. Expected: FAIL.
- [ ] **Step 3: Implement:** `--version` is handled in `main` only when it is the whole argument list (`argv` or `sys.argv[1:]` equals `["--version"]`); every other use stays argparse's usage error. `_status` adds `"version": kacha_version()` to both forms (the project form adds it to `project_status`'s dict in the CLI, not inside `project_status`). `doctor()` adds `"version"` first in its dict.
- [ ] **Step 4: Run** `uv run pytest tests/test_cli.py tests/test_assets.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `pipeline/cli.py pipeline/assets.py tests/test_cli.py tests/test_assets.py`.

### Task 3: Projects record the version

**Files:**
- Modify: `pipeline/project.py` (`run_step`, `approve_gate`, `project_status`)
- Test: `tests/test_project.py`

**Interfaces:**
- Consumes: `kacha_version()` (Task 1).
- Produces: step records and the gate record carry `"kacha_version": str`; `project_status(p)` has `"made_with": dict[str, str | None]` (keys: the done steps as in `"steps"`, plus `"gate1"` when `state["gates"]` holds a gate1 record).

- [ ] **Step 1: Write the failing tests** in `tests/test_project.py` (monkeypatch `pipeline.project.kacha_version` where a fixed value is needed):
  - `test_run_step_records_the_kacha_version`: after a run, `load_state(p)["steps"][step]["kacha_version"] == kacha_version()`.
  - `test_a_skipped_step_keeps_the_version_that_made_it`: run with the version patched to `"0.0.1"`, then run again unchanged with `"0.9.0"`: the second call returns `{"skipped": True}` and the record still says `"0.0.1"`.
  - `test_approve_records_the_kacha_version`: `approve_gate(...)["kacha_version"] == kacha_version()` and the same in `state.json`. Update `test_approve_records_the_gate` to expect `{"approved_at", "storyboard_inputs", "kacha_version"}`.
  - `test_status_lists_the_version_of_each_done_step_and_the_approval`: after the early steps, storyboard and approval, `project_status(p)["made_with"]` has exactly the keys of `project_status(p)["steps"]` plus `"gate1"`, each `kacha_version()`.
  - `test_a_project_made_before_versioning_reports_null_and_stays_done`: drop `kacha_version` from every step and gate record in `state.json` (write it back with `save_state`): `next`, `steps` and `gate1_ok` are what they were, and every `made_with` value is `None`.
  - `test_another_version_does_not_undo_a_step_or_the_approval`: with the records written under `"0.0.1"` and `kacha_version` patched to `"0.9.0"`, `next` and `gate1_ok` are unchanged.
- [ ] **Step 2: Run** `uv run pytest tests/test_project.py -q -k "version or approve_records"`. Expected: FAIL.
- [ ] **Step 3: Implement:** `run_step` adds `"kacha_version": kacha_version()` to `done` (after `done_at`); the skipped path leaves the record alone. `approve_gate` adds it to `record`. `project_status` adds `made_with` built from the same `state` it already loaded: `record.get("kacha_version")` for each done step, and the gate1 record's when there is one. `load_state` does not require the key. Update the docstrings of the three functions.
- [ ] **Step 4: Run** `uv run pytest tests/test_project.py tests/test_cli.py tests/test_review_server.py -q`. Expected: PASS (fix any other test that compared a whole record or a whole status dict, by adding the new key, not by loosening it).
- [ ] **Step 5: Commit** `pipeline/project.py tests/test_project.py` and any test updated in Step 4.

### Task 4: Docs

**Files:**
- Modify: `CLAUDE.md`, `.claude/skills/reel/SKILL.md`, `README.md`, `FEATURE.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test** `tests/test_docs.py::test_the_docs_explain_the_version`:
  - `CLAUDE.md` holds `docs/superpowers/specs/2026-11-15-versioning-design.md`, `pyproject.toml`, `uv lock` and `v<版本>`.
  - the reel skill holds `made_with`, `kacha_version` is absent from it (the user never sees record keys; Claude reads `made_with`), and `pipeline status` near `version`.
  - `README.md` mentions 版本 in the section after `### 出問題找誰？`.
  - every `### ` heading under a `## 已完成` heading of `FEATURE.md` ends with `（0.1.0）` or `（0.2.0）`.
- [ ] **Step 2: Run** `uv run pytest tests/test_docs.py -q -k version`. Expected: FAIL.
- [ ] **Step 3: Write the docs** (spec sections 2, 5, 6, 7, 8):
  - `CLAUDE.md`「開發」: a bullet with the bump rule of spec section 2 (where the version lives, when each number goes up, docs-only does not, skills and CLAUDE.md count as program, Claude proposes the number with the merge question, the bump is the branch's last commit), a bullet on the publish tag (spec section 6, pushed only with the user's OK); add the spec to the design-doc list.
  - reel skill: in section 1 (or the `status` explanation) the `version` of `doctor` and `status`, and `made_with` of `status <專案>`; a short paragraph: when the user wants to report a problem or asks which version made a project, answer from `version` and `made_with`, by step names in Chinese, no record keys.
  - `README.md`「出問題找誰？」: one sentence telling the reader to write the version in the Issue and that Claude tells them which version they have. Then `python3 ~/.claude/skills/handout-writing/check_handout.py --project .` and fix every `BLOCK`; then one fresh subagent with the skill's reader prompt over `README.md` and `docs/features.md` (this also covers the card-previews edits whose reader check was skipped); sort its list, and show the user what it found before changing shipped text beyond the one sentence.
  - `FEATURE.md`: versions per spec section 7 (0.1.0 for what is on GitHub; card placement, the three new cards, card previews and versioning are 0.2.0, marked on their items inside 動畫卡片; add a 版本號 item for this feature).
- [ ] **Step 4: Run** `uv run pytest tests/test_docs.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** the four docs and `tests/test_docs.py`.

### Review, before Task 5

One fresh reviewer over the branch so far (Tasks 1-4); fix what it finds, each fix with its test. The bump stays the branch's last commit (spec section 2).

### Task 5: 0.2.0

**Files:**
- Modify: `pyproject.toml`, `uv.lock`

- [ ] **Step 1:** Set `version = "0.2.0"` in `pyproject.toml`.
- [ ] **Step 2: Run** `uv run pytest tests/test_version.py -q`. Expected: FAIL in `test_the_lock_has_the_version_of_pyproject` (if `uv run` already relocked, note that the test could not be seen failing this way, and check `git diff uv.lock` shows the kacha entry at 0.2.0).
- [ ] **Step 3: Run** `uv lock`; `git diff uv.lock` changes only the kacha entry's version.
- [ ] **Step 4: Run** the full suite `uv run pytest -q` (about 10 min, in the background) and `node --test tests/js/*.test.mjs`. Expected: all pass. Also `uv run pipeline --version` prints `{"ok": true, "version": "0.2.0"}`.
- [ ] **Step 5: Commit** `pyproject.toml uv.lock` as "Version 0.2.0".

### After the tasks

- Update the memory `public-release-decisions`: the publish recipe gets the tag step (spec section 6), and v0.1.0 is to be set on `8b6a2b5` at the next publish.
- Stop: ask the user to merge. Tags `v0.1.0` (on `8b6a2b5`) and `v0.2.0` are made and pushed only at the next publish, with the user's OK.

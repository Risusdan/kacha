"""Rendering the scenes of an edit (spec 2026-11-15-remotion-foundation-design, section 7): the cache under
work/scenes, the checks made before Node runs, and the Remotion items of setup and doctor. Node and npm are replaced
by a stand-in that records the command and writes what render.mjs would, but in the one real render at the end. The
texts are made up."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from helpers import require_assets

from pipeline import assets, scene_render
from pipeline.errors import PipelineError
from pipeline.project import Project
from pipeline.scene_render import (
    NODE_MISSING,
    RENDER_TIMEOUT_S,
    STILL_FRAME,
    STILL_FRAMES,
    STILLS_TIMEOUT_S,
    cache_key,
    doctor_items,
    node_version,
    render_scenes,
    render_stills,
    setup_scenes,
    source_files,
    sources_digest,
    still_key,
    still_paths,
    style_digest,
)
from pipeline.scenes import Placed
from pipeline.style import load_style


def placed(index: int = 0, card: str = "keyword-pop", params: dict | None = None, frames: int = 45) -> Placed:
    scene = {"id": f"sc{index + 1}", "card": card, "at": {"s": "s01"}, "until": {"s": "s01", "end": True},
             "params": params if params is not None else {"text": "省電"}}
    return Placed(index, scene, 30 * index, frames)


class Runner:
    """A stand-in for subprocess.run. For render.mjs it writes each job's out file and prints what render.mjs prints;
    `fail` makes it exit 1 with an error line instead. Every call is kept as (command, keyword arguments)."""

    def __init__(self, fail: str | None = None):
        self.calls: list[tuple[list[str], dict]] = []
        self.fail = fail

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if not str(command[1]).endswith("render.mjs"):
            return subprocess.CompletedProcess(command, 0, "", "")
        self.jobs = json.loads(Path(command[2]).read_text(encoding="utf-8"))
        self.tmpdir = Path(kwargs["env"]["TMPDIR"])
        assert self.tmpdir.is_dir()
        (self.tmpdir / "remotion-webpack-bundle-x").mkdir()  # what Remotion leaves there
        if self.fail:
            return subprocess.CompletedProcess(command, 1, json.dumps({"ok": False, "error": self.fail}) + "\n",
                                               "a stack trace\n")
        lines = []
        for job in self.jobs["jobs"]:
            Path(job["out"]).write_bytes(b"a clip")
            lines.append(json.dumps({"ok": True, "card": job["card"], "out": job["out"], "frames": job["frames"]}))
        return subprocess.CompletedProcess(command, 0, "\n".join(lines) + "\n", "")


def refuse(*args, **kwargs):
    raise AssertionError("nothing may run")


@pytest.fixture
def project(tmp_path) -> Project:
    p = Project("2026-11-15-cards", tmp_path / "2026-11-15-cards")
    p.work.mkdir(parents=True)
    return p


@pytest.fixture
def tools(monkeypatch):
    """Node, the modules and the browser are all there (as far as render_scenes asks)."""
    monkeypatch.setattr(scene_render, "node_bin", lambda: "/stand-in/node")
    monkeypatch.setattr(scene_render, "node_version", lambda node: (23, 7, 0))
    monkeypatch.setattr(scene_render, "modules_installed", lambda: True)
    monkeypatch.setattr(scene_render, "browser_installed", lambda: True)


# ---- the cache key --------------------------------------------------------------------------------------------


def test_the_cache_key_changes_with_each_input_and_only_with_them():
    base = ("keyword-pop", {"text": "省電", "x": 1}, 45, "center", "style-a", "sources-a")
    key = cache_key(*base)
    assert cache_key("keyword-pop", {"x": 1, "text": "省電"}, 45, "center", "style-a", "sources-a") == key  # key order
    changed = [
        cache_key("number-slab", *base[1:]),
        cache_key(base[0], {"text": "亮度", "x": 1}, *base[2:]),
        cache_key(*base[:2], 46, *base[3:]),
        cache_key(*base[:3], "left", *base[4:]),
        cache_key(*base[:4], "style-b", base[5]),
        cache_key(*base[:5], "sources-b"),
    ]
    assert len({key, *changed}) == 7


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_sources_digest_covers_src_render_mjs_and_the_lock(tmp_path):
    folder = tmp_path / "scenes"
    for name in ("src/index.jsx", "src/cards/keyword-pop.jsx", "render.mjs", "package-lock.json", "package.json",
                 "README.md", "node_modules/remotion/index.js", "node_modules/.cache/x"):
        write(folder / name, name)
    digest = sources_digest(folder)
    for name in ("README.md", "node_modules/remotion/index.js", "node_modules/.cache/x", "package.json"):
        write(folder / name, "changed")
        assert sources_digest(folder) == digest, name
    for name in ("src/index.jsx", "src/cards/keyword-pop.jsx", "render.mjs", "package-lock.json", "src/cards/new.jsx"):
        write(folder / name, f"changed {name}")
        assert sources_digest(folder) != digest, name
        digest = sources_digest(folder)


def test_hidden_and_backup_files_under_src_are_not_card_sources(tmp_path):
    folder = tmp_path / "scenes"
    for name in ("render.mjs", "package-lock.json", "src/a.jsx"):
        write(folder / name, name)
    digest = sources_digest(folder)
    for name in ("src/.DS_Store", "src/cards/.a.jsx.swp", "src/a.jsx~", "src/.cache/x"):
        write(folder / name, name)
    assert sources_digest(folder) == digest
    assert [path.relative_to(folder).as_posix() for path in source_files(folder)] == [
        "src/a.jsx", "render.mjs", "package-lock.json"]


def test_a_file_moved_inside_src_changes_the_digest(tmp_path):
    folder = tmp_path / "scenes"
    write(folder / "render.mjs", "r")
    write(folder / "package-lock.json", "{}")
    write(folder / "src/a.jsx", "same")
    digest = sources_digest(folder)
    (folder / "src/a.jsx").rename(folder / "src/b.jsx")
    assert sources_digest(folder) != digest


def test_the_style_digest_covers_style_json_and_the_two_fonts(tmp_path):
    folder = tmp_path / "style"
    for name in ("style.json", "fonts/R.ttf", "fonts/B.ttf", "fonts/other.ttf"):
        write(folder / name, name)
    style = {"dir": folder, "fonts": {"regular": "fonts/R.ttf", "bold": "fonts/B.ttf"}}
    digest = style_digest(style)
    write(folder / "fonts/other.ttf", "changed")
    assert style_digest(style) == digest
    for name in ("style.json", "fonts/R.ttf", "fonts/B.ttf"):
        write(folder / name, f"changed {name}")
        assert style_digest(style) != digest, name
        digest = style_digest(style)


# ---- rendering through the cache ------------------------------------------------------------------------------


def test_an_edit_without_scenes_runs_nothing_and_makes_no_folder(project, monkeypatch):
    monkeypatch.setattr(scene_render, "node_bin", refuse)
    assert render_scenes(project, [], load_style(), run=refuse) == []
    assert not (project.work / "scenes").exists()


def test_the_scenes_are_rendered_by_one_node_run_and_then_come_from_the_cache(project, tools):
    style = load_style()
    scenes = [placed(0), placed(1, "number-slab", {"value": "兩小時", "label": "一天多撐"}, 60)]
    runner = Runner()
    clips = render_scenes(project, scenes, style, run=runner)
    assert len(runner.calls) == 1
    assert [scene for scene, _ in clips] == scenes
    for _, clip in clips:
        assert clip.parent == project.work / "scenes" and clip.suffix == ".mov" and clip.is_file()
    assert render_scenes(project, scenes, style, run=refuse) == clips


def test_the_command_and_the_jobs_render_mjs_gets(project, tools):
    style = load_style()
    runner = Runner()
    render_scenes(project, [placed(0, frames=45)], style, run=runner)
    (command, kwargs), = runner.calls
    scenes_dir = scene_render.scenes_dir()
    assert command == ["/stand-in/node", str(scenes_dir / "render.mjs"), str(project.work / "scenes" / "jobs.json")]
    assert kwargs["cwd"] == scenes_dir
    assert runner.tmpdir == project.work / "scenes" / "tmp"
    assert runner.jobs["style_dir"] == str(style["dir"])
    assert runner.jobs["style"] == {**style["scenes"], "fonts": {"regular": style["fonts"]["regular"],
                                                                  "bold": style["fonts"]["bold"]}}
    (job,) = runner.jobs["jobs"]
    assert (job["card"], job["params"], job["frames"], job["align"]) == ("keyword-pop", {"text": "省電"}, 45, "center")
    assert job["out"].startswith(str(project.work / "scenes")) and job["out"].endswith(".mov")


def test_the_temporary_folder_is_removed_after_the_run_and_after_a_failure(project, tools):
    runner = Runner()
    render_scenes(project, [placed(0)], load_style(), run=runner)
    assert not runner.tmpdir.exists()
    failing = Runner(fail="something broke")
    with pytest.raises(PipelineError):
        render_scenes(project, [placed(1, frames=50)], load_style(), run=failing)
    assert not failing.tmpdir.exists()


def test_scenes_of_one_card_params_and_length_are_rendered_once(project, tools):
    runner = Runner()
    clips = render_scenes(project, [placed(0), placed(1)], load_style(), run=runner)
    assert len(runner.jobs["jobs"]) == 1
    assert clips[0][1] == clips[1][1]


def test_a_card_placed_to_one_side_is_a_clip_of_its_own_and_render_mjs_is_told_the_side(project, tools):
    runner = Runner()
    clips = render_scenes(project, [placed(0), placed(1)._replace(align="left")], load_style(), run=runner)
    assert [job["align"] for job in runner.jobs["jobs"]] == ["center", "left"]
    assert clips[0][1] != clips[1][1]


def test_only_the_scenes_not_in_the_cache_are_rendered(project, tools):
    style = load_style()
    render_scenes(project, [placed(0)], style, run=Runner())
    runner = Runner()
    render_scenes(project, [placed(0), placed(1, frames=50)], style, run=runner)
    assert [job["frames"] for job in runner.jobs["jobs"]] == [50]


@pytest.mark.parametrize("digest", ["style_digest", "sources_digest"])
def test_a_changed_style_or_card_source_renders_the_clip_again(project, tools, monkeypatch, digest):
    style = load_style()
    render_scenes(project, [placed(0)], style, run=Runner())
    render_scenes(project, [placed(0)], style, run=refuse)  # from the cache
    monkeypatch.setattr(scene_render, digest, lambda *args: "changed")
    runner = Runner()
    render_scenes(project, [placed(0)], style, run=runner)
    assert len(runner.jobs["jobs"]) == 1


# ---- the stills ----------------------------------------------------------------------------------------------


def test_a_still_is_frame_81_of_a_card_90_frames_long():
    """Every card is at rest then: the last frame before its 8-frame exit (scenes/src/timing.js)."""
    assert (STILL_FRAMES, STILL_FRAME) == (90, 81)


def test_the_still_key_has_no_reel_length_and_changes_with_each_other_input():
    base = ("keyword-pop", {"text": "省電", "x": 1}, "center", "style-a", "sources-a")
    key = still_key(*base)
    assert still_key("keyword-pop", {"x": 1, "text": "省電"}, "center", "style-a", "sources-a") == key
    changed = [
        still_key("number-slab", *base[1:]),
        still_key(base[0], {"text": "亮度", "x": 1}, *base[2:]),
        still_key(*base[:2], "left", *base[3:]),
        still_key(*base[:3], "style-b", base[4]),
        still_key(*base[:4], "sources-b"),
    ]
    assert len({key, *changed}) == 6
    assert key not in {cache_key(base[0], base[1], frames, "center", "style-a", "sources-a")
                       for frames in (STILL_FRAMES, 45)}


def test_an_edit_without_scenes_renders_no_still_and_makes_no_folder(project, monkeypatch):
    monkeypatch.setattr(scene_render, "node_bin", refuse)
    assert render_stills(project, [], load_style(), run=refuse) == []
    assert not (project.work / "scenes").exists()


def test_the_stills_are_rendered_by_one_node_run_then_come_from_the_cache_whatever_the_length(project, tools):
    style = load_style()
    scenes = [placed(0, frames=45), placed(1, "number-slab", {"value": "兩小時", "label": "一天多撐"}, 60)]
    runner = Runner()
    stills = render_stills(project, scenes, style, run=runner)
    assert len(runner.calls) == 1
    assert [scene for scene, _ in stills] == scenes
    for _, still in stills:
        assert still.parent == project.work / "scenes" and still.suffix == ".png" and still.is_file()
    longer = [scene._replace(frames=scene.frames + 30) for scene in scenes]
    assert [path for _, path in render_stills(project, longer, style, run=refuse)] == [path for _, path in stills]


def test_the_still_jobs_render_mjs_gets_and_the_folder_it_runs_in(project, tools):
    style = load_style()
    runner = Runner()
    render_stills(project, [placed(0, frames=45)._replace(align="right")], style, run=runner)
    (command, kwargs), = runner.calls
    assert runner.tmpdir.parent == project.work / "scenes" and runner.tmpdir.name.startswith("still-tmp-")
    assert command == ["/stand-in/node", str(scene_render.scenes_dir() / "render.mjs"), str(runner.tmpdir / "jobs.json")]
    assert kwargs["cwd"] == scene_render.scenes_dir()
    assert runner.jobs["style_dir"] == str(style["dir"])
    (job,) = runner.jobs["jobs"]
    assert {key: job[key] for key in ("kind", "card", "params", "frames", "frame", "align")} == {
        "kind": "still", "card": "keyword-pop", "params": {"text": "省電"}, "frames": 90, "frame": 81, "align": "right"}
    assert Path(job["out"]).parent == project.work / "scenes" and job["out"].endswith(".png")
    assert not runner.tmpdir.exists()
    assert not (project.work / "scenes" / "jobs.json").exists()


def test_only_the_missing_stills_are_rendered_and_each_side_is_a_still_of_its_own(project, tools):
    style = load_style()
    render_stills(project, [placed(0)], style, run=Runner())
    runner = Runner()
    stills = render_stills(project, [placed(0), placed(1)._replace(align="left")], style, run=runner)
    assert [job["align"] for job in runner.jobs["jobs"]] == ["left"]
    assert stills[0][1] != stills[1][1]


def test_a_left_over_still_folder_older_than_an_hour_is_removed_and_a_recent_one_kept(project, tools):
    folder = project.work / "scenes"
    old, recent, clips = folder / "still-tmp-old", folder / "still-tmp-recent", folder / "tmp"
    for each in (old, recent, clips):
        (each / "inner").mkdir(parents=True)
    hour_ago = time.time() - 3700
    for each in (old, clips):
        os.utime(each, (hour_ago, hour_ago))
    render_stills(project, [placed(0)], load_style(), run=Runner())
    assert not old.exists()
    assert recent.is_dir() and clips.is_dir()  # another run's, and render's own


def test_a_left_over_still_folder_another_run_removes_first_is_no_error(project, tools, monkeypatch):
    """Two runs that start together both find an interrupted run's folder; the one that comes second finds it gone."""
    folder = project.work / "scenes"
    old = folder / "still-tmp-old"
    (old / "inner").mkdir(parents=True)
    hour_ago = time.time() - 3700
    os.utime(old, (hour_ago, hour_ago))
    real = scene_render.safe_rmtree

    def removed_first(path, **kwargs):
        if Path(path) == old:
            real(path, **kwargs)  # the other run
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return real(path, **kwargs)

    monkeypatch.setattr(scene_render, "safe_rmtree", removed_first)
    (_, still), = render_stills(project, [placed(0)], load_style(), run=Runner())
    assert still.is_file() and not old.exists()


def test_a_still_run_has_a_timeout_of_its_own_much_shorter_than_a_render(project, tools):
    """A still run answers a page that waits for it: a hung run must not hold it for the hour a render may take."""
    runner = Runner()
    render_stills(project, [placed(0)], load_style(), run=runner)
    (_, kwargs), = runner.calls
    assert kwargs["timeout"] == STILLS_TIMEOUT_S == 120
    runner = Runner()
    render_scenes(project, [placed(1)], load_style(), run=runner)
    assert [kwargs["timeout"] for command, kwargs in runner.calls if str(command[1]).endswith("render.mjs")] == [
        RENDER_TIMEOUT_S]


def test_a_failed_still_render_says_why_keeps_a_log_removes_its_folder_and_caches_nothing(project, tools):
    runner = Runner(fail="Error: no composition named keyword-pop")
    with pytest.raises(PipelineError) as failed:
        render_stills(project, [placed(0)], load_style(), run=runner)
    assert failed.value.message == "the card previews failed to render: Error: no composition named keyword-pop"
    log = Path(failed.value.extra["log"])
    assert log == project.work / "stills.log" and "a stack trace" in log.read_text(encoding="utf-8")
    assert not list((project.work / "scenes").glob("*.png"))
    assert not runner.tmpdir.exists()


def test_a_still_render_mjs_did_not_write_is_an_error(project, tools):
    def silent(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")
    with pytest.raises(PipelineError) as failed:
        render_stills(project, [placed(0)], load_style(), run=silent)
    assert failed.value.message.startswith("the card previews failed to render: ")
    assert failed.value.message.endswith(".png was not written")


def test_a_still_refuses_what_a_clip_refuses_before_node_runs(project, tools, monkeypatch):
    with pytest.raises(PipelineError) as failed:
        render_stills(project, [placed(0, params={"text": "省電😀"})], load_style(), run=refuse)
    assert "has no glyph for" in failed.value.message
    monkeypatch.setattr(scene_render, "node_bin", lambda: None)
    with pytest.raises(PipelineError) as failed:
        render_stills(project, [placed(0)], load_style(), run=refuse)
    assert failed.value.message == NODE_MISSING


NODE_CRASH = (
    "node:internal/modules/esm/resolve:873\n  throw new ERR_MODULE_NOT_FOUND(packageName, fileURLToPath(base), null);\n"
    "        ^\n\nError [ERR_MODULE_NOT_FOUND]: Cannot find package 'react' imported from /x/scenes/render.mjs\n"
    "    at packageResolve (node:internal/modules/esm/resolve:873:9) {\n  code: 'ERR_MODULE_NOT_FOUND'\n}\n\n"
    "Node.js v23.7.0\n"
)
NODE_ERROR = "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'react' imported from /x/scenes/render.mjs"


def test_a_node_crash_is_reported_by_its_error_line_not_by_the_node_version(project, tools):
    def crash(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", NODE_CRASH)
    with pytest.raises(PipelineError) as failed:
        render_scenes(project, [placed(0)], load_style(), run=crash)
    assert failed.value.message == f"the animation cards failed to render: {NODE_ERROR}"


def test_a_failed_render_says_why_keeps_a_log_and_caches_nothing(project, tools):
    runner = Runner(fail="Error: no composition named keyword-pop")
    with pytest.raises(PipelineError) as failed:
        render_scenes(project, [placed(0)], load_style(), run=runner)
    assert failed.value.message == "the animation cards failed to render: Error: no composition named keyword-pop"
    log = Path(failed.value.extra["log"])
    assert log == project.work / "scenes.log" and "a stack trace" in log.read_text(encoding="utf-8")
    assert not list((project.work / "scenes").glob("*.mov"))


def test_a_character_the_card_font_lacks_is_refused_before_node_runs(project, tools):
    require_assets()
    with pytest.raises(PipelineError) as refused:
        render_scenes(project, [placed(0, "bullet-list", {"items": ["亮度", "電池🔋"]})], load_style(), run=refuse)
    assert refused.value.message == "the card font NotoSansTC-Bold.ttf has no glyph for: 🔋 (U+1F50B)"
    assert refused.value.extra["characters"] == ["🔋"]


@pytest.mark.parametrize("missing, words", [
    ("node_bin", "node (Node.js 18.18 or later) not found"),
    ("node_version", "node 18.17.1 is older than 18.18"),
    ("modules_installed", "scenes/node_modules"),
    ("browser_installed", "the browser Remotion renders with"),
])
def test_without_a_tool_the_render_refuses_and_names_setup(project, tools, monkeypatch, missing, words):
    stand_in = {"node_bin": lambda: None, "node_version": lambda node: (18, 17, 1),
                "modules_installed": lambda: False, "browser_installed": lambda: False}[missing]
    monkeypatch.setattr(scene_render, missing, stand_in)
    with pytest.raises(PipelineError) as refused:
        render_scenes(project, [placed(0)], load_style(), run=refuse)
    assert words in refused.value.message and "uv run pipeline setup" in refused.value.message


def test_a_render_from_the_cache_needs_no_node(project, tools, monkeypatch):
    style = load_style()
    clips = render_scenes(project, [placed(0)], style, run=Runner())
    monkeypatch.setattr(scene_render, "node_bin", lambda: None)
    assert render_scenes(project, [placed(0)], style, run=refuse) == clips


def test_check_renderable_needs_node_only_for_a_clip_not_rendered_yet(project, tools, monkeypatch):
    style = load_style()
    render_scenes(project, [placed(0)], style, run=Runner())
    before = sorted(path.name for path in (project.work / "scenes").iterdir())
    monkeypatch.setattr(scene_render, "node_bin", lambda: None)
    scene_render.check_renderable(project, [placed(0)], style)
    with pytest.raises(PipelineError, match=re.escape(NODE_MISSING)):
        scene_render.check_renderable(project, [placed(0), placed(1, params={"text": "亮度"})], style)
    assert sorted(path.name for path in (project.work / "scenes").iterdir()) == before  # it writes nothing


def test_check_renderable_refuses_a_missing_glyph_and_reads_nothing_without_scenes(project, monkeypatch):
    monkeypatch.setattr(scene_render, "scenes_dir", lambda: project.dir / "not there")
    scene_render.check_renderable(project, [], load_style())
    with pytest.raises(PipelineError, match=r"has no glyph for: \U0001f50b \(U\+1F50B\)"):
        scene_render.check_renderable(project, [placed(0, params={"text": "省電\U0001f50b"})], load_style())
    assert not (project.work / "scenes").exists()


def test_a_style_without_an_animation_zone_is_refused_before_anything_is_written(project, tools):
    style = {key: value for key, value in load_style().items() if key != "scenes"}
    for check in (scene_render.check_renderable, scene_render.render_scenes):
        with pytest.raises(PipelineError, match=r"^style default has no animation zone \(scenes in style\.json\)$"):
            check(project, [placed(0)], style)
    assert not (project.work / "scenes").exists()


# ---- setup and doctor -----------------------------------------------------------------------------------------


def test_node_version_reads_node_s_answer():
    def answer(text):
        return lambda command, **kwargs: subprocess.CompletedProcess(command, 0, text, "")
    assert node_version("node", run=answer("v23.7.0\n")) == (23, 7, 0)
    assert node_version("node", run=answer("something else")) is None


def test_setup_installs_the_modules_and_the_browser_when_they_are_missing(monkeypatch):
    monkeypatch.setattr(scene_render, "node_bin", lambda: "/stand-in/node")
    monkeypatch.setattr(scene_render, "node_version", lambda node: (23, 7, 0))
    monkeypatch.setattr(scene_render, "npm_bin", lambda: "/stand-in/npm")
    monkeypatch.setattr(scene_render, "modules_installed", lambda: False)
    monkeypatch.setattr(scene_render, "browser_installed", lambda: False)
    runner = Runner()
    setup_scenes(run=runner)
    folder = scene_render.scenes_dir()
    assert [(command, kwargs["cwd"]) for command, kwargs in runner.calls] == [
        (["/stand-in/npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], folder),
        (["/stand-in/node", str(folder / "ensure-browser.mjs")], folder),
    ]


def test_setup_leaves_what_is_there(monkeypatch):
    monkeypatch.setattr(scene_render, "node_bin", lambda: "/stand-in/node")
    monkeypatch.setattr(scene_render, "node_version", lambda node: (23, 7, 0))
    monkeypatch.setattr(scene_render, "modules_installed", lambda: True)
    monkeypatch.setattr(scene_render, "browser_installed", lambda: True)
    assert setup_scenes(run=refuse) == {"remotion": True, "remotion_browser": True}


def test_setup_without_node_says_to_install_it(monkeypatch):
    monkeypatch.setattr(scene_render, "node_bin", lambda: None)
    monkeypatch.setattr(scene_render, "modules_installed", lambda: False)
    with pytest.raises(PipelineError, match=r"node \(Node.js 18.18 or later\) not found"):
        setup_scenes(run=refuse)


def test_a_failed_npm_ci_is_an_error(monkeypatch):
    monkeypatch.setattr(scene_render, "node_bin", lambda: "/stand-in/node")
    monkeypatch.setattr(scene_render, "node_version", lambda node: (23, 7, 0))
    monkeypatch.setattr(scene_render, "npm_bin", lambda: "/stand-in/npm")
    monkeypatch.setattr(scene_render, "modules_installed", lambda: False)

    def failing(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "npm error code EUSAGE\n")
    with pytest.raises(PipelineError, match="npm ci failed with exit code 1: npm error code EUSAGE"):
        setup_scenes(run=failing)

    def not_found(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "npm error code E404\nnpm error 404 Not Found - GET x\n"
                                                           "npm error A complete log of this run can be found in: y\n")
    with pytest.raises(PipelineError, match="^npm ci failed with exit code 1: npm error code E404$"):
        setup_scenes(run=not_found)


def test_a_failed_browser_download_is_reported_by_its_error_line(monkeypatch):
    monkeypatch.setattr(scene_render, "node_bin", lambda: "/stand-in/node")
    monkeypatch.setattr(scene_render, "node_version", lambda node: (23, 7, 0))
    monkeypatch.setattr(scene_render, "modules_installed", lambda: True)
    monkeypatch.setattr(scene_render, "browser_installed", lambda: False)

    def crash(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", NODE_CRASH)
    with pytest.raises(PipelineError) as failed:
        setup_scenes(run=crash)
    assert failed.value.message == f"the browser download failed with exit code 1: {NODE_ERROR}"


def test_the_modules_count_as_installed_only_at_the_locked_versions(tmp_path, monkeypatch):
    folder = tmp_path / "scenes"
    lock = {"packages": {"": {}, **{f"node_modules/{name}": {"version": "4.0.542"}
                                    for name in ("remotion", "@remotion/bundler", "@remotion/renderer")},
                         "node_modules/react": {"version": "19.3.1"}, "node_modules/react-dom": {"version": "19.3.1"}}}
    write(folder / "package-lock.json", json.dumps(lock))
    monkeypatch.setattr(scene_render, "scenes_dir", lambda: folder)
    assert not scene_render.modules_installed()
    for path, entry in lock["packages"].items():
        if path:
            write(folder / path / "package.json", json.dumps({"version": entry["version"]}))
    assert scene_render.modules_installed()
    write(folder / "node_modules/remotion/package.json", json.dumps({"version": "4.0.541"}))
    assert not scene_render.modules_installed()


def test_the_browser_counts_as_there_when_its_program_is(tmp_path, monkeypatch):
    folder = tmp_path / "scenes"
    monkeypatch.setattr(scene_render, "scenes_dir", lambda: folder)
    assert not scene_render.browser_installed()
    write(folder / "node_modules/.remotion/chrome-headless-shell/platform/chrome-headless-shell-platform/x", "")
    assert not scene_render.browser_installed()
    write(folder / "node_modules/.remotion/chrome-headless-shell/platform/chrome-headless-shell-platform/"
                   "chrome-headless-shell", "")
    assert scene_render.browser_installed()


@pytest.mark.parametrize("node, version, modules, browser, expected", [
    ("/stand-in/node", (23, 7, 0), True, True, ("/stand-in/node", True, True, [])),
    ("/stand-in/node", (18, 17, 1), True, True,
     (None, True, True, ["node 18.17.1 is older than 18.18: install a newer Node.js"])),
    (None, None, False, False,
     (None, False, False, ["node (Node.js 18.18 or later) not found: install it, then run uv run pipeline setup",
                           "the Remotion modules or their browser are missing: run uv run pipeline setup"])),
])
def test_the_remotion_items_of_doctor(monkeypatch, node, version, modules, browser, expected):
    monkeypatch.setattr(scene_render, "node_bin", lambda: node)
    monkeypatch.setattr(scene_render, "node_version", lambda path: version)
    monkeypatch.setattr(scene_render, "modules_installed", lambda: modules)
    monkeypatch.setattr(scene_render, "browser_installed", lambda: browser)
    items, warnings = doctor_items()
    assert (items["node"], items["remotion"], items["remotion_browser"], warnings) == expected


def test_doctor_reports_the_remotion_items_and_needs_them(monkeypatch):
    monkeypatch.setattr(scene_render, "doctor_items",
                        lambda: ({"node": None, "remotion": True, "remotion_browser": True}, ["a node warning"]))
    report = assets.doctor()
    assert (report["node"], report["remotion"], report["remotion_browser"]) == (None, True, True)
    assert "a node warning" in report["warnings"]
    assert report["ok"] is False


# ---- a real render ---------------------------------------------------------------------------------------------


def test_a_real_render_through_the_cache(project):
    from test_scene_cards import requirements_or_skip  # skips without node, the modules, the browser or the fonts

    requirements_or_skip()
    from pipeline.media import ffprobe_bin

    style = load_style()
    clips = render_scenes(project, [placed(0, frames=40)], style)
    (_, clip), = clips
    probe = subprocess.run([ffprobe_bin(), "-v", "error", "-count_frames", "-show_entries", "stream=nb_read_frames",
                            "-of", "csv=p=0", str(clip)], capture_output=True, text=True, check=True, timeout=120)
    assert probe.stdout.strip() == "40"
    assert not (project.work / "scenes" / "tmp").exists()
    assert render_scenes(project, [placed(0, frames=40)], style, run=refuse) == clips


def test_a_real_run_of_render_mjs_writes_no_bundle_cache(project):
    """Runs that bundle at the same time (the review page drawing stills while a storyboard or render runs, or two
    projects at once) would share one bundle cache under scenes/node_modules/.cache, and the bundler aborts a run when
    another one writes that cache at the same moment; so render.mjs bundles without a cache, and a run leaves it as it
    was."""
    from test_scene_cards import requirements_or_skip  # skips without node, the modules, the browser or the fonts

    requirements_or_skip()
    cache = scene_render.scenes_dir() / "node_modules" / ".cache"

    def files() -> dict[Path, int]:
        return {f: f.stat().st_mtime_ns for f in cache.rglob("*") if f.is_file()} if cache.exists() else {}

    before = files()
    (_, still), = render_stills(project, [placed(0)], load_style())
    assert still.is_file()
    assert files() == before


def test_a_real_still_is_written_in_the_runs_own_folder_first(project):
    """Two runs may draw the same still at once (the review page and a storyboard, say). Each writes the still in its
    own still-tmp- folder and renames it into work/scenes when it is complete, so neither renames, overwrites or
    removes a file the other is writing: here, a file next to the still under the name of a part file stands for one."""
    from test_scene_cards import requirements_or_skip  # skips without node, the modules, the browser or the fonts

    requirements_or_skip()
    style = load_style()
    (_, still), = still_paths(project, [placed(0)], style)
    other = still.with_name(f"{still.stem}.part.png")
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another run's part file")
    render_stills(project, [placed(0)], style)
    assert other.read_bytes() == b"another run's part file"
    assert still.read_bytes().startswith(b"\x89PNG")

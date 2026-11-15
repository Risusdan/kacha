"""Rendering the animation cards of an edit, and the Remotion items of setup and doctor
(docs/superpowers/specs/2026-11-15-remotion-foundation-design.md, section 7).

Each scene that shows (scenes.scene_spans) becomes a clip of its card, its params, its length in frames and where
the card goes on the zone (its align, pipeline.card_place), which scenes/render.mjs renders to transparent ProRes
4444. A clip is kept as work/scenes/<key>.mov, the key being a sha256 of the card, the params, the frames, the
align, style_digest (style.json and the two fonts) and sources_digest
(scenes/src, render.mjs and package-lock.json): a clip whose file is there is not rendered again. The clips that are
missing are rendered by one run of render.mjs (one bundle, one browser), with TMPDIR at work/scenes/tmp, which is
removed afterwards, as Remotion leaves its bundle there. Node, the modules and the browser are needed only when a
clip is missing, and every character of the params must be in the style's fonts before Node runs.

A still is the card at rest where it goes on the zone (docs/superpowers/specs/2026-11-15-card-previews-design.md):
frame STILL_FRAME of the card STILL_FRAMES long, the last frame before its exit, as a transparent 1080x1920 PNG,
kept as work/scenes/<still_key>.png. Its key has no reel length: a still is the same however long the card shows.
render_stills renders the missing ones in one run of render.mjs, with TMPDIR and the jobs in a folder of its own,
work/scenes/still-tmp-<random>, so that the review page and render can run at once; it removes that folder
afterwards and, before it starts, any still-tmp- folder left over for more than STALE_TMP_S (an interrupted run's).

setup installs the modules with `npm ci` (from the committed package-lock.json, install scripts off) and downloads
the browser with scenes/ensure-browser.mjs, each only when it is missing. `npm ci` replaces scenes/node_modules
whole, the browser in it too.
"""

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from pipeline.captions import missing_glyphs
from pipeline.errors import PipelineError
from pipeline.paths import repo_root
from pipeline.project import Project
from pipeline.safe_delete import safe_rmtree
from pipeline.scenes import Placed, scene_texts
from pipeline.style import font_path

MIN_NODE = (18, 18)
LOCKED = ("remotion", "@remotion/bundler", "@remotion/renderer", "react", "react-dom")
RENDER_TIMEOUT_S = 3600
SETUP_TIMEOUT_S = 1800
NODE_MISSING = "node (Node.js 18.18 or later) not found: install it, then run uv run pipeline setup"
TOOLS_MISSING = "the Remotion modules or their browser are missing: run uv run pipeline setup"
STILL_FRAMES = 90  # the length of the card a still is taken from
STILL_FRAME = 81  # its last frame before the 8-frame exit (scenes/src/timing.js): every card is at rest then
STILL_TMP = "still-tmp-"  # the prefix of the folder of a run of render_stills
STALE_TMP_S = 3600  # a still-tmp- folder older than this is an interrupted run's (RENDER_TIMEOUT_S)
STILLS_TIMEOUT_S = 120  # a run of render_stills ends after this: a page waits for it (a few seconds is usual)

Run = Callable[..., subprocess.CompletedProcess]


def scenes_dir() -> Path:
    return repo_root() / "scenes"


def node_bin() -> str | None:
    return shutil.which("node")


def npm_bin() -> str | None:
    return shutil.which("npm")


def node_version(node: str, run: Run = subprocess.run) -> tuple[int, ...] | None:
    """The version `node --version` reports, or None when it cannot be run or says something else."""
    try:
        done = run([node, "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", done.stdout or "")
    return tuple(int(group) for group in match.groups()) if match else None


def modules_installed() -> bool:
    """Whether scenes/node_modules holds the direct dependencies at the versions package-lock.json locks."""
    folder = scenes_dir()
    try:
        lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))["packages"]
        for name in LOCKED:
            installed = json.loads((folder / "node_modules" / name / "package.json").read_text(encoding="utf-8"))
            if installed.get("version") != lock[f"node_modules/{name}"]["version"]:
                return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


def browser_installed() -> bool:
    """Whether the browser Remotion renders with is under scenes/node_modules/.remotion."""
    root = scenes_dir() / "node_modules" / ".remotion" / "chrome-headless-shell"
    return any(path.is_file() for path in root.glob("*/*/chrome-headless-shell*"))


def _node_problem(node: str | None, version: tuple[int, ...] | None) -> str | None:
    if node is None or version is None:
        return NODE_MISSING
    if version[:2] < MIN_NODE:
        return f"node {'.'.join(map(str, version))} is older than {'.'.join(map(str, MIN_NODE))}: install a newer Node.js"
    return None


def _require_node() -> str:
    node = node_bin()
    problem = _node_problem(node, node_version(node) if node else None)
    if problem == NODE_MISSING:
        raise PipelineError(problem)
    if problem:
        raise PipelineError(f"{problem}, then run uv run pipeline setup")
    return node


def doctor_items() -> tuple[dict, list[str]]:
    """({"node": its path or None, "remotion": bool, "remotion_browser": bool}, warnings) for doctor. node is None
    when it is missing or older than MIN_NODE."""
    node = node_bin()
    problem = _node_problem(node, node_version(node) if node else None)
    warnings = [problem] if problem else []
    modules, browser = modules_installed(), browser_installed()
    if not (modules and browser):
        warnings.append(TOOLS_MISSING)
    return {"node": None if problem else node, "remotion": modules, "remotion_browser": browser}, warnings


def _run_setup(run: Run, command: list[str], what: str) -> None:
    try:
        done = run(command, cwd=scenes_dir(), capture_output=True, text=True, timeout=SETUP_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as error:
        raise PipelineError(f"cannot run {what}: {error}") from error
    if done.returncode != 0:
        reason = _stderr_reason(done.stderr)
        detail = f": {reason}" if reason else ""
        raise PipelineError(f"{what} failed with exit code {done.returncode}{detail}")


def setup_scenes(run: Run = subprocess.run) -> dict:
    """Install the modules (npm ci) and download the browser, each only when it is missing. Returns
    {"remotion": bool, "remotion_browser": bool} as found afterwards."""
    if modules_installed() and browser_installed():
        return {"remotion": True, "remotion_browser": True}
    node = _require_node()
    if not modules_installed():
        npm = npm_bin()
        if npm is None:
            raise PipelineError("npm not found: it comes with Node.js, install Node.js 18.18 or later")
        _run_setup(run, [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], "npm ci")
    if not browser_installed():
        _run_setup(run, [node, str(scenes_dir() / "ensure-browser.mjs")], "the browser download")
    return {"remotion": modules_installed(), "remotion_browser": browser_installed()}


# ---- the cache ------------------------------------------------------------------------------------------------


def _digest(parts: list[tuple[str, bytes]]) -> str:
    sha = hashlib.sha256()
    for name, data in parts:
        sha.update(f"{name}\0{len(data)}\0".encode())
        sha.update(data)
    return sha.hexdigest()


def source_files(folder: Path) -> list[Path]:
    """What makes the cards look as they do: every file under `folder`/src, in order, then render.mjs and
    package-lock.json. A hidden file or folder (.DS_Store, an editor's .swp) and a backup ending in ~ are left out."""
    src = folder / "src"
    files = sorted(
        path for path in src.rglob("*")
        if path.is_file() and not path.name.endswith("~")
        and not any(part.startswith(".") for part in path.relative_to(src).parts)
    )
    return files + [folder / "render.mjs", folder / "package-lock.json"]


def sources_digest(folder: Path) -> str:
    """A sha256 of the source_files of `folder`, with their paths."""
    return _digest([(path.relative_to(folder).as_posix(), path.read_bytes()) for path in source_files(folder)])


def source_inputs() -> dict[str, Path]:
    """The source_files of scenes/ as inputs of a step, each named scenes/<its path>: what render and the storyboard
    read when the reel shows a scene."""
    folder = scenes_dir()
    return {f"scenes/{path.relative_to(folder).as_posix()}": path for path in source_files(folder)}


def style_digest(style: dict) -> str:
    """A sha256 of the style's style.json and its regular and bold fonts."""
    files = [("style.json", style["dir"] / "style.json")]
    files += [(weight, font_path(style, weight)) for weight in ("regular", "bold")]
    return _digest([(name, path.read_bytes()) for name, path in files])


def cache_key(card: str, params: dict, frames: int, align: str, style_hash: str, source_hash: str) -> str:
    document = {"card": card, "params": params, "frames": frames, "align": align, "style": style_hash,
                "sources": source_hash}
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def still_key(card: str, params: dict, align: str, style_hash: str, source_hash: str) -> str:
    document = {"kind": "still", "card": card, "params": params, "align": align, "frames": STILL_FRAMES,
                "frame": STILL_FRAME, "style": style_hash, "sources": source_hash}
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ---- rendering ------------------------------------------------------------------------------------------------


def _check_glyphs(placed: list[Placed], style: dict) -> None:
    """Refuse a style without the animation zone, and a character of the params that the style's fonts lack."""
    if "scenes" not in style:
        raise PipelineError(f"style {style['dir'].name} has no animation zone (scenes in style.json)")
    text = "".join(text for item in placed for text in scene_texts(item.scene))
    for weight in ("bold", "regular"):
        font = font_path(style, weight)
        missing = sorted(missing_glyphs(text, font))
        if missing:
            raise PipelineError(
                f"the card font {font.name} has no glyph for: " + " ".join(f"{c} (U+{ord(c):04X})" for c in missing),
                characters=missing,
            )


def _require_tools() -> str:
    node = _require_node()
    if not modules_installed():
        raise PipelineError("scenes/node_modules is not installed at the locked versions: run uv run pipeline setup")
    if not browser_installed():
        raise PipelineError("the browser Remotion renders with is not downloaded: run uv run pipeline setup")
    return node


def _clips(p: Project, placed: list[Placed], style: dict) -> list[tuple[Placed, Path]]:
    style_hash, source_hash = style_digest(style), sources_digest(scenes_dir())
    folder = p.work / "scenes"
    clips = []
    for item in placed:
        key = cache_key(item.scene["card"], item.scene["params"], item.frames, item.align, style_hash, source_hash)
        clips.append((item, folder / f"{key}.mov"))
    return clips


def check_renderable(p: Project, placed: list[Placed], style: dict) -> None:
    """Refuse what render_scenes would refuse before it runs Node, without writing anything: a character of the
    params that the style's fonts lack, and, when a clip is not there yet, a missing node, modules or browser.
    Reads nothing of scenes/ when `placed` is empty."""
    if not placed:
        return
    _check_glyphs(placed, style)
    if any(not clip.is_file() for _, clip in _clips(p, placed, style)):
        _require_tools()


def still_paths(p: Project, placed: list[Placed], style: dict) -> list[tuple[Placed, Path]]:
    """Each of `placed` with the path of its still under work/scenes, whether it is there or not."""
    if not placed:
        return []
    style_hash, source_hash = style_digest(style), sources_digest(scenes_dir())
    folder = p.work / "scenes"
    paths = []
    for item in placed:
        key = still_key(item.scene["card"], item.scene["params"], item.align, style_hash, source_hash)
        paths.append((item, folder / f"{key}.png"))
    return paths


def render_stills(p: Project, placed: list[Placed], style: dict, run: Run = subprocess.run) -> list[tuple[Placed, Path]]:
    """Each of `placed` with its still under work/scenes, rendering the stills that are not there yet; refuses
    what render_scenes refuses before Node runs."""
    if not placed:
        return []
    _check_glyphs(placed, style)
    stills = still_paths(p, placed, style)
    missing: dict[Path, Placed] = {}
    for item, still in stills:
        if not still.is_file():
            missing.setdefault(still, item)
    if not missing:
        return stills
    node = _require_tools()
    folder = p.work / "scenes"
    folder.mkdir(parents=True, exist_ok=True)
    for left in folder.glob(f"{STILL_TMP}*"):
        with contextlib.suppress(FileNotFoundError):  # another run that started at the same time removed it first
            if left.is_dir() and left.stat().st_mtime < time.time() - STALE_TMP_S:
                safe_rmtree(left, allowed_root=p.dir)
    tmp = Path(tempfile.mkdtemp(prefix=STILL_TMP, dir=folder))
    try:
        jobs = [{"kind": "still", "card": item.scene["card"], "params": item.scene["params"], "frames": STILL_FRAMES,
                 "frame": STILL_FRAME, "align": item.align, "out": str(still)}
                for still, item in missing.items()]
        _run_render_mjs(p, node, jobs, tmp / "jobs.json", tmp, style, p.work / "stills.log", run,
                        "the card previews failed to render", STILLS_TIMEOUT_S)
    finally:
        safe_rmtree(tmp, allowed_root=p.dir)
    unwritten = [still for still in missing if not still.is_file()]
    if unwritten:
        raise PipelineError(f"the card previews failed to render: {unwritten[0].name} was not written",
                            log=str(p.work / "stills.log"))
    return stills


def render_scenes(p: Project, placed: list[Placed], style: dict, run: Run = subprocess.run) -> list[tuple[Placed, Path]]:
    """Each of `placed` with its clip under work/scenes, rendering the clips that are not there yet."""
    if not placed:
        return []
    _check_glyphs(placed, style)
    clips = _clips(p, placed, style)
    missing: dict[Path, Placed] = {}
    for item, clip in clips:
        if not clip.is_file():
            missing.setdefault(clip, item)
    if missing:
        _render(p, p.work / "scenes", missing, style, run)
    return clips


def _render(p: Project, folder: Path, missing: dict[Path, Placed], style: dict, run: Run) -> None:
    node = _require_tools()
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "tmp"
    if tmp.exists():
        safe_rmtree(tmp, allowed_root=p.dir)
    tmp.mkdir()
    jobs = [{"card": item.scene["card"], "params": item.scene["params"], "frames": item.frames,
             "align": item.align, "out": str(clip)}
            for clip, item in missing.items()]
    log = p.work / "scenes.log"
    try:
        _run_render_mjs(p, node, jobs, folder / "jobs.json", tmp, style, log, run,
                        "the animation cards failed to render", RENDER_TIMEOUT_S)
    finally:
        safe_rmtree(tmp, allowed_root=p.dir)
    unwritten = [clip for clip in missing if not clip.is_file()]
    if unwritten:
        raise PipelineError(f"the animation cards failed to render: {unwritten[0].name} was not written",
                            log=str(log))


def _run_render_mjs(p: Project, node: str, jobs: list[dict], jobs_path: Path, tmp: Path, style: dict, log: Path,
                    run: Run, failed: str, timeout_s: float) -> None:
    """Write `jobs` with the style's tokens to `jobs_path`, run render.mjs on it with TMPDIR at `tmp` for at most
    `timeout_s` seconds, and keep what it printed in `log`; a failure is a PipelineError that starts with `failed`."""
    tokens = {**style["scenes"], "fonts": {"regular": style["fonts"]["regular"], "bold": style["fonts"]["bold"]}}
    jobs_path.write_text(json.dumps({"style_dir": str(style["dir"]), "style": tokens, "jobs": jobs},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        done = run([node, str(scenes_dir() / "render.mjs"), str(jobs_path)], cwd=scenes_dir(),
                   env={**os.environ, "TMPDIR": str(tmp)}, capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError) as error:
        raise PipelineError(f"cannot run scenes/render.mjs: {error}") from error
    log.write_text((done.stdout or "") + (done.stderr or ""), encoding="utf-8")
    if done.returncode != 0:
        raise PipelineError(f"{failed}: {_failure(done)}", log=str(log))


def _failure(done: subprocess.CompletedProcess) -> str:
    """What render.mjs said went wrong: the error of its last failure line, else the last line of its stderr."""
    for line in reversed((done.stdout or "").splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and result.get("ok") is False and result.get("error"):
            return str(result["error"])
    return _stderr_reason(done.stderr) or f"exit code {done.returncode}"


def _stderr_reason(stderr: str | None) -> str:
    """The line of a failed node or npm run that says what went wrong: the first that names an error (`TypeError:
    …`, `Error [CODE]: …`, `npm error …` other than npm's pointer to its log), else the last line that is not node's
    closing `Node.js v…`, else ""."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in lines:
        if re.match(r"\w*Error\b", line) or (line.startswith("npm error") and "A complete log" not in line):
            return line
    rest = [line for line in lines if not re.fullmatch(r"Node\.js v[\d.]+", line)]
    return rest[-1] if rest else ""

"""Project folders, state.json, step skipping and gate approval.

A project lives in <projects_root>/<date>-<slug>/ with work/, review/, out/, state.json and
project.md. It is built in <projects_root>/.new-<date>-<slug>/ and renamed when complete, so a
crash never leaves a half-built project behind. Every pipeline step runs through run_step, which
skips it when its inputs have not changed and its outputs are still there. An input counts as
unchanged when its size and modification time are the recorded ones (its recorded hash is then
used without reading the file), or else when its hash is the recorded one. Project.dir is always
a resolved path, because safe_rmtree refuses targets reached through a symbolic link.
"""

import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

from pipeline.errors import PipelineError
from pipeline.paths import projects_root
from pipeline.safe_delete import safe_rmtree

STATE_VERSION = 1
# The format version of the file each of these steps writes (transcript.json, trim.json). run_step records it,
# and a record of another version (or of none, as phase 1 wrote them) is out of date: the step runs again.
FORMAT_VERSIONS: dict[str, int] = {"transcribe": 2, "trim": 2}
GATES = ("gate1",)
EARLY_STEPS = ("prep", "transcribe", "trim")  # in the order of the flow; the storyboard is built on their results
_STATE_KEYS = ("source", "steps", "gates")
_EDIT_FILES = ("edit", "transcript", "trim")  # the files edit_checked in state.json holds a sha256 of
LOCK_FILE = "output.lock"  # in work/: the file ProjectLock locks
_TEMP_TRIES = 100  # write_text_atomically gives up after this many temp names that are taken
PROJECT_BUSY_ERROR = "another storyboard, approve or render is running for this project: wait until it ends"

_SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_PROJECT_MD = "# {name}\n\n## 決定\n\n## 使用者的意見\n"


@dataclass(frozen=True)
class Project:
    name: str
    dir: Path

    @property
    def work(self) -> Path:
        return self.dir / "work"

    @property
    def review(self) -> Path:
        return self.dir / "review"

    @property
    def out(self) -> Path:
        return self.dir / "out"

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def edit_path(self) -> Path:
        return self.work / "edit.json"

    @property
    def md_path(self) -> Path:
        return self.dir / "project.md"


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _projects_dir() -> Path:
    return projects_root().resolve()


def _check_date(date: str) -> None:
    if not _DATE.fullmatch(date):
        raise PipelineError(f"date must look like YYYY-MM-DD, got {date!r}")
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise PipelineError(f"not a real date: {date!r}") from None


def _source_file(path: Path, what: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise PipelineError(f"{what} file not found: {str(path)!r}")
    return resolved


def create_project(video: Path, script: Path, slug: str, date: str | None = None) -> Project:
    """Create the folder layout and state.json for a new project. The video and the script are
    only read: their paths and hashes go into state.json, nothing is copied or changed."""
    if not _SLUG.fullmatch(slug):
        raise PipelineError(f"slug must be lowercase letters and digits joined by single hyphens, got {slug!r}")
    date = datetime.date.today().isoformat() if date is None else date
    _check_date(date)
    video_path = _source_file(video, "video")
    script_path = _source_file(script, "script")

    name = f"{date}-{slug}"
    root = _projects_dir()
    folder = root / name
    if folder.exists():
        raise PipelineError(f"project already exists: {name}")
    video_sha256 = file_sha256(video_path)
    script_sha256 = file_sha256(script_path)

    root.mkdir(parents=True, exist_ok=True)
    building = root / f".new-{name}"
    # A folder that is already there was left by a run that crashed before it could clean up, or belongs to
    # another run for the same name that started together with this one: this run deletes its folder.
    if os.path.lexists(building):
        safe_rmtree(building, allowed_root=root)
    try:
        building.mkdir()
    except FileExistsError:  # another run is building the same project right now
        raise PipelineError(f"project already exists: {name}") from None
    try:
        staging = Project(name, building)
        for sub in (staging.work, staging.review, staging.out):
            sub.mkdir()
        save_state(
            staging,
            {
                "version": STATE_VERSION,
                "slug": slug,
                "name": name,
                "created_at": _now(),
                "source": {
                    "video": str(video_path),
                    "video_sha256": video_sha256,
                    "script": str(script_path),
                    "script_sha256": script_sha256,
                },
                "steps": {},
                "gates": {},
            },
        )
        staging.md_path.write_text(_PROJECT_MD.format(name=name), encoding="utf-8")
        if os.path.lexists(folder):  # created by someone else while this one was being built
            raise PipelineError(f"project already exists: {name}")
        building.rename(folder)  # the last step: the project appears whole or not at all
    except BaseException:
        safe_rmtree(building, allowed_root=root)
        raise
    return Project(name, folder)


def list_projects() -> list[Project]:
    """Every project folder under the projects root, sorted by name. A folder counts when it is
    a real folder (not a symbolic link) whose name does not start with a dot (so not a .new-<name>
    that is still being built) and that holds a state.json."""
    root = _projects_dir()
    if not root.is_dir():
        return []
    return [
        Project(child.name, child)
        for child in sorted(root.iterdir())
        if not child.name.startswith(".")
        and child.is_dir()
        and not child.is_symlink()
        and (child / "state.json").is_file()
    ]


def load_project(name: str) -> Project:
    """Find a project by its full folder name, or by its slug when only one project has it."""
    projects = list_projects()
    for project in projects:
        if project.name == name:
            return project
    by_slug = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-" + re.escape(name))
    matches = [project for project in projects if by_slug.fullmatch(project.name)]
    if not matches:
        raise PipelineError(f"project not found: {name!r}")
    if len(matches) > 1:
        raise PipelineError(
            f"ambiguous project name {name!r}, use the full name",
            matches=[project.name for project in matches],
        )
    return matches[0]


def load_state(p: Project) -> dict:
    """state.json of `p`. A file that is not JSON, not an object, lacks source, steps or gates, or holds
    something other than an object for steps, gates, a step record or a gate is reported as unreadable
    (a PipelineError), never as a raw JSON, key or type error."""

    def unreadable(reason: str) -> PipelineError:
        return PipelineError(f"state.json of project {p.name!r} is unreadable: {reason}")

    try:
        text = p.state_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PipelineError(f"state.json not found for project {p.name!r}") from None
    except (OSError, ValueError) as error:  # ValueError: bytes that are not UTF-8
        raise unreadable(str(error)) from error
    try:
        state = json.loads(text)
    except ValueError as error:
        raise unreadable(f"not valid JSON ({error})") from error
    if not isinstance(state, dict):
        raise unreadable("not a JSON object")
    missing = [key for key in _STATE_KEYS if key not in state]
    if missing:
        raise unreadable(f"missing {', '.join(repr(key) for key in missing)}")
    for key in ("steps", "gates"):
        if not isinstance(state[key], dict):
            raise unreadable(f"{key!r} is not a JSON object")
        for name, record in state[key].items():
            if not isinstance(record, dict):
                raise unreadable(f"{key!r} entry {name!r} is not a JSON object")
    return state


def write_text_atomically(path: Path, text: str) -> None:
    """`path` becomes `text` (UTF-8) all at once: the text goes into a temp file of its own beside it,
    <name>.<random>.tmp, which then replaces it. The name is unique (made with O_EXCL), so two writers at once
    never share a temp file. It is made with mode 0o666 less the umask, as a plain open makes a file (mkstemp would
    make it 0600), so `path` keeps the mode it has always had. When anything fails, the temp file is removed, `path`
    is as it was, and the error goes on."""
    for _ in range(_TEMP_TRIES):
        temp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"no free temp name beside {path} after {_TEMP_TRIES} tries")
    try:
        with open(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(temp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp)
        raise


def save_state(p: Project, state: dict) -> None:
    """Write state.json through a temp file of its own in the same folder, then replace it in one step
    (write_text_atomically)."""
    write_text_atomically(p.state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


class ProjectBusy(PipelineError):
    """Another storyboard, approve or render holds the lock of the project (ProjectLock)."""

    def __init__(self, message: str = PROJECT_BUSY_ERROR, /, **extra):
        super().__init__(message, **extra)


class ProjectLock:
    """One storyboard, approve or render at a time in a project: they share fixed temporary names (review/gate1,
    out/final.tmp.mp4). acquire() takes an exclusive flock on work/LOCK_FILE without waiting, creating the file
    when it is missing, and raises ProjectBusy while another holder has it; release() lets it go. Two acquires
    conflict even in one process, as each opens the file anew (flock locks an open file, not a process): so the
    review page's job, which runs the steps in the server's process, and a command from the chat exclude each other.
    The file is never deleted: the system lets the lock go when the process that holds it ends, so a crash leaves
    no stale lock. The lock is taken where a command or the review page's job starts, never inside the steps.
    POSIX only, as the render is."""

    def __init__(self, p: Project):
        self.path = p.work / LOCK_FILE
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise PipelineError(f"{self.path} is locked already by this holder")
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o666)
        except OSError as error:
            raise PipelineError(f"cannot open the lock file {self.path}: {error.strerror or error}") from error
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ProjectBusy() from None
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        """Let the lock go (closing the file does). Releasing a lock that is not held does nothing."""
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def __enter__(self) -> "ProjectLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


def _recorded_path(p: Project, path: Path) -> str:
    try:
        return Path(path).relative_to(p.dir).as_posix()
    except ValueError:
        return str(path)


def _output_path(p: Project, recorded: str) -> Path:
    return p.dir / recorded  # an absolute `recorded` replaces p.dir


# A per-call cache of file hashes keyed by path. The source video is hundreds of MB and several
# steps list it as an input, so one status or next_step call hashes each file at most once.
HashCache = dict[str, str]


def _input_entry(path: Path | str, recorded: dict | None, cache: HashCache) -> dict | None:
    """What a step input record holds for the file at `path` now: path, sha256, size and mtime_ns. None
    when it is not a file.

    When `recorded` (an earlier record of the same path) has the same size and mtime_ns, its sha256 is
    taken as it is and the file is not read. Otherwise the file is hashed, once per call through `cache`.
    A record without size and mtime_ns (written before they were recorded) always leads to hashing."""
    key = str(path)
    try:
        info = Path(path).stat()
    except OSError:
        return None
    if not S_ISREG(info.st_mode):
        return None
    sha256 = None
    if recorded is not None and recorded.get("path") == key:
        if (recorded.get("size"), recorded.get("mtime_ns")) == (info.st_size, info.st_mtime_ns):
            sha256 = recorded.get("sha256")
    if sha256 is None:
        if key not in cache:
            cache[key] = file_sha256(Path(path))
        sha256 = cache[key]
    return {"path": key, "sha256": sha256, "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def _current_sha256(path: Path | str, recorded: dict | None, cache: HashCache) -> str | None:
    """The sha256 of the file at `path` now, or None when it is not a file (see _input_entry)."""
    entry = _input_entry(path, recorded, cache)
    return None if entry is None else entry["sha256"]


def _refresh_recorded_stat(p: Project, state: dict, step: str, current: dict[str, dict]) -> None:
    """Write the size and mtime_ns of each input as they are now into the record of `step`, where a record
    lacks them (written before they were recorded) or holds others (the file was touched, its content is
    the same). Nothing else in the record changes, and state.json is written only when something did."""
    changed = False
    for key, entry in current.items():
        recorded = state["steps"][step]["inputs"][key]
        if recorded.get("path") != entry["path"]:
            continue
        for field in ("size", "mtime_ns"):
            if recorded.get(field) != entry[field]:
                recorded[field] = entry[field]
                changed = True
    if changed:
        save_state(p, state)


def _format_current(step: str, record: dict) -> bool:
    """Whether `record` was made by the current format of `step`. A step without a format version always is."""
    return step not in FORMAT_VERSIONS or record.get("version") == FORMAT_VERSIONS[step]


def refuse_older_document(path: Path, document: object, step: str, version: int) -> None:
    """Raise a PipelineError when `document`, read from the file `path` that `step` writes, has a "version"
    other than `version`: it was made by an older pipeline. A document without a "version" (one written by
    hand, as the tests do) is not refused."""
    if isinstance(document, dict) and "version" in document and document["version"] != version:
        raise PipelineError(f"{path.name} was made by an older pipeline: run {step} again")


def run_step(
    p: Project,
    step: str,
    inputs: dict[str, Path],
    outputs: list[Path],
    fn: Callable[[], dict],
    force: bool = False,
) -> dict:
    """Run `fn` unless `step` already ran on the same input contents and its outputs are still
    there. Returns {"skipped": True}, or the result of `fn` plus "skipped": False.

    Each input is recorded with its path, sha256, size and mtime_ns. An input whose size and mtime_ns
    are the recorded ones is not read again; its recorded sha256 stands. A step that is skipped writes the
    current size and mtime_ns of its inputs into its record where they are missing or differ, so that the
    next run or status does not read those files again. A step in FORMAT_VERSIONS records its version too,
    and a record of another version (or of none) is not skipped.

    The step record is dropped before `fn` runs and written again only when it returns, so a
    step that failed half way is never reported as done."""
    state = load_state(p)
    record = state["steps"].get(step)
    recorded_inputs = {} if force or record is None else record.get("inputs", {})
    cache: HashCache = {}
    current = {}
    for key, path in inputs.items():
        entry = _input_entry(path, recorded_inputs.get(key), cache)
        if entry is None:
            raise PipelineError(f"input {key!r} of step {step!r} not found: {str(path)!r}")
        current[key] = entry
    recorded_outputs = [_recorded_path(p, path) for path in outputs]

    if (
        not force
        and record is not None
        and _format_current(step, record)
        and {key: value["sha256"] for key, value in record.get("inputs", {}).items()}
        == {key: value["sha256"] for key, value in current.items()}
        and sorted(record.get("outputs", [])) == sorted(recorded_outputs)
        and all(Path(path).exists() for path in outputs)
    ):
        _refresh_recorded_stat(p, state, step, current)
        return {"skipped": True}

    if record is not None:
        del state["steps"][step]
        save_state(p, state)
    result = fn()
    state = load_state(p)
    state["steps"][step] = {
        "done_at": _now(),
        **({"version": FORMAT_VERSIONS[step]} if step in FORMAT_VERSIONS else {}),
        "inputs": current,
        "outputs": recorded_outputs,
    }
    save_state(p, state)
    return {**result, "skipped": False}


def _step_done(p: Project, state: dict, step: str, cache: HashCache) -> bool:
    """The step has a record made by the current format of its file (see FORMAT_VERSIONS), everything it
    produced is still there, and every file it read still has the content it had when the step ran."""
    record = state["steps"].get(step)
    if record is None or not _format_current(step, record):
        return False
    if not all(_output_path(p, out).exists() for out in record.get("outputs", [])):
        return False
    return all(
        _current_sha256(entry["path"], entry, cache) == entry["sha256"] for entry in record.get("inputs", {}).values()
    )


def step_done(p: Project, step: str) -> bool:
    """Whether `step` has run, its outputs are still there and its inputs are unchanged since."""
    return _step_done(p, load_state(p), step, {})


def _done_steps(p: Project, state: dict, cache: HashCache) -> dict[str, str]:
    return {step: record["done_at"] for step, record in state["steps"].items() if _step_done(p, state, step, cache)}


def done_steps(p: Project) -> dict[str, str]:
    """The finished steps (see step_done) and the time each one finished."""
    return _done_steps(p, load_state(p), {})


def _storyboard_is_current(p: Project, state: dict, cache: HashCache) -> bool:
    """Whether the storyboard step is done (see step_done) and ran on the edit.json that exists
    now, registered under the input name "edit"."""
    record = state["steps"].get("storyboard") or {}
    recorded = (record.get("inputs") or {}).get("edit") or {}
    edit_sha256 = _current_sha256(p.edit_path, recorded, cache)
    if edit_sha256 is None or recorded.get("sha256") != edit_sha256:
        return False
    return _step_done(p, state, "storyboard", cache)


def _storyboard_input_hashes(state: dict) -> dict[str, str]:
    """Input name -> sha256 of every file the storyboard step read, as its record holds them."""
    record = state["steps"].get("storyboard") or {}
    return {name: entry["sha256"] for name, entry in (record.get("inputs") or {}).items()}


def _edit_files(p: Project) -> dict[str, Path]:
    """The files validate checks edit.json against, under the names edit_checked in state.json uses."""
    return {"edit": p.edit_path, "transcript": p.work / "transcript.json", "trim": p.work / "trim.json"}


def _edit_hashes(p: Project, recorded: dict, cache: HashCache) -> dict[str, str | None]:
    """The sha256 of each file of _edit_files as it is now, None for one that is not there. `recorded`
    holds earlier input records by name (see _input_entry): a file with the size and mtime_ns of its
    record is not read again."""
    return {name: _current_sha256(path, recorded.get(name), cache) for name, path in _edit_files(p).items()}


def edit_file_hashes(p: Project) -> dict[str, str | None]:
    """The sha256 of edit.json, transcript.json and trim.json as they are now (None for a file that is not
    there), under "edit", "transcript" and "trim": what record_edit_checked takes. Every file is read."""
    return _edit_hashes(p, {}, {})


def _edit_checked(state: dict) -> dict[str, str] | None:
    """edit_checked of state.json, or None when there is none (a project from before validate recorded it)
    or it is not a record with a sha256 string for each of edit, transcript and trim."""
    record = state.get("edit_checked")
    if not isinstance(record, dict) or not all(isinstance(record.get(name), str) for name in _EDIT_FILES):
        return None
    return record


def record_edit_checked(p: Project, hashes: dict[str, str | None]) -> dict[str, str] | None:
    """Record in state.json that validate passed on the files with these hashes (edit_file_hashes, taken
    before the files were read, so that a file that changed meanwhile fails the next comparison). Returns
    the record it replaces, None when there was none or it was malformed."""
    state = load_state(p)
    previous = _edit_checked(state)
    state["edit_checked"] = dict(hashes)
    save_state(p, state)
    return previous


def _edit_stage(p: Project, state: dict, cache: HashCache) -> str | None:
    """Where edit.json stands once prep, transcribe and trim are done: "edit" when there is no edit.json or
    it was checked against another transcript.json or trim.json than the ones there are now (the trim and
    sentence ids in it may point somewhere else); "validate" when it was never checked, or is not the
    edit.json that was; None when the edit.json that is there was checked against the files that are there."""
    storyboard_inputs = (state["steps"].get("storyboard") or {}).get("inputs") or {}  # same names, same files
    now = _edit_hashes(p, storyboard_inputs, cache)
    if now["edit"] is None:
        return "edit"
    checked = _edit_checked(state)
    if checked is not None and (checked["transcript"], checked["trim"]) != (now["transcript"], now["trim"]):
        return "edit"
    if checked is None or checked["edit"] != now["edit"]:
        return "validate"
    return None


def edit_stage(p: Project) -> str | None:
    """"edit", "validate" or None, as next_step would say it for edit.json (see _edit_stage); the caller
    has made sure that prep, transcribe and trim are done."""
    return _edit_stage(p, load_state(p), {})


def _first_stale_step(p: Project, state: dict, cache: HashCache) -> str | None:
    return next((step for step in EARLY_STEPS if not _step_done(p, state, step, cache)), None)


def first_stale_step(p: Project) -> str | None:
    """The first of prep, transcribe and trim, in that order, that is not done (see step_done), or None
    when all three are. The storyboard and the gate stand on their results."""
    return _first_stale_step(p, load_state(p), {})


def approve_gate(p: Project, gate: str) -> dict:
    """Record that the user approved `gate`. prep, transcribe and trim must be done (see step_done), the
    first of them that is not is named in the error. gate1 also needs a storyboard made from the current
    edit.json: the storyboard step must have registered that file under the input name "edit", and its
    outputs must still be there.

    The record holds the hash of every input of the storyboard step, edit.json among them
    (storyboard_inputs): the user approved the storyboard as it was shown, which also depends on
    transcript.json, trim.json and the rest, not only on edit.json."""
    if gate not in GATES:
        raise PipelineError(f"unknown gate {gate!r}, expected one of {', '.join(GATES)}")
    state = load_state(p)
    cache: HashCache = {}
    stale = _first_stale_step(p, state, cache)
    if stale is not None:
        raise PipelineError(f"{stale} is out of date: run {stale} first")
    if not _storyboard_is_current(p, state, cache):
        raise PipelineError("run storyboard first")
    record = {
        "approved_at": _now(),
        "storyboard_inputs": _storyboard_input_hashes(state),
    }
    state["gates"][gate] = record
    save_state(p, state)
    return record


def _gate_ok(p: Project, state: dict, gate: str, cache: HashCache) -> bool:
    """The gate was approved, prep, transcribe and trim are done, the storyboard step is current, and the
    inputs it ran on are the ones that were recorded at approval. An approval without storyboard_inputs
    (made before they were recorded) does not count."""
    approved = state["gates"].get(gate)
    if approved is None or not approved.get("storyboard_inputs"):
        return False
    return (
        _first_stale_step(p, state, cache) is None
        and _storyboard_is_current(p, state, cache)
        and approved["storyboard_inputs"] == _storyboard_input_hashes(state)
    )


def gate_ok(p: Project, gate: str) -> bool:
    """Whether `gate` was approved for the storyboard as it stands now: the same edit.json and the same
    inputs of the storyboard step as when the user approved, with prep, transcribe and trim still done."""
    return _gate_ok(p, load_state(p), gate, {})


def _next_step(p: Project, state: dict, cache: HashCache) -> str:
    stale = _first_stale_step(p, state, cache)
    if stale is not None:
        return stale
    stage = _edit_stage(p, state, cache)
    if stage is not None:
        return stage
    if not _storyboard_is_current(p, state, cache):
        return "storyboard"
    if not _gate_ok(p, state, "gate1", cache):
        return "approve"
    if not _step_done(p, state, "render", cache):
        return "render"
    return "done"


def next_step(p: Project) -> str:
    """The step to do next: prep, transcribe, trim, edit (write work/edit.json, or go through it again:
    it was checked against another transcript.json or trim.json than the ones there are now), validate
    (edit.json was never checked, or has changed since), storyboard, approve, render or done. A step that
    is not done (see step_done) comes first, so a change to a file a step read sends the flow back to
    that step."""
    return _next_step(p, load_state(p), {})


def project_status(p: Project) -> dict:
    """What the status command reports for one project. Hashes each input file once."""
    state = load_state(p)
    cache: HashCache = {}
    return {
        "name": p.name,
        "next": _next_step(p, state, cache),
        "steps": _done_steps(p, state, cache),
        "gate1_ok": _gate_ok(p, state, "gate1", cache),
    }

import fcntl
import json
import os
import re
import stat
import threading
import time
from pathlib import Path

import pytest
from helpers import MEIJIA

from pipeline import cli as cli_module
from pipeline import project as project_module
from pipeline.cli import main
from pipeline.errors import PipelineError
from pipeline.project import (
    FORMAT_VERSIONS,
    LOCK_FILE,
    PROJECT_BUSY_ERROR,
    STATE_LOCK_FILE,
    Project,
    ProjectBusy,
    ProjectLock,
    approve_gate,
    create_project,
    done_steps,
    edit_file_hashes,
    file_sha256,
    first_stale_step,
    gate_ok,
    list_projects,
    load_project,
    load_state,
    next_step,
    project_status,
    run_step,
    save_state,
    step_done,
    update_state,
)
from pipeline.safe_delete import safe_rmtree
from pipeline.version import kacha_version


def _cli(capsys, *argv):
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _writes(path, text):
    """A step function that writes `text` to `path` and returns no result."""

    def fn():
        path.write_text(text)
        return {}

    return fn


def _finish_early_steps(project):
    """prep, transcribe and trim done. transcribe and trim each read one file that no storyboard reads.
    Returns (audio16k, audio48k), those two files."""
    audio16k, audio48k = project.work / "audio16k.wav", project.work / "audio48k.wav"
    audio16k.write_text("audio16k v1")
    audio48k.write_text("audio48k v1")
    run_step(project, "prep", {}, [], lambda: {})
    run_step(project, "transcribe", {"audio": audio16k}, [], lambda: {})
    run_step(project, "trim", {"audio": audio48k}, [], lambda: {})
    return audio16k, audio48k


def _checked(project, **changes):
    """Write into state.json the edit_checked that validate records for the files as they are now (a
    transcript.json or trim.json that is not there is written first). `changes` replace entries of it."""
    for name in ("transcript.json", "trim.json"):
        document = project.work / name
        if not document.exists():
            document.write_text(f"{name} v1")
    state = load_state(project)
    state["edit_checked"] = {
        "edit": file_sha256(project.edit_path),
        "transcript": file_sha256(project.work / "transcript.json"),
        "trim": file_sha256(project.work / "trim.json"),
    } | changes
    save_state(project, state)


def _forget_the_stat_of_every_input(project):
    """Rewrite state.json the way the first phase wrote it: no size and no mtime_ns in any input record."""
    state = load_state(project)
    for record in state["steps"].values():
        for entry in record["inputs"].values():
            del entry["size"], entry["mtime_ns"]
    save_state(project, state)


def _touch(path):
    """Give `path` another mtime a second later, whatever its content."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


# --- create_project -------------------------------------------------------------------


def test_new_creates_layout(inbox_files):
    p = create_project(*inbox_files, slug="coffee", date="2026-11-12")
    assert p.name == "2026-11-12-coffee"
    assert all((p.dir / d).is_dir() for d in ("work", "review", "out"))
    st = load_state(p)
    assert st["source"]["video_sha256"] == file_sha256(inbox_files[0]) and st["steps"] == {}
    assert p.md_path.read_text(encoding="utf-8").count("## ") == 2


def test_project_paths(project):
    assert project.work == project.dir / "work"
    assert project.review == project.dir / "review"
    assert project.out == project.dir / "out"
    assert project.state_path == project.dir / "state.json"
    assert project.edit_path == project.dir / "work" / "edit.json"
    assert project.md_path == project.dir / "project.md"


def test_state_records_the_source_files(inbox_files):
    video, script = inbox_files
    p = create_project(video, script, slug="coffee", date="2026-11-12")
    st = load_state(p)
    assert st["version"] == 1 and st["slug"] == "coffee" and st["name"] == "2026-11-12-coffee"
    assert st["source"] == {
        "video": str(video.resolve()),
        "video_sha256": file_sha256(video),
        "script": str(script.resolve()),
        "script_sha256": file_sha256(script),
    }
    assert st["steps"] == {} and st["gates"] == {}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", st["created_at"])


def test_project_md_has_the_two_sections(project):
    text = project.md_path.read_text(encoding="utf-8")
    assert text.startswith(f"# {project.name}\n")
    assert "## 決定" in text and "## 使用者的意見" in text


def test_default_date_is_today(inbox_files):
    p = create_project(*inbox_files, slug="c")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-c", p.name)


@pytest.mark.parametrize("slug", ["Coffee", "a b", "../x", "", "-a", "a-", "a--b", "a\n", "咖啡"])
def test_new_rejects_bad_slug(inbox_files, slug):
    with pytest.raises(PipelineError):
        create_project(*inbox_files, slug=slug)


@pytest.mark.parametrize("date", ["2026-13-01", "2026-02-30", "../x", "20261112", "2026-1-2", "", "２０２６-１１-１２"])
def test_new_rejects_bad_date(inbox_files, studio, date):
    with pytest.raises(PipelineError):
        create_project(*inbox_files, slug="c", date=date)
    assert list((studio / "projects").iterdir()) == []


def test_new_rejects_missing_source_files(inbox_files, studio):
    video, script = inbox_files
    with pytest.raises(PipelineError, match="video"):
        create_project(video.with_name("nope.mp4"), script, slug="c")
    with pytest.raises(PipelineError, match="script"):
        create_project(video, script.with_name("nope.md"), slug="c")
    assert list((studio / "projects").iterdir()) == []


def test_new_refuses_existing(inbox_files):
    create_project(*inbox_files, slug="c", date="2026-11-12")
    with pytest.raises(PipelineError, match="exists"):
        create_project(*inbox_files, slug="c", date="2026-11-12")


def _fail_when_project_md_is_written(monkeypatch):
    """Make the write of project.md, the last file create_project writes, raise OSError."""
    real_write_text = Path.write_text

    def failing_write_text(self, *args, **kwargs):
        if self.name == "project.md":
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)


def test_failed_create_leaves_nothing(inbox_files, studio, monkeypatch):
    with monkeypatch.context() as patch:
        _fail_when_project_md_is_written(patch)
        with pytest.raises(OSError, match="disk full"):
            create_project(*inbox_files, slug="c", date="2026-11-12")
    assert list((studio / "projects").iterdir()) == []  # neither 2026-11-12-c nor .new-2026-11-12-c
    assert create_project(*inbox_files, slug="c", date="2026-11-12").name == "2026-11-12-c"


def test_create_clears_a_leftover_new_folder(inbox_files, studio):
    leftover = studio / "projects" / ".new-2026-11-12-c"
    (leftover / "work").mkdir(parents=True)
    (leftover / "work" / "half-written.txt").write_text("from a crashed run")
    p = create_project(*inbox_files, slug="c", date="2026-11-12")
    assert not leftover.exists()
    assert [f.name for f in (studio / "projects").iterdir()] == [p.name]
    assert (p.dir / "state.json").is_file()


def test_the_new_folder_is_deleted_through_safe_rmtree_inside_the_projects_folder(inbox_files, studio, monkeypatch):
    calls = []
    real = project_module.safe_rmtree

    def spy(path, *, allowed_root, **kwargs):
        calls.append((Path(path), Path(allowed_root)))
        real(path, allowed_root=allowed_root, **kwargs)

    monkeypatch.setattr(project_module, "safe_rmtree", spy)
    building = (studio / "projects").resolve() / ".new-2026-11-12-c"
    building.mkdir()  # a leftover of a crashed run: deleted before the build starts
    with monkeypatch.context() as patch:
        _fail_when_project_md_is_written(patch)
        with pytest.raises(OSError):
            create_project(*inbox_files, slug="c", date="2026-11-12")  # deleted again after the failure
    assert calls == [(building, building.parent)] * 2
    assert not building.exists()


def test_new_leaves_inbox_untouched(inbox_files, studio):
    def listing():
        return sorted(str(f.relative_to(studio / "inbox")) for f in (studio / "inbox").rglob("*"))

    before = {f: (file_sha256(f), f.stat().st_mtime_ns) for f in inbox_files}
    names_before = listing()
    create_project(*inbox_files, slug="c")
    assert before == {f: (file_sha256(f), f.stat().st_mtime_ns) for f in inbox_files}
    assert listing() == names_before and names_before == ["a", "a/clip.mp4", "a/script.md"]


def test_new_creates_missing_projects_folder(inbox_files, tmp_path, monkeypatch):
    monkeypatch.setenv("REELS_STUDIO", str(tmp_path / "fresh-studio"))
    p = create_project(*inbox_files, slug="c", date="2026-11-12")
    assert p.dir.is_dir()


def test_project_dir_is_resolved_when_studio_is_reached_through_a_symlink(inbox_files, tmp_path, monkeypatch):
    real = tmp_path / "real-studio"
    real.mkdir()
    link = tmp_path / "linked-studio"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("REELS_STUDIO", str(link))
    p = create_project(*inbox_files, slug="c", date="2026-11-12")
    assert p.dir == p.dir.resolve()
    assert p.dir == real.resolve() / "projects" / "2026-11-12-c"
    assert load_project("c").dir == p.dir


def test_safe_rmtree_accepts_a_folder_of_a_project_in_a_symlinked_studio(inbox_files, tmp_path, monkeypatch):
    real = tmp_path / "real-studio"
    real.mkdir()
    link = tmp_path / "linked-studio"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("REELS_STUDIO", str(link))
    p = create_project(*inbox_files, slug="c", date="2026-11-12")
    gate_folder = p.review / "gate1"
    gate_folder.mkdir()
    deleted = []
    safe_rmtree(gate_folder, allowed_root=p.dir, remover=deleted.append)  # a recorder, nothing is deleted
    assert deleted == [gate_folder] and gate_folder.is_dir()


# --- load_project / list_projects -----------------------------------------------------


def test_load_project_by_full_name_or_slug(project):
    assert load_project(project.name) == project
    assert load_project("coffee") == project


def test_load_project_unknown_name(project):
    with pytest.raises(PipelineError, match="not found"):
        load_project("tea")


def test_load_project_never_leaves_the_projects_folder(project, studio):
    with pytest.raises(PipelineError, match="not found"):
        load_project("../projects/" + project.name)
    with pytest.raises(PipelineError, match="not found"):
        load_project("..")


def test_load_project_ambiguous_slug(inbox_files):
    create_project(*inbox_files, slug="coffee", date="2026-11-12")
    create_project(*inbox_files, slug="coffee", date="2026-11-13")
    with pytest.raises(PipelineError, match="ambiguous") as raised:
        load_project("coffee")
    assert raised.value.extra["matches"] == ["2026-11-12-coffee", "2026-11-13-coffee"]
    assert load_project("2026-11-13-coffee").name == "2026-11-13-coffee"


def test_load_project_prefers_the_exact_name_over_a_slug_match(inbox_files):
    a = create_project(*inbox_files, slug="coffee", date="2026-11-12")
    create_project(*inbox_files, slug="2026-11-12-coffee", date="2026-11-12")
    assert load_project("2026-11-12-coffee") == a


def test_list_projects_sorted_and_ignores_other_entries(inbox_files, studio):
    create_project(*inbox_files, slug="b", date="2026-11-13")
    create_project(*inbox_files, slug="a", date="2026-11-12")
    (studio / "projects" / "stray-folder").mkdir()
    (studio / "projects" / ".hidden").write_text("x")
    assert [p.name for p in list_projects()] == ["2026-11-12-a", "2026-11-13-b"]


def test_list_projects_skips_dot_folders(project, studio):
    leftover = studio / "projects" / ".new-x"
    leftover.mkdir()
    (leftover / "state.json").write_text("{}")
    assert [p.name for p in list_projects()] == [project.name]


def test_list_projects_skips_symlinked_folders(project, studio, tmp_path):
    (studio / "projects" / "2026-11-14-linked").symlink_to(project.dir, target_is_directory=True)
    assert [p.name for p in list_projects()] == [project.name]


def test_list_projects_without_a_projects_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("REELS_STUDIO", str(tmp_path / "no-such-studio"))
    assert list_projects() == []


# --- state.json -----------------------------------------------------------------------


def test_save_state_round_trips_and_leaves_no_stray_files(project):
    state = load_state(project)
    state["note"] = "咖啡"
    save_state(project, state)
    assert load_state(project)["note"] == "咖啡"
    assert "咖啡" in project.state_path.read_text(encoding="utf-8")
    assert sorted(f.name for f in project.dir.iterdir()) == ["out", "project.md", "review", "state.json", "work"]


def test_save_state_goes_through_a_temp_file(project, monkeypatch):
    before = project.state_path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_state(project, {"version": 1, "steps": {}, "gates": {}, "changed": True})
    assert project.state_path.read_bytes() == before
    assert sorted(f.name for f in project.dir.iterdir()) == ["out", "project.md", "review", "state.json", "work"]


def test_two_saves_of_state_json_use_temp_files_of_their_own(project, monkeypatch):
    """A validate from the chat and a change from the review page may save state.json at once: each writes a temp
    file of its own beside it, so neither replaces state.json with the other's half-written text."""
    temps = []
    replace = os.replace

    def record(source, target):
        temps.append(Path(source))
        replace(source, target)

    monkeypatch.setattr(os, "replace", record)
    save_state(project, load_state(project))
    save_state(project, load_state(project))
    assert len(temps) == 2 and temps[0] != temps[1]
    assert all(temp.parent == project.dir for temp in temps)
    assert sorted(f.name for f in project.dir.iterdir()) == ["out", "project.md", "review", "state.json", "work"]


def test_state_json_has_the_mode_a_plain_open_gives(project):
    plain = project.dir / "plain.txt"
    plain.write_text("made by a plain open")
    mode = stat.S_IMODE(plain.stat().st_mode)
    save_state(project, load_state(project))
    assert stat.S_IMODE(project.state_path.stat().st_mode) == mode


# --- the project lock ------------------------------------------------------------------


def test_two_locks_of_one_project_conflict_even_in_one_process(project):
    first = ProjectLock(project)
    first.acquire()
    try:
        with pytest.raises(ProjectBusy) as refused:
            ProjectLock(project).acquire()  # a second open of the lock file, as the page's job and a command make
        assert refused.value.message == PROJECT_BUSY_ERROR
        assert isinstance(refused.value, PipelineError) and refused.value.exit_code == 1
    finally:
        first.release()
    with ProjectLock(project):  # free again once released
        with pytest.raises(ProjectBusy):
            ProjectLock(project).acquire()
    assert (project.work / LOCK_FILE).is_file()  # never deleted: the lock goes with the process, not the file


def test_the_lock_of_one_project_leaves_another_project_free(project, inbox_files):
    other = create_project(*inbox_files, slug="tea", date="2026-11-12")
    with ProjectLock(project), ProjectLock(other):
        pass


# --- writers of state.json at once ---------------------------------------------------------

ROUNDS = 20  # how many times each writer of a race writes state.json


def _slow_loads(monkeypatch, seconds=0.002):
    """Make every load_state wait `seconds` after it has read state.json, so that two writers that load and save it
    at once without the state lock certainly overlap: the later save drops the other's change."""
    real = project_module.load_state

    def slow(p):
        state = real(p)
        time.sleep(seconds)
        return state

    monkeypatch.setattr(project_module, "load_state", slow)


def _at_once(*jobs):
    """Run each job in a thread of its own, all at once, and wait for them; the first error a job raised is raised
    here. A job that has not ended after a minute fails the test (a lock that is never let go)."""
    errors = []

    def run(job):
        try:
            job()
        except BaseException as error:  # noqa: BLE001 - handed to the test below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(job,), daemon=True) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    if errors:
        raise errors[0]


def test_two_updates_of_state_json_at_once_lose_neither(project, monkeypatch):
    _slow_loads(monkeypatch)

    def count(key):
        def job():
            for _ in range(ROUNDS):
                update_state(project, lambda state: state.__setitem__(key, state.get(key, 0) + 1))

        return job

    _at_once(count("page"), count("chat"))
    state = load_state(project)
    assert (state["page"], state["chat"]) == (ROUNDS, ROUNDS)


def test_update_state_saves_the_change_and_returns_what_it_returns(project):
    assert update_state(project, lambda state: state.setdefault("note", "咖啡")) == "咖啡"
    assert load_state(project)["note"] == "咖啡"
    assert (project.work / STATE_LOCK_FILE).is_file() and STATE_LOCK_FILE == "state.lock"  # never deleted


def test_update_state_that_changes_nothing_does_not_write_state(project, monkeypatch):
    saved = []
    monkeypatch.setattr(project_module, "save_state", lambda p, state: saved.append(1))
    update_state(project, lambda state: state.get("steps"))
    assert saved == []


def _state_lock_is_free(project) -> bool:
    """Whether nobody holds the state lock: an exclusive flock of an open of its own, without waiting, succeeds."""
    fd = os.open(project.work / STATE_LOCK_FILE, os.O_WRONLY | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def test_update_state_holds_the_state_lock_only_while_it_changes_state(project):
    held = update_state(project, lambda state: _state_lock_is_free(project))
    assert held is False and _state_lock_is_free(project)


def test_run_step_runs_fn_without_the_state_lock(project):
    """A step takes minutes (a render): a validate from the review page must not wait for it."""
    free = []
    run_step(project, "s", {}, [], lambda: free.append(_state_lock_is_free(project)) or {})
    assert free == [True]


def test_a_change_that_fails_saves_nothing_and_lets_the_lock_go(project):
    def broken(state):
        state["half"] = True
        raise PipelineError("boom")

    with pytest.raises(PipelineError, match="boom"):
        update_state(project, broken)
    assert "half" not in load_state(project)
    assert _state_lock_is_free(project)


def test_update_state_waits_while_another_holder_has_the_state_lock(project):
    """Another open of the lock file, in this process too, holds the lock: update_state waits, then goes on."""
    fd = os.open(project.work / STATE_LOCK_FILE, os.O_WRONLY | os.O_CREAT, 0o666)
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = threading.Event()

    def update():
        update_state(project, lambda state: state.__setitem__("note", 1))
        done.set()

    thread = threading.Thread(target=update, daemon=True)
    thread.start()
    try:
        assert not done.wait(0.3)
        assert "note" not in load_state(project)
    finally:
        os.close(fd)
    assert done.wait(10)
    thread.join()
    assert load_state(project)["note"] == 1


def test_the_state_lock_and_the_project_lock_do_not_hold_each_other_up(project):
    with ProjectLock(project):  # the order is always ProjectLock, then the state lock
        _at_once(lambda: update_state(project, lambda state: state.__setitem__("under_the_project_lock", 1)))

    def change(state):
        with ProjectLock(project):  # it does not wait: it would raise ProjectBusy were it the same file
            state["under_the_state_lock"] = 1

    update_state(project, change)
    assert load_state(project)["under_the_project_lock"] == load_state(project)["under_the_state_lock"] == 1


def _checking(project):
    """(a job that records ROUNDS validate passes, a check that the last one is in state.json)."""

    def job():
        for i in range(ROUNDS):
            project_module.record_edit_checked(project, {"edit": f"e{i}", "transcript": "t", "trim": "t"})

    return job, lambda state: state["edit_checked"]["edit"] == f"e{ROUNDS - 1}"


def _approving(project):
    """(a job that approves gate1 ROUNDS times, a check that the approval is in state.json)."""
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})

    def job():
        for _ in range(ROUNDS):
            approve_gate(project, "gate1")

    return job, lambda state: "gate1" in state["gates"]


def _running(project):
    """(a job that runs ROUNDS steps, each of its own, a check that every one is recorded)."""

    def job():
        for i in range(ROUNDS):
            run_step(project, f"a{i:02d}", {}, [], lambda: {})

    return job, lambda state: all(f"a{i:02d}" in state["steps"] for i in range(ROUNDS))


def _refreshing(project):
    """(a job that touches an input of a done step and runs the step ROUNDS times, each a skip that records the new
    mtime, a check that the last mtime is recorded)."""
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})

    def job():
        for _ in range(ROUNDS):
            _touch(src)
            assert run_step(project, "s", {"in": src}, [], lambda: {}) == {"skipped": True}

    return job, lambda state: state["steps"]["s"]["inputs"]["in"]["mtime_ns"] == src.stat().st_mtime_ns


@pytest.mark.parametrize("writer", [_checking, _approving, _running, _refreshing],
                         ids=["record_edit_checked", "approve_gate", "run_step", "skip-refresh"])
def test_a_writer_of_state_json_and_a_step_at_once_lose_no_update(project, monkeypatch, writer):
    """A validate on the review page and a step from the chat (other processes; here threads, as each takes the lock
    through an open of its own) write state.json at the same moment: each loads it again under the state lock, so
    neither saves over the other's change."""
    job, written = writer(project)
    _slow_loads(monkeypatch)

    def steps():
        for i in range(ROUNDS):
            run_step(project, f"b{i:02d}", {}, [], lambda: {})

    _at_once(job, steps)
    state = load_state(project)
    assert sorted(step for step in state["steps"] if step.startswith("b")) == [f"b{i:02d}" for i in range(ROUNDS)]
    assert written(state)


def test_load_state_of_a_missing_project_folder(tmp_path):
    with pytest.raises(PipelineError, match="state.json"):
        load_state(Project("gone", tmp_path / "gone"))


@pytest.mark.parametrize("content", [b"{", b"", b"[]", b'"text"', b"null", b"\xff\xfe not utf-8"])
def test_load_state_rejects_broken_json(project, content):
    project.state_path.write_bytes(content)
    with pytest.raises(PipelineError) as raised:
        load_state(project)
    assert project.name in raised.value.message and "unreadable" in raised.value.message


@pytest.mark.parametrize("key", ["source", "steps", "gates"])
def test_load_state_rejects_missing_keys(project, key):
    state = load_state(project)
    del state[key]
    save_state(project, state)
    with pytest.raises(PipelineError) as raised:
        load_state(project)
    assert project.name in raised.value.message and "unreadable" in raised.value.message and key in raised.value.message


# Each of these has a key that is there but holds the wrong type, and the name the error has to give.
_WRONG_TYPES = {
    "steps-list": ({"steps": []}, "steps"),
    "steps-string": ({"steps": "none"}, "steps"),
    "step-record-string": ({"steps": {"prep": "done"}}, "prep"),
    "step-record-list": ({"steps": {"prep": []}}, "prep"),
    "gates-list": ({"gates": []}, "gates"),
    "gates-null": ({"gates": None}, "gates"),
    "gate-string": ({"gates": {"gate1": "yes"}}, "gate1"),
    "gate-null": ({"gates": {"gate1": None}}, "gate1"),
}


@pytest.mark.parametrize("changes, name", _WRONG_TYPES.values(), ids=_WRONG_TYPES.keys())
def test_load_state_rejects_values_of_the_wrong_type(project, changes, name):
    save_state(project, {**load_state(project), **changes})
    with pytest.raises(PipelineError) as raised:
        load_state(project)
    message = raised.value.message
    assert project.name in message and "unreadable" in message and name in message


# --- run_step -------------------------------------------------------------------------


def test_run_step_skips_rerun_and_reruns_on_change(project):
    src = project.work / "in.txt"
    src.write_text("a")
    out = project.work / "out.txt"
    calls = []

    def fn():
        calls.append(1)
        out.write_text("x")
        return {"n": 1}

    assert run_step(project, "s", {"in": src}, [out], fn)["skipped"] is False
    assert run_step(project, "s", {"in": src}, [out], fn)["skipped"] is True
    src.write_text("b")
    run_step(project, "s", {"in": src}, [out], fn)
    out.unlink()
    run_step(project, "s", {"in": src}, [out], fn)
    assert len(calls) == 3


def test_run_step_returns_the_result_of_fn(project):
    result = run_step(project, "s", {}, [], lambda: {"n": 1})
    assert result == {"n": 1, "skipped": False}
    assert run_step(project, "s", {}, [], lambda: {"n": 2}) == {"skipped": True}


def test_run_step_records_time_input_hashes_and_relative_outputs(project):
    src = project.work / "in.txt"
    src.write_text("a")
    out = project.work / "out.txt"
    run_step(project, "s", {"in": src}, [out], _writes(out, "x"))
    record = load_state(project)["steps"]["s"]
    stat = src.stat()
    assert record["inputs"] == {
        "in": {"path": str(src), "sha256": file_sha256(src), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    }
    assert record["outputs"] == ["work/out.txt"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", record["done_at"])


def test_run_step_keeps_state_written_by_the_step(project):
    def fn():
        state = load_state(project)  # a step that saves state itself
        state["written_by_the_step"] = {"n": 1}
        save_state(project, state)
        return {}

    run_step(project, "s", {}, [], fn)
    state = load_state(project)
    assert state["written_by_the_step"] == {"n": 1}
    assert "s" in state["steps"]


def test_output_outside_the_project_is_recorded_as_absolute(project, tmp_path):
    outside = tmp_path / "elsewhere" / "out.txt"
    outside.parent.mkdir()
    run_step(project, "s", {}, [outside], _writes(outside, "x"))
    assert load_state(project)["steps"]["s"]["outputs"] == [str(outside)]
    assert step_done(project, "s")
    assert run_step(project, "s", {}, [outside], _writes(outside, "x")) == {"skipped": True}
    outside.unlink()
    assert not step_done(project, "s")


def test_run_step_skips_without_rehashing_an_unchanged_input(project, monkeypatch):
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})
    hashed = []
    real = project_module.file_sha256
    monkeypatch.setattr(project_module, "file_sha256", lambda path: hashed.append(str(path)) or real(path))
    assert run_step(project, "s", {"in": src}, [], lambda: {}) == {"skipped": True}
    assert hashed == []


def test_run_step_force_reruns(project):
    calls = []
    run_step(project, "s", {}, [], lambda: calls.append(1) or {})
    run_step(project, "s", {}, [], lambda: calls.append(1) or {}, force=True)
    assert len(calls) == 2


def test_run_step_reruns_when_the_set_of_inputs_changes(project):
    a = project.work / "a.txt"
    b = project.work / "b.txt"
    a.write_text("a")
    b.write_text("b")
    calls = []
    run_step(project, "s", {"a": a}, [], lambda: calls.append(1) or {})
    run_step(project, "s", {"a": a, "b": b}, [], lambda: calls.append(1) or {})
    assert len(calls) == 2


def test_run_step_reruns_when_the_expected_outputs_change(project):
    first = project.work / "first.txt"
    second = project.work / "second.txt"
    first.write_text("1")
    second.write_text("2")
    calls = []
    run_step(project, "s", {}, [first], lambda: calls.append(1) or {})
    run_step(project, "s", {}, [first, second], lambda: calls.append(1) or {})
    assert len(calls) == 2


def test_run_step_missing_input_raises_and_does_not_run(project):
    calls = []
    with pytest.raises(PipelineError, match="in"):
        run_step(project, "s", {"in": project.work / "nope.txt"}, [], lambda: calls.append(1) or {})
    assert calls == [] and not step_done(project, "s")


def test_failed_run_leaves_the_step_not_done(project):
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})
    assert step_done(project, "s")

    def broken():
        raise PipelineError("boom")

    with pytest.raises(PipelineError, match="boom"):
        run_step(project, "s", {"in": src}, [], broken, force=True)
    assert not step_done(project, "s")
    # the next plain run does the work again instead of trusting the half-finished state
    assert run_step(project, "s", {"in": src}, [], lambda: {})["skipped"] is False


def test_done_steps_maps_finished_steps_to_their_time(project):
    out = project.work / "out.txt"
    run_step(project, "a", {}, [], lambda: {})
    run_step(project, "b", {}, [out], _writes(out, "x"))
    out.unlink()
    steps = load_state(project)["steps"]
    assert done_steps(project) == {"a": steps["a"]["done_at"]}


def test_changed_mtime_rehashes(project):
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})
    assert step_done(project, "s")
    src.write_text("b")  # the same size, so only the mtime tells that the file was written again
    _touch(src)
    assert not step_done(project, "s")


def test_a_touched_input_with_the_same_content_is_still_done(project):
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})
    _touch(src)  # the mtime differs, so the file is hashed: the content is what counts
    assert step_done(project, "s")
    assert run_step(project, "s", {"in": src}, [], lambda: {}) == {"skipped": True}


def test_a_skip_records_the_stat_of_an_input_that_had_none(project, monkeypatch):
    src = project.work / "in.txt"
    src.write_text("a")
    out = project.work / "out.txt"
    run_step(project, "s", {"in": src}, [out], _writes(out, "x"))
    _forget_the_stat_of_every_input(project)  # a record the way the first phase wrote it
    before = load_state(project)["steps"]["s"]
    assert run_step(project, "s", {"in": src}, [out], _writes(out, "x")) == {"skipped": True}
    after = load_state(project)["steps"]["s"]
    stat = src.stat()
    assert after["inputs"]["in"] == {**before["inputs"]["in"], "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    assert (after["done_at"], after["outputs"]) == (before["done_at"], before["outputs"])
    hashed = _count_hashes(monkeypatch)
    assert step_done(project, "s") and done_steps(project) == {"s": before["done_at"]}
    assert hashed == []  # the next status does not read the input again


def test_a_skip_records_the_new_mtime_of_a_touched_input_with_the_same_content(project, monkeypatch):
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})
    before = load_state(project)["steps"]["s"]
    _touch(src)  # another mtime, the same content
    assert run_step(project, "s", {"in": src}, [], lambda: {}) == {"skipped": True}
    after = load_state(project)["steps"]["s"]
    assert after["inputs"]["in"] == {**before["inputs"]["in"], "mtime_ns": src.stat().st_mtime_ns}
    assert after["inputs"]["in"]["mtime_ns"] != before["inputs"]["in"]["mtime_ns"]
    assert (after["done_at"], after["outputs"]) == (before["done_at"], before["outputs"])
    hashed = _count_hashes(monkeypatch)
    assert step_done(project, "s") and hashed == []


def test_a_skip_that_has_nothing_to_refresh_does_not_write_state(project, monkeypatch):
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})
    saved = []
    real = project_module.save_state
    monkeypatch.setattr(project_module, "save_state", lambda p, state: saved.append(1) or real(p, state))
    assert run_step(project, "s", {"in": src}, [], lambda: {}) == {"skipped": True}
    assert saved == []


# --- gates ----------------------------------------------------------------------------


def test_gate_ok_turns_false_when_edit_changes(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    approve_gate(project, "gate1")
    assert gate_ok(project, "gate1")
    project.edit_path.write_text('{"x":1}')
    assert not gate_ok(project, "gate1")


def test_approve_requires_current_storyboard(project, capsys):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    assert main(["approve", project.name, "gate1"]) == 1
    assert "storyboard" in json.loads(capsys.readouterr().out)["error"]


def test_approve_refuses_when_edit_changed_after_storyboard(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    project.edit_path.write_text('{"x":1}')
    with pytest.raises(PipelineError, match="run storyboard first"):
        approve_gate(project, "gate1")
    assert load_state(project)["gates"] == {}


def test_approve_refuses_when_storyboard_ran_on_another_input_name(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit.json": project.edit_path}, [], lambda: {})
    with pytest.raises(PipelineError, match="run storyboard first"):
        approve_gate(project, "gate1")


def test_approve_refuses_without_edit_json(project):
    _finish_early_steps(project)
    with pytest.raises(PipelineError, match="run storyboard first"):
        approve_gate(project, "gate1")


def test_approve_unknown_gate(project):
    with pytest.raises(PipelineError, match="gate2"):
        approve_gate(project, "gate2")


def test_approve_records_the_gate(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    record = approve_gate(project, "gate1")
    assert set(record) == {"approved_at", "storyboard_inputs", "kacha_version"}
    assert load_state(project)["gates"]["gate1"] == record
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", record["approved_at"])


def test_gate_ok_is_false_before_approval_and_without_edit_json(project):
    _finish_early_steps(project)
    assert not gate_ok(project, "gate1")
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    approve_gate(project, "gate1")
    project.edit_path.unlink()
    assert not gate_ok(project, "gate1")


def _approved_on_three_inputs(project):
    """prep, transcribe and trim done, edit.json written, a storyboard built from edit.json, transcript.json
    and trim.json, and gate 1 approved. Returns the storyboard's inputs and its output."""
    transcript, trim, page = project.work / "transcript.json", project.work / "trim.json", project.review / "gate1.html"
    transcript.write_text("transcript v1")
    trim.write_text("trim v1")
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    _checked(project)  # validate has passed on these three files
    inputs = {"edit": project.edit_path, "transcript": transcript, "trim": trim}
    run_step(project, "storyboard", inputs, [page], _writes(page, "x"))
    approve_gate(project, "gate1")
    assert gate_ok(project, "gate1") and next_step(project) == "render"
    return inputs, page


def test_approve_records_the_storyboard_inputs(project):
    inputs, _ = _approved_on_three_inputs(project)
    record = load_state(project)["gates"]["gate1"]
    assert record["storyboard_inputs"] == {name: file_sha256(path) for name, path in inputs.items()}


@pytest.mark.parametrize("name", ["trim", "transcript"])
def test_gate_closes_when_a_storyboard_input_other_than_edit_changes(project, name, capsys):
    inputs, page = _approved_on_three_inputs(project)
    edit_before = project.edit_path.read_text()
    inputs[name].write_text(f"{name} v2")
    assert project.edit_path.read_text() == edit_before  # edit.json is untouched
    assert not gate_ok(project, "gate1")
    assert next_step(project) == "edit"  # edit.json was checked against the old transcript.json or trim.json
    status = _cli(capsys, "status", project.name)[1]
    assert status["next"] == "edit" and status["gate1_ok"] is False
    _checked(project)  # the ids were checked again and validate passed
    assert next_step(project) == "storyboard"
    # the storyboard is rebuilt on the same edit.json: the old approval does not cover the new one
    run_step(project, "storyboard", inputs, [page], _writes(page, "y"))
    assert not gate_ok(project, "gate1")
    assert next_step(project) == "approve"
    approve_gate(project, "gate1")
    assert gate_ok(project, "gate1")
    assert next_step(project) == "render"


def test_gate_stays_open_when_the_storyboard_is_rebuilt_on_unchanged_inputs(project):
    inputs, page = _approved_on_three_inputs(project)
    result = run_step(project, "storyboard", inputs, [page], _writes(page, "y"), force=True)
    assert result["skipped"] is False
    assert gate_ok(project, "gate1")
    assert next_step(project) == "render"


def test_an_approval_from_before_the_storyboard_inputs_were_recorded_does_not_count(project):
    _approved_on_three_inputs(project)
    state = load_state(project)
    del state["gates"]["gate1"]["storyboard_inputs"]
    save_state(project, state)
    assert not gate_ok(project, "gate1")
    assert next_step(project) == "approve"


def test_gate_is_closed_while_the_storyboard_is_not_current(project):
    inputs, page = _approved_on_three_inputs(project)
    page.unlink()
    assert not gate_ok(project, "gate1")


def test_an_approval_record_with_edit_sha256_from_phase_1_still_counts(project):
    _approved_on_three_inputs(project)
    state = load_state(project)
    state["gates"]["gate1"]["edit_sha256"] = file_sha256(project.edit_path)
    save_state(project, state)
    assert gate_ok(project, "gate1")
    assert next_step(project) == "render"


def test_gate_closes_when_an_earlier_step_is_stale(project):
    _approved_on_three_inputs(project)
    (project.work / "audio16k.wav").write_text("audio16k v2")  # transcribe read it, the storyboard did not
    assert not gate_ok(project, "gate1")
    assert next_step(project) == "transcribe"


def test_approve_refuses_when_trim_is_stale(project):
    _, audio48k = _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})  # the storyboard is current
    audio48k.write_text("audio48k v2")  # trim read it, the storyboard did not
    with pytest.raises(PipelineError, match="trim is out of date: run trim first"):
        approve_gate(project, "gate1")
    assert load_state(project)["gates"] == {}


def test_approve_names_the_first_step_that_is_out_of_date(project):
    audio16k, audio48k = _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    audio16k.write_text("audio16k v2")
    audio48k.write_text("audio48k v2")
    with pytest.raises(PipelineError, match="transcribe is out of date: run transcribe first"):
        approve_gate(project, "gate1")


def test_approve_asks_for_the_earlier_steps_before_the_storyboard(project):
    project.edit_path.write_text("{}")  # no step has run: prep comes before the storyboard
    with pytest.raises(PipelineError, match="prep is out of date: run prep first"):
        approve_gate(project, "gate1")


# --- next_step ------------------------------------------------------------------------


def test_next_step_walks_through_the_flow(project):
    def finish(step, **inputs):
        run_step(project, step, inputs, [], lambda: {})

    finish("prep")
    assert next_step(project) == "transcribe"
    finish("transcribe")
    assert next_step(project) == "trim"
    finish("trim")
    assert next_step(project) == "edit"
    project.edit_path.write_text("{}")
    assert next_step(project) == "validate"
    _checked(project)
    assert next_step(project) == "storyboard"
    finish("storyboard", edit=project.edit_path)
    assert next_step(project) == "approve"
    approve_gate(project, "gate1")
    assert next_step(project) == "render"
    finish("render")
    assert next_step(project) == "done"


def test_next_step_goes_back_when_edit_changes_after_storyboard(project):
    for step in ("prep", "transcribe", "trim"):
        run_step(project, step, {}, [], lambda: {})
    project.edit_path.write_text("{}")
    _checked(project)
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    approve_gate(project, "gate1")
    run_step(project, "render", {}, [], lambda: {})
    assert next_step(project) == "done"
    project.edit_path.write_text('{"x":1}')
    assert next_step(project) == "validate"
    _checked(project)
    assert next_step(project) == "storyboard"


def test_next_step_asks_for_a_missing_output_again(project):
    out = project.work / "probe.json"
    run_step(project, "prep", {}, [out], _writes(out, "{}"))
    assert next_step(project) == "transcribe"
    out.unlink()
    assert next_step(project) == "prep"


def _flow_until_render_done(project, edit_text="{}"):
    """prep, transcribe, trim, edit.json, storyboard, approval and render, all on one edit.json.
    The storyboard writes review/gate1.html; the render registers edit.json as an input."""
    for step in ("prep", "transcribe", "trim"):
        run_step(project, step, {}, [], lambda: {})
    project.edit_path.write_text(edit_text)
    _checked(project)
    page = project.review / "gate1.html"
    run_step(project, "storyboard", {"edit": project.edit_path}, [page], _writes(page, "x"))
    approve_gate(project, "gate1")
    run_step(project, "render", {"edit": project.edit_path}, [], lambda: {})
    assert next_step(project) == "done"


def test_next_step_renders_again_after_edit_changes_storyboard_and_approval(project):
    _flow_until_render_done(project)
    project.edit_path.write_text('{"x":1}')
    _checked(project)
    page = project.review / "gate1.html"
    run_step(project, "storyboard", {"edit": project.edit_path}, [page], _writes(page, "y"))
    approve_gate(project, "gate1")
    assert next_step(project) == "render"  # out/final.mp4 still holds the render of the old edit.json
    run_step(project, "render", {"edit": project.edit_path}, [], lambda: {})
    assert next_step(project) == "done"


def test_next_step_asks_for_storyboard_again_when_its_output_is_gone(project):
    _flow_until_render_done(project)
    (project.review / "gate1.html").unlink()
    assert not step_done(project, "storyboard")
    assert next_step(project) == "storyboard"


def test_approve_refuses_when_the_storyboard_output_is_gone(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    page = project.review / "gate1.html"
    run_step(project, "storyboard", {"edit": project.edit_path}, [page], _writes(page, "x"))
    page.unlink()
    with pytest.raises(PipelineError, match="run storyboard first"):
        approve_gate(project, "gate1")
    assert load_state(project)["gates"] == {}


def test_step_done_requires_its_inputs_to_be_unchanged(project):
    src = project.work / "in.txt"
    src.write_text("a")
    run_step(project, "s", {"in": src}, [], lambda: {})
    assert step_done(project, "s") and "s" in done_steps(project)
    src.write_text("b")
    assert not step_done(project, "s") and "s" not in done_steps(project)
    src.write_text("a")
    assert step_done(project, "s")
    src.unlink()
    assert not step_done(project, "s")


def test_next_step_goes_back_to_prep_when_the_source_video_changed(project, inbox_files):
    video = inbox_files[0]
    for step in ("prep", "transcribe", "trim"):
        run_step(project, step, {"video": video} if step == "prep" else {}, [], lambda: {})
    assert next_step(project) == "edit"
    video.write_bytes(b"another take")
    assert next_step(project) == "prep"


def _every_step_done_and_gate1_approved(project, video):
    """All five steps done and gate 1 approved, edit.json checked. prep and render both list the source video
    as an input, the storyboard lists edit.json, transcript.json and trim.json, render lists edit.json."""
    project.edit_path.write_text("{}")
    _checked(project)
    run_step(project, "prep", {"video": video}, [], lambda: {})
    run_step(project, "transcribe", {}, [], lambda: {})
    run_step(project, "trim", {}, [], lambda: {})
    work = project.work
    inputs = {"edit": project.edit_path, "transcript": work / "transcript.json", "trim": work / "trim.json"}
    run_step(project, "storyboard", inputs, [], lambda: {})
    approve_gate(project, "gate1")
    run_step(project, "render", {"video": video, "edit": project.edit_path}, [], lambda: {})


def _count_hashes(monkeypatch):
    """The list of paths that file_sha256 is called on from now on."""
    hashed = []
    real = project_module.file_sha256
    monkeypatch.setattr(project_module, "file_sha256", lambda path: hashed.append(str(path)) or real(path))
    return hashed


def test_status_does_not_rehash_an_unchanged_input(project, inbox_files, monkeypatch):
    video = inbox_files[0]
    _every_step_done_and_gate1_approved(project, video)
    hashed = _count_hashes(monkeypatch)
    status = project_module.project_status(project)
    assert status["next"] == "done" and status["gate1_ok"] is True
    assert set(status["steps"]) == {"prep", "transcribe", "trim", "storyboard", "render"}
    assert hashed == []  # the size and the mtime_ns of every input are the recorded ones


def test_status_does_not_rehash_after_the_steps_were_skipped_on_a_phase1_state(project, inbox_files, monkeypatch):
    video = inbox_files[0]
    _every_step_done_and_gate1_approved(project, video)
    _forget_the_stat_of_every_input(project)  # records the way the first phase wrote them
    _every_step_done_and_gate1_approved(project, video)  # every step is skipped; it also writes edit.json again
    hashed = _count_hashes(monkeypatch)
    status = project_module.project_status(project)
    assert status["next"] == "done" and status["gate1_ok"] is True
    assert set(status["steps"]) == {"prep", "transcribe", "trim", "storyboard", "render"}
    assert hashed == []  # the skips wrote the size and the mtime_ns of every input into its record


def test_status_hashes_each_input_file_once(project, inbox_files, monkeypatch):
    video = inbox_files[0]
    _every_step_done_and_gate1_approved(project, video)
    _forget_the_stat_of_every_input(project)  # without size and mtime_ns every file has to be hashed
    hashed = _count_hashes(monkeypatch)
    status = project_module.project_status(project)
    assert status["next"] == "done" and status["gate1_ok"] is True
    assert set(status["steps"]) == {"prep", "transcribe", "trim", "storyboard", "render"}
    documents = (project.edit_path, project.work / "transcript.json", project.work / "trim.json")
    assert sorted(hashed) == sorted({str(video), *map(str, documents)})


def test_status_reads_a_phase1_state(project, inbox_files):
    video = inbox_files[0]
    _every_step_done_and_gate1_approved(project, video)
    _forget_the_stat_of_every_input(project)
    state = load_state(project)
    state["gates"]["gate1"]["edit_sha256"] = file_sha256(project.edit_path)  # phase 1 wrote it too
    del state["edit_checked"]  # and had no record of what validate checked
    save_state(project, state)
    status = project_module.project_status(project)
    assert status["next"] == "validate" and status["gate1_ok"] is True  # the approval still counts
    assert set(status["steps"]) == {"prep", "transcribe", "trim", "storyboard", "render"}
    assert run_step(project, "prep", {"video": video}, [], lambda: {}) == {"skipped": True}
    video.write_bytes(b"another take")
    assert project_module.project_status(project)["next"] == "prep"


# --- CLI ------------------------------------------------------------------------------


def test_cli_new(inbox_files, capsys, studio):
    video, script = inbox_files
    code, out = _cli(capsys, "new", str(video), str(script), "--slug", "coffee", "--date", "2026-11-12")
    assert code == 0 and out["ok"] is True
    assert out["project"] == "2026-11-12-coffee"
    assert out["dir"] == str((studio / "projects" / "2026-11-12-coffee").resolve())
    assert (studio / "projects" / "2026-11-12-coffee" / "state.json").is_file()


def test_cli_new_reports_errors_as_json(inbox_files, capsys):
    video, script = inbox_files
    code, out = _cli(capsys, "new", str(video), str(script), "--slug", "Bad Slug")
    assert code == 1 and out["ok"] is False and "slug" in out["error"]
    code, out = _cli(capsys, "new", str(video), str(script))
    assert code == 1 and out["ok"] is False and "--slug" in out["error"]


def test_cli_status_without_projects(capsys, studio):
    assert _cli(capsys, "status") == (0, {"ok": True, "version": kacha_version(), "projects": []})


def test_cli_status_lists_every_project_with_its_next_step(inbox_files, capsys):
    a = create_project(*inbox_files, slug="a", date="2026-11-12")
    create_project(*inbox_files, slug="b", date="2026-11-13")
    run_step(a, "prep", {}, [], lambda: {})
    code, out = _cli(capsys, "status")
    assert code == 0
    assert out["projects"] == [
        {"name": "2026-11-12-a", "next": "transcribe"},
        {"name": "2026-11-13-b", "next": "prep"},
    ]


def test_status_lists_a_broken_project_with_its_error(inbox_files, capsys):
    good = create_project(*inbox_files, slug="a", date="2026-11-12")
    bad = create_project(*inbox_files, slug="b", date="2026-11-13")
    bad.state_path.write_text("{")
    code, out = _cli(capsys, "status")
    assert code == 0 and out["ok"] is True
    assert out["projects"][0] == {"name": good.name, "next": "prep"}
    assert set(out["projects"][1]) == {"name", "error"} and out["projects"][1]["name"] == bad.name
    assert bad.name in out["projects"][1]["error"] and "unreadable" in out["projects"][1]["error"]
    assert len(out["projects"]) == 2
    code, out = _cli(capsys, "status", bad.name)  # asked for by name, the error is the answer
    assert code == 1 and out["ok"] is False and "unreadable" in out["error"]


@pytest.mark.parametrize("changes, name", _WRONG_TYPES.values(), ids=_WRONG_TYPES.keys())
def test_status_lists_a_project_whose_state_has_the_wrong_types_and_the_others(inbox_files, capsys, changes, name):
    good = create_project(*inbox_files, slug="a", date="2026-11-12")
    bad = create_project(*inbox_files, slug="b", date="2026-11-13")
    save_state(bad, {**load_state(bad), **changes})
    code, out = _cli(capsys, "status")
    assert code == 0 and out["ok"] is True
    assert out["projects"][0] == {"name": good.name, "next": "prep"}
    assert set(out["projects"][1]) == {"name", "error"} and out["projects"][1]["name"] == bad.name
    assert "unreadable" in out["projects"][1]["error"] and "unexpected" not in out["projects"][1]["error"]
    assert len(out["projects"]) == 2
    code, out = _cli(capsys, "status", bad.name)  # asked for by name, the error is the answer
    assert code == 1 and out["ok"] is False
    assert "unreadable" in out["error"] and "unexpected" not in out["error"]


def test_status_lists_a_project_that_fails_in_an_unexpected_way_and_the_others(inbox_files, capsys, monkeypatch):
    good = create_project(*inbox_files, slug="a", date="2026-11-12")
    bad = create_project(*inbox_files, slug="b", date="2026-11-13")
    real = cli_module.next_step

    def next_step_that_breaks_for_one(project):
        if project.name == bad.name:
            raise AttributeError("surprise")
        return real(project)

    monkeypatch.setattr(cli_module, "next_step", next_step_that_breaks_for_one)
    code = main(["status"])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert code == 0 and out["ok"] is True
    assert out["projects"][0] == {"name": good.name, "next": "prep"}
    assert out["projects"][1] == {"name": bad.name, "error": "unexpected error: AttributeError: surprise"}
    assert len(out["projects"]) == 2
    assert "Traceback" in captured.err and "surprise" in captured.err


def test_cli_status_of_one_project(project, capsys):
    run_step(project, "prep", {}, [], lambda: {})
    code, out = _cli(capsys, "status", "coffee")
    assert code == 0
    assert out["name"] == project.name and out["next"] == "transcribe"
    assert out["steps"] == {"prep": load_state(project)["steps"]["prep"]["done_at"]}
    assert out["gate1_ok"] is False


def test_cli_status_shows_gate1_ok(project, capsys):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    approve_gate(project, "gate1")
    assert _cli(capsys, "status", project.name)[1]["gate1_ok"] is True


def test_cli_status_unknown_project(capsys, studio):
    code, out = _cli(capsys, "status", "nope")
    assert code == 1 and out["ok"] is False and "not found" in out["error"]


def test_cli_approve(project, capsys):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    code, out = _cli(capsys, "approve", project.name, "gate1")
    assert code == 0 and out["ok"] is True
    assert out["project"] == project.name and out["gate"] == "gate1"
    assert set(out) == {"ok", "project", "gate", "approved_at", "kacha_version", "storyboard_inputs"}
    assert gate_ok(project, "gate1")


def test_cli_approve_unknown_gate(project, capsys):
    code, out = _cli(capsys, "approve", project.name, "gate9")
    assert code == 1 and "gate9" in out["error"]


# --- edit_checked: what validate checked edit.json against ----------------------------


def test_phase1_project_without_edit_checked_goes_to_validate(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    assert "edit_checked" not in load_state(project)  # state.json as the first phase wrote it still loads
    assert next_step(project) == "validate"
    assert project_module.project_status(project)["next"] == "validate"


def test_next_is_validate_when_edit_differs_from_the_one_that_was_checked(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    _checked(project)
    assert next_step(project) == "storyboard"
    project.edit_path.write_text('{"x":1}')
    assert next_step(project) == "validate"
    project.edit_path.write_text("{}")  # back to the checked content
    assert next_step(project) == "storyboard"


@pytest.mark.parametrize("name", ["transcript", "trim"])
def test_next_is_edit_when_transcript_or_trim_differs_from_the_one_edit_was_checked_against(project, name):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    _checked(project)
    (project.work / f"{name}.json").write_text(f"{name} v2")
    assert next_step(project) == "edit"
    assert project_module.project_status(project)["next"] == "edit"
    project.edit_path.write_text('{"x":1}')  # a new edit.json is not checked yet, and still against the old files
    assert next_step(project) == "edit"
    _checked(project)  # validate ran again: both files and edit.json are the checked ones
    assert next_step(project) == "storyboard"


def test_next_is_edit_when_edit_json_is_gone_even_though_it_was_checked(project):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    _checked(project)
    project.edit_path.unlink()
    assert next_step(project) == "edit"


def test_a_step_that_is_out_of_date_comes_before_validate(project):
    audio16k, _ = _finish_early_steps(project)
    project.edit_path.write_text("{}")
    audio16k.write_text("audio16k v2")
    assert next_step(project) == "transcribe"


@pytest.mark.parametrize(
    "malformed",
    [None, "abc", [], {}, {"edit": "a"}, {"edit": "a", "transcript": "b", "trim": 3}, {"edit": "a", "transcript": "b"}],
    ids=["null", "string", "list", "empty", "one-key", "number", "two-keys"],
)
def test_a_malformed_edit_checked_counts_as_a_missing_one(project, malformed):
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    _checked(project)
    state = load_state(project)
    state["edit_checked"] = malformed
    save_state(project, state)
    assert next_step(project) == "validate"
    assert project_module.project_status(project)["next"] == "validate"


def _deleted_when_hashed(monkeypatch, target):
    """Make the hashing of `target` delete it first: the file goes away after its stat and before it is read."""
    real = project_module.file_sha256

    def hash_after_the_delete(path):
        if Path(path) == target:
            target.unlink()
        return real(path)

    monkeypatch.setattr(project_module, "file_sha256", hash_after_the_delete)


def test_a_file_deleted_between_its_stat_and_its_hash_is_not_there(project, monkeypatch):
    project.edit_path.write_text("{}")
    _checked(project)  # transcript.json and trim.json are written too
    _deleted_when_hashed(monkeypatch, project.edit_path)
    assert edit_file_hashes(project) == {
        "edit": None,
        "transcript": file_sha256(project.work / "transcript.json"),
        "trim": file_sha256(project.work / "trim.json"),
    }


def test_an_input_deleted_between_its_stat_and_its_hash_is_not_found(project, monkeypatch):
    src = project.work / "in.txt"
    src.write_text("a")
    _deleted_when_hashed(monkeypatch, src)
    with pytest.raises(PipelineError, match="input 'in' of step 's' not found"):
        run_step(project, "s", {"in": src}, [], lambda: {})


def test_another_error_while_hashing_an_input_goes_on(project, monkeypatch):
    src = project.work / "in.txt"
    src.write_text("a")

    def refuse(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(project_module, "file_sha256", refuse)
    with pytest.raises(PermissionError):
        run_step(project, "s", {"in": src}, [], lambda: {})


# --- format versions of transcript.json and trim.json -----------------------------------


def _set_document_version(path, version):
    document = json.loads(path.read_text(encoding="utf-8"))
    document["version"] = version
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_format_versions_name_the_steps_that_have_one():
    """transcribe and trim for the format of their files; render for what it does to the sound and the picture
    (version 2 treats the voice, see pipeline.voice; version 3 punches in, see pipeline.punch)."""
    assert FORMAT_VERSIONS == {"transcribe": 2, "trim": 2, "render": 3}


def test_a_step_record_without_the_current_format_version_is_not_done(transcribed_project):
    state = load_state(transcribed_project)
    state["steps"]["transcribe"].pop("version", None)
    save_state(transcribed_project, state)
    assert not step_done(transcribed_project, "transcribe")
    assert first_stale_step(transcribed_project) == "transcribe"


def test_a_step_record_with_another_format_version_is_not_done(transcribed_project):
    state = load_state(transcribed_project)
    state["steps"]["transcribe"]["version"] = FORMAT_VERSIONS["transcribe"] + 1
    save_state(transcribed_project, state)
    assert not step_done(transcribed_project, "transcribe")


def test_a_step_record_with_the_current_format_version_is_done(trimmed_project):
    steps = load_state(trimmed_project)["steps"]
    assert (steps["transcribe"]["version"], steps["trim"]["version"]) == (2, 2)
    assert step_done(trimmed_project, "transcribe") and step_done(trimmed_project, "trim")
    assert first_stale_step(trimmed_project) is None


def test_run_step_runs_again_when_the_recorded_version_is_older(transcribed_project, capsys):
    state = load_state(transcribed_project)
    state["steps"]["transcribe"]["version"] = 1
    save_state(transcribed_project, state)
    code, out = _cli(
        capsys, "transcribe", transcribed_project.name, "--asr-json", str(MEIJIA / "asr-qwen3-1.7b.json")
    )
    assert code == 0 and out["skipped"] is False
    assert load_state(transcribed_project)["steps"]["transcribe"]["version"] == 2
    assert step_done(transcribed_project, "transcribe")


def test_run_step_runs_again_when_the_record_has_no_version(transcribed_project, capsys):
    state = load_state(transcribed_project)
    del state["steps"]["transcribe"]["version"]
    save_state(transcribed_project, state)
    code, out = _cli(
        capsys, "transcribe", transcribed_project.name, "--asr-json", str(MEIJIA / "asr-qwen3-1.7b.json")
    )
    assert code == 0 and out["skipped"] is False
    assert load_state(transcribed_project)["steps"]["transcribe"]["version"] == 2


def test_run_step_skips_when_the_recorded_version_is_current(transcribed_project, capsys):
    code, out = _cli(
        capsys, "transcribe", transcribed_project.name, "--asr-json", str(MEIJIA / "asr-qwen3-1.7b.json")
    )
    assert code == 0 and out["skipped"] is True


def test_run_step_records_the_format_version_of_a_step_that_has_one(project):
    audio = project.work / "audio48k.wav"
    audio.write_text("audio")
    run_step(project, "trim", {"audio": audio}, [], lambda: {})
    assert load_state(project)["steps"]["trim"]["version"] == FORMAT_VERSIONS["trim"]


def test_steps_without_a_format_version_record_none(transcribed_project):
    steps = load_state(transcribed_project)["steps"]
    assert "prep" in steps and "version" not in steps["prep"]


def test_a_phase1_project_goes_back_to_transcribe(edited_project, capsys):
    p = edited_project
    state = load_state(p)
    for step in ("transcribe", "trim"):
        del state["steps"][step]["version"]
    save_state(p, state)
    for name in ("transcript.json", "trim.json"):
        _set_document_version(p.work / name, 1)
    code, out = _cli(capsys, "status", p.name)
    assert code == 0 and out["next"] == "transcribe"
    code, out = _cli(capsys, "storyboard", p.name)
    assert code == 1 and out["error"] == "transcript.json was made by an older pipeline: run transcribe again"
    code, out = _cli(capsys, "render", p.name)
    assert code == 1 and "transcribe is out of date" in out["error"]


# ---- the version each step and approval was made with (spec 2026-11-15-versioning-design, section 5) ----------


def _approved(project):
    """prep, transcribe, trim and the storyboard done, and gate1 approved."""
    _finish_early_steps(project)
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    approve_gate(project, "gate1")


def test_run_step_records_the_kacha_version(project):
    run_step(project, "prep", {}, [], lambda: {})
    assert load_state(project)["steps"]["prep"]["kacha_version"] == kacha_version()


def test_a_skipped_step_keeps_the_version_that_made_it(project, monkeypatch):
    monkeypatch.setattr(project_module, "kacha_version", lambda: "0.0.1")
    run_step(project, "prep", {}, [], lambda: {})
    monkeypatch.setattr(project_module, "kacha_version", lambda: "0.9.0")
    assert run_step(project, "prep", {}, [], lambda: {}) == {"skipped": True}
    assert load_state(project)["steps"]["prep"]["kacha_version"] == "0.0.1"


def test_approve_records_the_kacha_version(project):
    _approved(project)
    assert load_state(project)["gates"]["gate1"]["kacha_version"] == kacha_version()


def test_status_lists_the_version_of_each_done_step_and_the_approval(project):
    _approved(project)
    status = project_status(project)
    assert status["made_with"] == {name: kacha_version() for name in [*status["steps"], "gate1"]}


def test_status_leaves_out_an_approval_that_no_longer_counts(project):
    """The approval was for a storyboard made from files that have changed since (gate1_ok is false): it is not the
    approval of this project as it stands, so made_with does not name a version for it."""
    _approved(project)
    project.edit_path.write_text('{"changed": true}')
    status = project_status(project)
    assert status["gate1_ok"] is False
    assert "gate1" not in status["made_with"]


def test_a_project_made_before_versioning_reports_null_and_stays_done(project):
    _approved(project)
    before = project_status(project)
    state = load_state(project)
    for record in [*state["steps"].values(), *state["gates"].values()]:
        del record["kacha_version"]
    save_state(project, state)
    after = project_status(project)
    assert (after["next"], after["steps"], after["gate1_ok"]) == (before["next"], before["steps"], before["gate1_ok"])
    assert after["made_with"] == {name: None for name in [*after["steps"], "gate1"]}


def test_another_version_does_not_undo_a_step_or_the_approval(project, monkeypatch):
    monkeypatch.setattr(project_module, "kacha_version", lambda: "0.0.1")
    _approved(project)
    before = project_status(project)
    monkeypatch.setattr(project_module, "kacha_version", lambda: "0.9.0")
    after = project_status(project)
    assert (after["next"], after["steps"], after["gate1_ok"]) == (before["next"], before["steps"], True)

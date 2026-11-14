"""Checks that the autouse isolation in conftest.py protects the real home and the model cache, and that the
Meijia stages built once per session (the *_template fixtures) are read-only and that the copy a test gets of one
is its own and stands where a fresh build stands."""

import os
import pwd
from pathlib import Path

import pytest
from helpers import (
    MEIJIA,
    TEMPLATE_PARTS,
    approve_project,
    copy_template,
    meijia_transcribed_project,
    real_hf_home,
    trim_project,
    write_default_edit,
)

from pipeline.align import transcribe
from pipeline.paths import repo_root
from pipeline.prep import prep
from pipeline.project import Project, gate_ok, load_project, load_state, project_status, step_done
from pipeline.storyboard import build_storyboard
from pipeline.trim import trim

# Captured at import time, before any fixture has touched the environment.
REAL_ENVIRON = dict(os.environ)
# The account database's home folder: it does not follow the HOME variable that the fixture fakes.
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
# Worked out here again from those two values, not read back from conftest.
EXPECTED_HF_HOME = str(real_hf_home(REAL_ENVIRON, REAL_HOME))


def test_home_is_a_fake_folder_under_tmp(tmp_path):
    home = Path(os.environ["HOME"])
    assert tmp_path in home.parents
    assert home != REAL_HOME
    assert not home.is_relative_to(REAL_HOME)


def test_hf_home_points_at_the_real_cache():
    assert os.environ["HF_HOME"] == EXPECTED_HF_HOME


SOME_HOME = Path("/home/someone")


def test_real_hf_home_prefers_hf_home():
    environ = {"HF_HOME": "/data/hf", "XDG_CACHE_HOME": "/data/xdg"}
    assert real_hf_home(environ, SOME_HOME) == Path("/data/hf")


def test_real_hf_home_uses_xdg_cache_home():
    assert real_hf_home({"XDG_CACHE_HOME": "/data/xdg"}, SOME_HOME) == Path("/data/xdg/huggingface")


def test_real_hf_home_defaults_under_home():
    assert real_hf_home({}, SOME_HOME) == Path("/home/someone/.cache/huggingface")


def test_real_hf_home_treats_empty_as_unset():
    assert real_hf_home({"HF_HOME": "", "XDG_CACHE_HOME": "/data/xdg"}, SOME_HOME) == Path("/data/xdg/huggingface")
    assert real_hf_home({"HF_HOME": "", "XDG_CACHE_HOME": ""}, SOME_HOME) == Path("/home/someone/.cache/huggingface")


def test_meijia_inbox_needs_face_model(request, tmp_path, monkeypatch):
    # models_dir() is where the face model is looked for; an empty folder stands in for a machine
    # where `pipeline setup` has not run. The real models/ folder is not touched.
    no_models = tmp_path / "no_models"
    no_models.mkdir()
    monkeypatch.setattr("pipeline.assets.models_dir", lambda: no_models)
    with pytest.raises(pytest.fail.Exception, match="pipeline setup"):
        request.getfixturevalue("meijia_inbox")


def test_studio_env_points_under_tmp(tmp_path):
    assert tmp_path in Path(os.environ["REELS_STUDIO"]).parents


def test_T_and_TR_are_private_copies_of_the_session_documents(T, TR, meijia_docs):
    assert T == meijia_docs[0] and TR == meijia_docs[1]
    assert T is not meijia_docs[0] and TR is not meijia_docs[1]
    T["sentences"][0]["text"] = "changed"
    TR["trims"].clear()
    TR["bounds"]["s01"]["cut_in"] = -1.0
    assert meijia_docs[0]["sentences"][0]["text"] != "changed"
    assert meijia_docs[1]["trims"] and meijia_docs[1]["bounds"]["s01"]["cut_in"] != -1.0


def test_T_and_TR_describe_the_meijia_fixture(T, TR):
    assert [s["id"] for s in T["sentences"]] == ["s01", "s02", "s03", "s04"]
    assert [t["id"] for t in TR["trims"]] == ["t01", "t02", "t03", "t04", "t05"]
    assert set(TR["bounds"]) == {"s01", "s02", "s03", "s04"}


# ---- the Meijia stages: built once per session, copied for every test ---------------------------

STAGES = ("transcribed", "trimmed", "edited", "approved")


def files_under(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


@pytest.mark.parametrize("stage", STAGES)
def test_a_meijia_template_is_built_under_the_session_folder_and_is_read_only(stage, request, tmp_path_factory):
    template = request.getfixturevalue(f"{stage}_template")
    assert template.root.is_relative_to(tmp_path_factory.getbasetemp())
    project = template.root / "studio" / "projects" / template.name
    assert (project / "state.json").is_file()
    paths = [path for part in TEMPLATE_PARTS for path in (template.root / part, *(template.root / part).rglob("*"))]
    assert len(paths) > 10 and [path for path in paths if path.stat().st_mode & 0o222] == []
    with pytest.raises(PermissionError):
        open(project / "state.json", "ab").close()  # opened to append nothing: it would change nothing
    new = project / "work" / "written-by-a-test"
    try:
        with pytest.raises(PermissionError):
            new.touch()
    finally:
        new.unlink(missing_ok=True)


@pytest.mark.parametrize("stage", STAGES)
def test_a_copy_of_a_meijia_stage_holds_no_path_of_any_template(stage, request, tmp_path, tmp_path_factory):
    """No file of the copy names a folder of any template, its own or one of the stages it was built from: every
    template's root is a folder meijia-<stage>N of the session's base folder."""
    template = request.getfixturevalue(f"{stage}_template")
    p = request.getfixturevalue(f"{stage}_project")
    marker = str(tmp_path_factory.getbasetemp() / "meijia-").encode("utf-8")
    assert marker in (template.root / "studio" / "projects" / template.name / "state.json").read_bytes()
    assert p.dir == (tmp_path / "studio" / "projects" / template.name).resolve()
    assert load_project(template.name) == p
    assert [path for path in files_under(tmp_path) if marker in path.read_bytes()] == []


@pytest.mark.parametrize("stage", STAGES)
def test_a_copy_of_a_meijia_stage_records_its_own_files_with_their_size_and_time(stage, request, tmp_path):
    """Every input a step of the copy recorded is a file of the copy or of the repo, with the recorded size and
    mtime_ns: a step skips on them without reading the file again, as it does in the template."""
    p = request.getfixturevalue(f"{stage}_project")
    state = load_state(p)
    assert Path(state["source"]["video"]).is_relative_to(tmp_path)
    assert Path(state["source"]["script"]).is_relative_to(tmp_path)
    for record in state["steps"].values():
        for entry in record["inputs"].values():
            path = Path(entry["path"])
            assert path.is_relative_to(tmp_path) or path.is_relative_to(repo_root()), path
            assert (path.stat().st_size, path.stat().st_mtime_ns) == (entry["size"], entry["mtime_ns"]), path


def test_a_test_cannot_get_two_stages_of_the_meijia_project(transcribed_project, trimmed_template, tmp_path):
    """The stages are copies to one place, so a second one would land on the first: copy_template refuses it."""
    before = load_state(transcribed_project)
    with pytest.raises(AssertionError, match="exists already"):
        copy_template(trimmed_template, tmp_path)
    assert load_state(transcribed_project) == before


def standing(p: Project) -> dict:
    """Where a project stands, without its name and times."""
    status = project_status(p)
    return {
        "next": status["next"],
        "steps": sorted(status["steps"]),
        "gate1_ok": status["gate1_ok"],
        "gate1": gate_ok(p, "gate1"),
        "done": {step: step_done(p, step) for step in ("prep", "transcribe", "trim", "storyboard")},
    }


def rerun(p: Project, stage: str) -> dict:
    """Whether each step that has run in `stage` skips when it is run again (the storyboard as its command runs
    it, without --force)."""
    skipped = {
        "prep": prep(p)["skipped"],
        "transcribe": transcribe(p, asr_json=MEIJIA / "asr-qwen3-1.7b.json")["skipped"],
    }
    if stage != "transcribed":
        skipped["trim"] = trim(p)["skipped"]
    if stage == "approved":
        skipped["storyboard"] = build_storyboard(p, force=False)["skipped"]
    return skipped


def test_copies_of_the_meijia_stages_stand_where_a_fresh_build_stands(request, tmp_path, monkeypatch):
    """One project built step by step in the test's studio, and at each stage a copy of that stage's template in a
    studio of its own: the same steps are done, the same step comes next, gate 1 is approved in both or in
    neither, and each step that ran skips when it is run again, in both, after which they still stand together."""
    studio = os.environ["REELS_STUDIO"]
    steps = {"transcribed": lambda p: p, "trimmed": trim_project, "edited": write_default_edit,
             "approved": approve_project}
    fresh = meijia_transcribed_project(tmp_path)
    for stage, step in steps.items():
        monkeypatch.setenv("REELS_STUDIO", studio)
        step(fresh)
        root = tmp_path / "copies" / stage
        root.mkdir(parents=True)
        monkeypatch.setenv("REELS_STUDIO", str(root / "studio"))
        copy = copy_template(request.getfixturevalue(f"{stage}_template"), root)
        assert standing(copy) == standing(fresh), stage
        skipped = rerun(copy, stage)
        assert all(skipped.values()) and skipped == rerun(fresh, stage), stage
        assert standing(copy) == standing(fresh), stage

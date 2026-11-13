import pytest

from pipeline.errors import PipelineError
from pipeline.paths import inbox_root, projects_root, repo_root, studio_root


def test_repo_root_holds_pyproject():
    assert (repo_root() / "pyproject.toml").is_file()


def test_studio_root_default_is_repo_studio(monkeypatch):
    monkeypatch.delenv("REELS_STUDIO", raising=False)
    assert studio_root() == repo_root() / "studio"


def test_studio_root_empty_means_default(monkeypatch):
    monkeypatch.setenv("REELS_STUDIO", "")
    assert studio_root() == repo_root() / "studio"


def test_studio_root_rejects_relative(monkeypatch):
    monkeypatch.setenv("REELS_STUDIO", "studio")
    with pytest.raises(PipelineError, match="absolute"):
        studio_root()


def test_studio_root_uses_absolute_value(monkeypatch, tmp_path):
    monkeypatch.setenv("REELS_STUDIO", str(tmp_path / "my studio"))
    assert studio_root() == tmp_path / "my studio"


def test_projects_and_inbox_under_studio(studio):
    assert projects_root() == studio / "projects" and inbox_root() == studio / "inbox"


def test_studio_fixture_builds_inbox_and_projects(studio):
    assert (studio / "inbox").is_dir()
    assert (studio / "projects").is_dir()

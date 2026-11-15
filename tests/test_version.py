"""The program version (spec 2026-11-15-versioning-design, section 4): read from pyproject.toml, kept in uv.lock."""

import re
import tomllib

import pytest

from pipeline import version
from pipeline.errors import PipelineError
from pipeline.paths import repo_root
from pipeline.version import kacha_version


def test_the_version_is_the_one_in_pyproject():
    with open(repo_root() / "pyproject.toml", "rb") as file:
        expected = tomllib.load(file)["project"]["version"]
    assert kacha_version() == expected
    assert re.fullmatch(r"\d+\.\d+\.\d+", kacha_version())


def test_the_lock_has_the_version_of_pyproject():
    """Fails when pyproject.toml was given a new version and uv lock was not run."""
    with open(repo_root() / "uv.lock", "rb") as file:
        packages = tomllib.load(file)["package"]
    (kacha,) = [package for package in packages if package["name"] == "kacha"]
    assert kacha["version"] == kacha_version()


def test_a_process_keeps_the_version_it_started_with(tmp_path, monkeypatch):
    """The review page's server runs for a long time with the code it loaded: a version given to pyproject.toml
    while it runs (a merge) belongs to code it has not loaded, so the records it writes keep the first version."""
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nversion = "0.0.1"\n', encoding="utf-8")
    monkeypatch.setattr(version, "PYPROJECT", path)
    assert kacha_version() == "0.0.1"
    path.write_text('[project]\nversion = "0.0.2"\n', encoding="utf-8")
    assert kacha_version() == "0.0.1"


@pytest.mark.parametrize("content",[None, '[project]\nname = "kacha"\n', "[project\nversion = 1", '[project]\nversion = 2\n'])
def test_an_unreadable_pyproject_is_a_pipeline_error(tmp_path, monkeypatch, content):
    path = tmp_path / "pyproject.toml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(version, "PYPROJECT", path)
    with pytest.raises(PipelineError) as error:
        kacha_version()
    assert error.value.message.startswith("cannot read the kacha version from pyproject.toml: ")

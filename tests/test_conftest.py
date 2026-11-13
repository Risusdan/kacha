"""Checks that the autouse isolation in conftest.py protects the real home and the model cache."""

import os
import pwd
from pathlib import Path

import pytest
from helpers import real_hf_home

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

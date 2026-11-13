import copy
import os
import shutil
from pathlib import Path

import pytest
from helpers import real_hf_home

# The real Hugging Face cache, worked out at import time from the real environment, before any
# fixture points HOME at a fake folder. Without it the tests would download the 4.7 GB ASR model again.
REAL_HF_HOME = str(real_hf_home(os.environ, Path.home()))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Keep every test away from the real home folder and the real studio."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(fake_home / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))
    monkeypatch.setenv("REELS_STUDIO", str(tmp_path / "studio"))
    monkeypatch.setenv("HF_HOME", REAL_HF_HOME)


@pytest.fixture(autouse=True)
def restore_cli_commands():
    """Put cli._COMMANDS back after each test, so a command a test registers never reaches the next one."""
    from pipeline import cli

    saved = dict(cli._COMMANDS)
    yield
    cli._COMMANDS.clear()
    cli._COMMANDS.update(saved)


@pytest.fixture
def studio(tmp_path):
    """The temporary studio that REELS_STUDIO points at, with inbox/ and projects/ built."""
    root = tmp_path / "studio"
    (root / "inbox").mkdir(parents=True)
    (root / "projects").mkdir()
    return root


@pytest.fixture
def inbox_files(studio):
    """(video, script): two small files under the temp studio's inbox/a/. The video is not a
    real video, only bytes; the script is one short Chinese line."""
    folder = studio / "inbox" / "a"
    folder.mkdir()
    video = folder / "clip.mp4"
    video.write_bytes(b"not a real video, just bytes")
    script = folder / "script.md"
    script.write_text("今天我們來聊短影音剪輯。\n", encoding="utf-8")
    return video, script


@pytest.fixture
def meijia_inbox(tmp_path, studio):
    """(video, script): the Meijia video and a copy of the Meijia script.md, under the temp studio's
    inbox/meijia/, as the director leaves them. The video is the one helpers.meijia_video makes.
    The face model and the fonts are checked first, so a machine without them is told to run
    `pipeline setup` before prep or the storyboard fails on them."""
    from helpers import MEIJIA, meijia_video, require_assets

    require_assets()
    folder = studio / "inbox" / "meijia"
    folder.mkdir()
    video = folder / "meijia.mp4"
    shutil.copyfile(meijia_video(tmp_path), video)
    script = folder / "script.md"
    shutil.copyfile(MEIJIA / "script.md", script)
    return video, script


@pytest.fixture
def project(inbox_files):
    """A Project created from inbox_files, in the temp studio."""
    from pipeline.project import create_project

    return create_project(*inbox_files, slug="coffee", date="2026-11-12")


@pytest.fixture
def transcribed_project(tmp_path):
    """A project made from the Meijia fixture, with prep and transcribe done. The video is a
    gray-counter clip as long as fixture.wav, with fixture.wav as its sound; the script is the
    Meijia script.md. transcribe reads the recorded 1.7B result (--asr-json), so no engine runs."""
    from helpers import meijia_transcribed_project

    return meijia_transcribed_project(tmp_path)


@pytest.fixture
def trimmed_project(transcribed_project):
    """transcribed_project with the trim step done: work/trim.json exists."""
    from helpers import trim_project

    return trim_project(transcribed_project)


@pytest.fixture
def edited_project(trimmed_project):
    """trimmed_project with the edit.json that helpers.default_edit makes for its transcript and trims:
    every suggested cut is accepted, no extra cut."""
    from helpers import write_default_edit

    return write_default_edit(trimmed_project)


@pytest.fixture
def approved_project(edited_project):
    """edited_project with the storyboard built and gate 1 approved (the real build_storyboard and
    approve_gate): what the render needs."""
    from helpers import approve_project

    return approve_project(edited_project)


@pytest.fixture(scope="session")
def meijia_docs():
    """(transcript, trims) of the Meijia fixture, built once per session: the transcript from script.md
    and the recorded 1.7B result, the trims from the loudness envelope of fixture.wav. Tests use the
    copies `T` and `TR`, never this pair, so that no test can change what the others see."""
    from helpers import MEIJIA, fixture_transcript

    from pipeline.audio import envelope
    from pipeline.trim import build_trims

    transcript = fixture_transcript()
    return transcript, build_trims(transcript, envelope(MEIJIA / "fixture.wav"))


@pytest.fixture
def T(meijia_docs):
    """A private copy of the Meijia transcript."""
    return copy.deepcopy(meijia_docs[0])


@pytest.fixture
def TR(meijia_docs):
    """A private copy of the trim.json document made for the Meijia transcript."""
    return copy.deepcopy(meijia_docs[1])

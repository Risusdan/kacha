import copy
import os
import shutil
from pathlib import Path

import pytest
from helpers import real_hf_home, stand_in_render_stills

# The real Hugging Face cache, worked out at import time from the real environment, before any
# fixture points HOME at a fake folder. Without it the tests would download the 4.7 GB ASR model again.
REAL_HF_HOME = str(real_hf_home(os.environ, Path.home()))


def point_env_at(monkeypatch, root: Path) -> None:
    """Point HOME and the XDG folders at a fake home under `root`, REELS_STUDIO at `root`/studio, and HF_HOME at the
    real Hugging Face cache."""
    fake_home = root / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(fake_home / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))
    monkeypatch.setenv("REELS_STUDIO", str(root / "studio"))
    monkeypatch.setenv("HF_HOME", REAL_HF_HOME)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Keep every test away from the real home folder and the real studio."""
    point_env_at(monkeypatch, tmp_path)


@pytest.fixture(autouse=True)
def stand_in_stills(monkeypatch):
    """The storyboard and the review server draw stand-in stills of the cards (helpers.stand_in_render_stills), so that
    no test of them needs Node. The real render_stills is tested on its own: with a stand-in run in test_scene_render,
    with Node in test_scene_cards."""
    monkeypatch.setattr("pipeline.storyboard.render_stills", stand_in_render_stills)
    monkeypatch.setattr("pipeline.review_server.render_stills", stand_in_render_stills)


@pytest.fixture(scope="module")
def module_env(tmp_path_factory):
    """A folder for what the tests of one module share (a project made once, which they only read), with the
    environment pointed under it as isolated_env points it for a test, for as long as the module runs. A fixture of
    the module's scope is made before the isolated_env of the test that first asks for it, so without this it would
    build in the real home folder and the real studio. Each test still gets its own isolated_env on top."""
    root = tmp_path_factory.mktemp("module")
    with pytest.MonkeyPatch.context() as monkeypatch:
        point_env_at(monkeypatch, root)
        yield root


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


def build_template(tmp_path_factory, stage: str, build):
    """Build a stage of the Meijia project once for the session, as build(root) makes it in a folder of its own
    under pytest's temp folder, with the environment pointed under that folder as isolated_env points it for a
    test, and freeze it (helpers.freeze_template); yield the Template, and give it its modes back at the end. A
    fixture of the session's scope is made before the isolated_env of the test that first asks for it, so without
    this it would build in the real home folder and the real studio."""
    from helpers import freeze_template, thaw_template

    from pipeline.paths import studio_root

    root = tmp_path_factory.mktemp(f"meijia-{stage}")
    with pytest.MonkeyPatch.context() as monkeypatch:
        point_env_at(monkeypatch, root)
        # checked before anything is written
        assert studio_root().is_relative_to(root) and Path.home().is_relative_to(root)
        template = freeze_template(root, build(root))
    try:
        yield template
    finally:
        thaw_template(template)


@pytest.fixture(scope="session")
def transcribed_template(tmp_path_factory):
    """The Meijia project with prep and transcribe done, built once per session: the Template that
    transcribed_project copies. The video is a gray-counter clip as long as fixture.wav, with fixture.wav as its
    sound; the script is the Meijia script.md. transcribe reads the recorded 1.7B result (--asr-json), so no engine
    runs."""
    from helpers import meijia_transcribed_project

    yield from build_template(tmp_path_factory, "transcribed", meijia_transcribed_project)


@pytest.fixture(scope="session")
def trimmed_template(tmp_path_factory, transcribed_template):
    """A copy of transcribed_template with the trim step done: work/trim.json exists."""
    from helpers import copy_template, trim_project

    yield from build_template(
        tmp_path_factory, "trimmed", lambda root: trim_project(copy_template(transcribed_template, root))
    )


@pytest.fixture(scope="session")
def edited_template(tmp_path_factory, trimmed_template):
    """A copy of trimmed_template with the edit.json that helpers.default_edit makes for its transcript and trims:
    every suggested cut is accepted, no extra cut."""
    from helpers import copy_template, write_default_edit

    yield from build_template(
        tmp_path_factory, "edited", lambda root: write_default_edit(copy_template(trimmed_template, root))
    )


@pytest.fixture(scope="session")
def approved_template(tmp_path_factory, edited_template):
    """A copy of edited_template with the storyboard built and gate 1 approved (validate_project, the real
    build_storyboard and approve_gate): what the render needs."""
    from helpers import approve_project, copy_template

    yield from build_template(
        tmp_path_factory, "approved", lambda root: approve_project(copy_template(edited_template, root))
    )


@pytest.fixture
def transcribed_project(tmp_path, transcribed_template):
    """The test's own copy of transcribed_template (helpers.copy_template), in its tmp_path and its studio: a
    project made from the Meijia fixture, with prep and transcribe done. The test may change, delete or run
    anything in it; no other test sees it."""
    from helpers import copy_template

    return copy_template(transcribed_template, tmp_path)


@pytest.fixture
def trimmed_project(tmp_path, trimmed_template):
    """The test's own copy of trimmed_template: transcribed_project with the trim step done."""
    from helpers import copy_template

    return copy_template(trimmed_template, tmp_path)


@pytest.fixture
def edited_project(tmp_path, edited_template):
    """The test's own copy of edited_template: trimmed_project with the edit.json of helpers.default_edit."""
    from helpers import copy_template

    return copy_template(edited_template, tmp_path)


@pytest.fixture
def approved_project(tmp_path, approved_template):
    """The test's own copy of approved_template: edited_project with the storyboard built and gate 1 approved,
    what the render needs."""
    from helpers import copy_template

    return copy_template(approved_template, tmp_path)


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

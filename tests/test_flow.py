"""The whole flow on the Meijia fixture, driven only through pipeline.cli.main, the way the director uses it:
new, prep, transcribe, trim, edit.json, validate, storyboard, approve, render. The inbox files must come out
of it byte for byte as they went in. test_full_flow_with_real_asr runs the real 1.7B speech recogniser."""

import contextlib
import io
import json
from pathlib import Path

import pytest
from helpers import MEIJIA, default_edit, load_t_tr

from pipeline.asr import MODELS
from pipeline.cli import main
from pipeline.project import file_sha256, load_project

RAW = MEIJIA / "asr-qwen3-1.7b.json"
EXPECTED_MODEL = MODELS["1.7b"]


def run(*argv):
    """(exit code, the JSON line main printed) of one command."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(list(argv))
    return code, json.loads(buf.getvalue())


def ok(*argv):
    """The JSON line of a command that must exit 0; a failure shows the line, which says why."""
    code, r = run(*argv)
    assert code == 0, r
    return r


def _flow(meijia_inbox, asr_json):  # meijia_inbox: inbox/meijia/ holds the Meijia video and script.md
    video, script = meijia_inbox
    before = {f: file_sha256(f) for f in (video, script)}
    r = ok("new", str(video), str(script), "--slug", "meijia")
    name = r["project"]
    ok("prep", name)
    ok("transcribe", name, *(["--asr-json", str(asr_json)] if asr_json else []))
    if asr_json is None:  # the real engine ran: it must be the 1.7B, not the 0.6B that a failed run falls back to
        asr = json.loads((load_project(name).work / "transcript.json").read_text(encoding="utf-8"))["asr"]
        assert asr["model"] == EXPECTED_MODEL, asr
        assert asr["fallback_used"] is False, asr
    ok("trim", name)
    assert run("status", name)[1]["next"] == "edit"
    p = load_project(name)
    p.edit_path.write_text(json.dumps(default_edit(*load_t_tr(p)), ensure_ascii=False))
    assert run("status", name)[1]["next"] == "validate"  # edit.json was written, not checked
    code, r = run("storyboard", name)  # refused until validate has passed on it
    assert code == 1 and r["error"] == "edit.json has not been validated: run validate", r
    ok("validate", name)
    assert run("status", name)[1]["next"] == "storyboard"
    code, r = run("render", name)  # gate 1 is not passed yet
    assert code == 1, r
    ok("storyboard", name)
    assert run("status", name)[1]["next"] == "approve"
    ok("approve", name, "gate1")
    code, r = run("render", name)
    assert code == 0 and Path(r["out"]).exists(), r
    assert run("status", name)[1]["next"] == "done"
    code, r = run("prep", name)  # nothing changed: skipped, and a skip is a success
    assert code == 0 and r["skipped"] is True, r
    assert before == {f: file_sha256(f) for f in (video, script)}


def test_full_flow_on_fixture(meijia_inbox):
    _flow(meijia_inbox, RAW)


@pytest.mark.slow
def test_full_flow_with_real_asr(meijia_inbox):
    _flow(meijia_inbox, None)  # runs the real 1.7B

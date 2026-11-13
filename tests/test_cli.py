import json
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import pytest
from helpers import edited_project_from_script

from pipeline import cli
from pipeline.assets import models_dir
from pipeline.cli import command, main, registered_commands
from pipeline.edit import validate_project
from pipeline.errors import AsrTruncatedError, EditInvalid, PipelineError, UnsafeDeleteError
from pipeline.project import PROJECT_BUSY_ERROR, ProjectBusy, ProjectLock
from pipeline.review_video import review_video_path
from pipeline.storyboard import NOT_VALIDATED_REFUSAL


def test_unknown_command_prints_json_error(capsys):
    assert main(["frobnicate"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_missing_command_prints_json_error(capsys):
    assert main([]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_registered_handler_output(capsys):
    @command("_echo_test")
    def _echo(args):
        return {"hello": "世界"}

    assert main(["_echo_test"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "hello": "世界"}


def test_output_is_one_line_of_unescaped_json(capsys):
    @command("_oneline_test")
    def _oneline(args):
        return {"text": "短影音"}

    main(["_oneline_test"])
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.endswith("\n")
    assert "短影音" in out


def test_registered_commands_lists_names():
    @command("_listed_test")
    def _listed(args):
        return {}

    assert "_listed_test" in registered_commands()


def test_handler_receives_parsed_arguments(capsys):
    @command("_args_test", args=[("name", {}), ("--force", {"action": "store_true"})])
    def _args(args):
        return {"name": args.name, "force": args.force}

    assert main(["_args_test", "coffee", "--force"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "name": "coffee", "force": True}
    assert main(["_args_test", "tea"]) == 0
    assert json.loads(capsys.readouterr().out)["force"] is False


def test_missing_required_argument_prints_json_error(capsys):
    @command("_required_test", args=[("name", {})])
    def _required(args):
        return {}

    assert main(["_required_test"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and "name" in payload["error"]


def test_pipeline_error_prints_extra_and_exit_code_1(capsys):
    @command("_fail_test")
    def _fail(args):
        raise PipelineError("render failed", log="work/render.log")

    assert main(["_fail_test"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "render failed",
        "log": "work/render.log",
    }


def test_edit_invalid_exit_code_2(capsys):
    @command("_bad_edit_test")
    def _bad(args):
        raise EditInvalid(["cuts.accept: unknown trim t99"])

    assert main(["_bad_edit_test"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"] == ["cuts.accept: unknown trim t99"]


def test_edit_invalid_output_is_ok_false_with_error_text(capsys):
    @command("_bad_edit_text_test")
    def _bad(args):
        raise EditInvalid(["captions: unknown mode"])

    main(["_bad_edit_text_test"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["error"]


@pytest.mark.parametrize("error_class", [UnsafeDeleteError, AsrTruncatedError])
def test_specific_errors_are_pipeline_errors_with_exit_code_1(error_class):
    error = error_class("boom", detail="x")
    assert isinstance(error, PipelineError)
    assert error.exit_code == 1 and error.extra == {"detail": "x"}


def test_module_entry_point_reports_json_and_exit_code():
    result = subprocess.run(
        [sys.executable, "-m", "pipeline", "frobnicate"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False


def test_unexpected_exception_is_one_json_line(capsys):
    @command("_crash_test")
    def _crash(args):
        raise ValueError("bad")

    assert main(["_crash_test"]) == 1
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error"] == "unexpected error: ValueError: bad"
    assert "Traceback" in captured.err


def _circular() -> dict:
    result: dict = {}
    result["self"] = result
    return result


@pytest.mark.parametrize(
    "result", [None, [1, 2], {(1, 2): "tuple key"}, _circular()], ids=["none", "list", "non-string-key", "circular"]
)
def test_a_result_that_cannot_be_printed_is_an_unexpected_error_line(capsys, result):
    @command("_unprintable_test")
    def _unprintable(args):
        return result

    assert main(["_unprintable_test"]) == 1
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1
    payload = json.loads(captured.out)
    assert payload["ok"] is False and payload["error"].startswith("unexpected error: ")
    assert "Traceback" in captured.err


def test_path_in_result_is_printed_as_text(capsys):
    @command("_path_test")
    def _path(args):
        return {"out": Path("/x/y")}

    assert main(["_path_test"]) == 0
    assert json.loads(capsys.readouterr().out)["out"] == "/x/y"


def test_error_extra_cannot_overwrite_ok_or_error(capsys):
    @command("_overwrite_test")
    def _overwrite(args):
        raise PipelineError("boom", ok=True, error="x", hint="h")

    assert main(["_overwrite_test"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "boom"
    assert payload["hint"] == "h"


def test_pipeline_error_accepts_message_keyword_in_extra():
    assert PipelineError("x", message="m").extra == {"message": "m"}


def test_help_lists_every_command_and_returns_0(capsys):
    assert main(["-h"]) == 0
    out = capsys.readouterr().out
    for name in registered_commands():
        assert name in out


def test_long_help_flag_returns_0(capsys):
    assert main(["--help"]) == 0
    assert "usage" in capsys.readouterr().out


def test_subcommand_help_returns_0(capsys):
    assert main(["status", "-h"]) == 0
    assert "usage" in capsys.readouterr().out


def test_setup_reports_the_fonts_the_face_model_and_the_caption_dictionary(monkeypatch, capsys):
    folders = []
    monkeypatch.setattr("pipeline.cli.setup_fonts", lambda dest: [Path("fonts/regular.ttf"), Path("fonts/bold.ttf")])
    monkeypatch.setattr("pipeline.cli.setup_models", lambda dest: Path("models/face.onnx"))
    monkeypatch.setattr("pipeline.cli.setup_dictionary", lambda dest: folders.append(dest) or Path("models/dict.txt"))
    assert main(["setup"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "fonts": [str(Path("fonts/regular.ttf")), str(Path("fonts/bold.ttf"))],
                       "yunet": str(Path("models/face.onnx")), "dictionary": str(Path("models/dict.txt"))}
    assert folders == [models_dir()]


def test_doctor_failure_exits_1(monkeypatch, capsys):
    monkeypatch.setattr("pipeline.cli.doctor", lambda: {"ok": False, "fonts": False})
    assert main(["doctor"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["fonts"] is False


def test_doctor_success_exits_0(monkeypatch, capsys):
    monkeypatch.setattr("pipeline.cli.doctor", lambda: {"ok": True})
    assert main(["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---- storyboard, approve and render: one at a time in a project --------------------------------


@pytest.mark.parametrize("name, extra, function", [
    ("storyboard", [], "build_storyboard"),
    ("approve", ["gate1"], "approve_gate"),
    ("render", [], "render"),
])
def test_storyboard_approve_and_render_refuse_while_the_project_is_busy(project, monkeypatch, capsys, name, extra,
                                                                         function):
    ran: list[bool] = []  # for each run of the step: whether the project lock was held while it ran (the command's)

    def step(p, *args, **kwargs):
        lock = ProjectLock(p)
        try:
            lock.acquire()
        except ProjectBusy:
            ran.append(True)
        else:
            lock.release()
            ran.append(False)
        return {}

    monkeypatch.setattr(cli, function, step)
    with ProjectLock(project):  # the review page's job, or another command, is running
        assert main([name, project.name, *extra]) == 1
        assert json.loads(capsys.readouterr().out) == {"ok": False, "error": PROJECT_BUSY_ERROR}
    assert ran == []
    assert main([name, project.name, *extra]) == 0
    assert ran == [True]
    with ProjectLock(project):  # the command let it go
        pass


# ---- review: the review page -------------------------------------------------------------------

REVIEW_URL = "http://127.0.0.1:4321/?key=a-made-up-key"


def fake_review_server(on_wait):
    """A class to put in place of ReviewServer for the review command: it serves nothing, and wait() returns what
    `on_wait(server)` returns. stop() sets `stopped`."""

    class FakeServer:
        def __init__(self, p):
            self.p, self.port, self.url = p, 4321, REVIEW_URL
            self.stopped = threading.Event()

        def start(self) -> str:
            return self.url

        def wait(self) -> str:
            return on_wait(self)

        def stop(self) -> None:
            self.stopped.set()

    return FakeServer


def validated_project(tmp_path):
    p = edited_project_from_script(tmp_path, "第一句話。今天我們來聊剪輯。")
    validate_project(p)
    return p


def test_review_command_announces_one_json_line_first_and_never_opens_a_browser_with_no_browser(
        tmp_path, capsys, monkeypatch):
    p = validated_project(tmp_path)
    monkeypatch.setattr(webbrowser, "open", lambda *args, **kwargs: pytest.fail("a browser was opened"))
    printed = []

    def on_wait(server):
        printed.append(capsys.readouterr().out)  # what was printed before the command waits
        return "idle"

    monkeypatch.setattr(cli, "ReviewServer", fake_review_server(on_wait))
    assert main(["review", p.name, "--no-browser"]) == 0
    assert printed[0].count("\n") == 1
    assert json.loads(printed[0]) == {"ok": True, "serving": True, "url": REVIEW_URL, "port": 4321,
                                      "video": str(review_video_path(p))}
    assert json.loads(capsys.readouterr().out) == {"ok": True, "stopped": "idle"}


def test_review_command_opens_the_browser_and_stops_on_sigterm_and_puts_the_old_handler_back(
        tmp_path, capsys, monkeypatch):
    """The handler the command installs is called as a SIGTERM would call it; no signal is sent."""
    p = validated_project(tmp_path)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *args, **kwargs: opened.append(url))

    def previous(signum, frame):
        raise AssertionError("the handler from before the command was called")

    def on_wait(server):
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is not previous
        handler(signal.SIGTERM, None)
        assert server.stopped.wait(10)
        return "stopped"

    monkeypatch.setattr(cli, "ReviewServer", fake_review_server(on_wait))
    original = signal.signal(signal.SIGTERM, previous)
    try:
        assert main(["review", p.name]) == 0
        assert signal.getsignal(signal.SIGTERM) is previous
        lines = capsys.readouterr().out.splitlines()
        assert json.loads(lines[0])["serving"] is True
        assert json.loads(lines[1]) == {"ok": True, "stopped": "stopped"}
        assert opened == [REVIEW_URL]

        p.edit_path.write_text(p.edit_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # not validated
        assert main(["review", p.name]) == 1
        assert json.loads(capsys.readouterr().out) == {"ok": False, "error": NOT_VALIDATED_REFUSAL}
        assert signal.getsignal(signal.SIGTERM) is previous
    finally:
        signal.signal(signal.SIGTERM, original)


def test_leak_probe_registers_a_command():
    @command("_leak_probe")
    def _probe(args):
        return {}

    assert "_leak_probe" in registered_commands()


def test_test_commands_do_not_leak():
    # Runs right after test_leak_probe_registers_a_command, which registered "_leak_probe".
    assert "_leak_probe" not in registered_commands()

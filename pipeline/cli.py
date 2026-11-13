"""Command line entry point: subcommand registry, one-line JSON output, exit codes.

Exit codes: 0 success, 1 execution error (including bad arguments, an unexpected exception, and a
handler result that says `"ok": false`), 2 invalid edit.json. Help text is plain text on stdout, exit 0.
The review command prints one more line first, while it serves (see _review). storyboard, approve and render
hold the project's lock (project.ProjectLock) while they run, and refuse with exit code 1 while another of them,
or the review page's approval job, holds it.
"""

import argparse
import json
import signal
import sys
import threading
import traceback
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

from pipeline.align import transcribe
from pipeline.asr import MODELS
from pipeline.assets import doctor, fonts_dir, models_dir, setup_dictionary, setup_fonts, setup_models
from pipeline.edit import validate_project
from pipeline.errors import PipelineError
from pipeline.prep import prep
from pipeline.project import (
    Project,
    ProjectLock,
    approve_gate,
    create_project,
    list_projects,
    load_project,
    next_step,
    project_status,
)
from pipeline.render import render
from pipeline.review_server import ReviewServer, serve_review
from pipeline.storyboard import build_storyboard
from pipeline.trim import trim

Handler = Callable[[argparse.Namespace], dict]
ArgSpec = tuple[str, dict]

_COMMANDS: dict[str, tuple[Handler, list[ArgSpec]]] = {}


def command(name: str, args: Sequence[ArgSpec] = ()) -> Callable[[Handler], Handler]:
    """Register `handler(args) -> dict` as subcommand `name`.

    Each entry of `args` is `(flag_or_name, kwargs)` and goes to `add_argument` as is.
    """

    def register(handler: Handler) -> Handler:
        _COMMANDS[name] = (handler, list(args))
        return handler

    return register


def registered_commands() -> list[str]:
    return sorted(_COMMANDS)


class _UsageError(PipelineError):
    """Raised instead of letting argparse print usage text and exit."""


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="pipeline")
    # No metavar: the top-level help then lists every registered subcommand name.
    subparsers = parser.add_subparsers(dest="command", required=True, title="commands")
    for name in registered_commands():
        handler, arg_specs = _COMMANDS[name]
        sub = subparsers.add_parser(name)
        for flag, kwargs in arg_specs:
            sub.add_argument(flag, **kwargs)
        sub.set_defaults(handler=handler)
    return parser


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _parse(argv: list[str] | None) -> argparse.Namespace | int:
    """The parsed arguments, or the exit code when argparse stopped after printing help."""
    try:
        return _build_parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse(argv)
        if isinstance(args, int):
            return args
        result = args.handler(args)
        _emit({"ok": True, **result})
        return 1 if result.get("ok") is False else 0
    except PipelineError as error:
        extra = {key: value for key, value in error.extra.items() if key not in ("ok", "error")}
        _emit({"ok": False, "error": error.message, **extra})
        return error.exit_code
    except Exception as error:
        traceback.print_exc()
        _emit({"ok": False, "error": f"unexpected error: {type(error).__name__}: {error}"})
        return 1


@command(
    "new",
    args=[("video", {}), ("script", {}), ("--slug", {"required": True}), ("--date", {"default": None})],
)
def _new(args: argparse.Namespace) -> dict:
    project = create_project(Path(args.video), Path(args.script), slug=args.slug, date=args.date)
    return {"project": project.name, "dir": str(project.dir)}


def _listed(project: Project) -> dict:
    """One line of the status listing: the next step, or the error when the project cannot be read."""
    try:
        return {"name": project.name, "next": next_step(project)}
    except PipelineError as error:
        return {"name": project.name, "error": error.message}
    except Exception as error:  # one project must not stop the listing of the others
        traceback.print_exc()
        return {"name": project.name, "error": f"unexpected error: {type(error).__name__}: {error}"}


@command("status", args=[("name", {"nargs": "?"})])
def _status(args: argparse.Namespace) -> dict:
    if args.name is None:
        return {"projects": [_listed(p) for p in list_projects()]}
    return project_status(load_project(args.name))


@command("approve", args=[("name", {}), ("gate", {})])
def _approve(args: argparse.Namespace) -> dict:
    project = load_project(args.name)
    with ProjectLock(project):
        return {"project": project.name, "gate": args.gate, **approve_gate(project, args.gate)}


@command("prep", args=[("name", {}), ("--force", {"action": "store_true"})])
def _prep(args: argparse.Namespace) -> dict:
    return prep(load_project(args.name), force=args.force)


@command(
    "transcribe",
    args=[
        ("name", {}),
        ("--asr-model", {"choices": sorted(MODELS), "default": "1.7b"}),
        ("--asr-json", {"default": None}),
        ("--force", {"action": "store_true"}),
    ],
)
def _transcribe(args: argparse.Namespace) -> dict:
    asr_json = None if args.asr_json is None else Path(args.asr_json)
    return transcribe(load_project(args.name), model_key=args.asr_model, asr_json=asr_json, force=args.force)


@command("trim", args=[("name", {}), ("--force", {"action": "store_true"})])
def _trim(args: argparse.Namespace) -> dict:
    return trim(load_project(args.name), force=args.force)


@command("validate", args=[("name", {})])
def _validate(args: argparse.Namespace) -> dict:
    return validate_project(load_project(args.name))


@command("storyboard", args=[("name", {}), ("--force", {"action": "store_true"})])
def _storyboard(args: argparse.Namespace) -> dict:
    project = load_project(args.name)
    with ProjectLock(project):
        return build_storyboard(project, force=args.force)


@command("render", args=[("name", {}), ("--force", {"action": "store_true"})])
def _render(args: argparse.Namespace) -> dict:
    project = load_project(args.name)
    with ProjectLock(project):
        return render(project, force=args.force)


@command("review", args=[("name", {}), ("--no-browser", {"action": "store_true"})])
def _review(args: argparse.Namespace) -> dict:
    """Serve the review page until it stops (see review_server.serve_review). The first line,
    {"ok": true, "serving": true, "url", "port", "video"}, is printed and flushed as soon as the page is
    served; the result is the second line. --no-browser leaves the browser closed.

    Once the server exists, SIGTERM stops it; the handler that was there before is put back when the command
    returns. A signal handler runs in the main thread, which may be inside wait() or stop() holding a lock that
    stop() takes, so the handler hands stop() to a thread of its own."""
    p = load_project(args.name)
    previous = signal.getsignal(signal.SIGTERM)

    def server_factory(project: Project) -> ReviewServer:
        server = ReviewServer(project)
        signal.signal(signal.SIGTERM,
                      lambda signum, frame: threading.Thread(target=server.stop, name="review-stop").start())
        return server

    try:
        return serve_review(
            p,
            open_browser=(lambda url: None) if args.no_browser else webbrowser.open,
            announce=lambda info: (_emit({"ok": True, "serving": True, **info}), sys.stdout.flush()),
            server_factory=server_factory,
        )
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)


@command("setup")
def _setup(args: argparse.Namespace) -> dict:
    fonts = setup_fonts(fonts_dir())
    model = setup_models(models_dir())
    dictionary = setup_dictionary(models_dir())
    return {"fonts": [str(path) for path in fonts], "yunet": str(model), "dictionary": str(dictionary)}


@command("doctor")
def _doctor(args: argparse.Namespace) -> dict:
    return doctor()

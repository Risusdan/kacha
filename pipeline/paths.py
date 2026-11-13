"""Locations of the repo and of the studio (user footage, projects, inbox)."""

import os
from pathlib import Path

from pipeline.errors import PipelineError


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def studio_root() -> Path:
    """The studio folder: $REELS_STUDIO, or <repo>/studio when it is unset or empty."""
    value = os.environ.get("REELS_STUDIO", "")
    if not value:
        return repo_root() / "studio"
    path = Path(value)
    if not path.is_absolute():
        raise PipelineError(f"REELS_STUDIO must be an absolute path, got {value!r}")
    return path


def projects_root() -> Path:
    return studio_root() / "projects"


def inbox_root() -> Path:
    return studio_root() / "inbox"

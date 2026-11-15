"""The version of kacha: project.version in the repo's pyproject.toml, the only place it is written (spec
2026-11-15-versioning-design). kacha always runs from the repo, so each process reads the file itself, without
installing the package again. It reads it once: a process that runs for long (the review page's server) keeps the
version of the code it loaded, even when pyproject.toml gets a new version while it runs."""

import functools
import tomllib
from pathlib import Path

from pipeline.errors import PipelineError
from pipeline.paths import repo_root

PYPROJECT: Path = repo_root() / "pyproject.toml"


def kacha_version() -> str:
    """project.version of pyproject.toml, e.g. "0.2.0", as this process first read it."""
    return _version_of(PYPROJECT)


@functools.cache
def _version_of(path: Path) -> str:
    try:
        with open(path, "rb") as file:
            value = tomllib.load(file)["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PipelineError(f"cannot read the kacha version from pyproject.toml: {error}") from error
    except (KeyError, TypeError) as error:
        raise PipelineError("cannot read the kacha version from pyproject.toml: no project.version") from error
    if not isinstance(value, str):
        raise PipelineError("cannot read the kacha version from pyproject.toml: project.version is not text")
    return value

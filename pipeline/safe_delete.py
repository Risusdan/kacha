"""The only way the pipeline deletes a folder tree.

check_delete_target decides without touching anything; safe_rmtree runs it and only then
calls the remover. The home folder comes from the HOME variable alone: when it is unset or
empty the check refuses, it never asks the account database or expands "~".
"""

import os
import shutil
from collections.abc import Callable
from pathlib import Path

from pipeline.errors import UnsafeDeleteError


def _resolve(path: str | Path) -> Path:
    """The absolute, symlink-free form of `path`. Anything that cannot be resolved is refused."""
    try:
        return Path(os.path.abspath(path)).resolve()
    except (OSError, RuntimeError, ValueError) as exc:  # loops, embedded NUL, unreadable parents
        raise UnsafeDeleteError(f"refusing a path that cannot be resolved: {str(path)!r} ({exc})") from exc


def _same_folder(a: Path, b: Path) -> bool:
    """Whether `a` and `b` are one folder. Compares the folders themselves as well as the
    strings, because on a case-insensitive filesystem two spellings can name the same folder."""
    if a == b:
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _is_or_contains(folder: Path, other: Path) -> bool:
    """Whether `other` is `folder` or one of its ancestors."""
    return any(_same_folder(other, candidate) for candidate in (folder, *folder.parents))


def _require_absolute(value: str | Path, what: str) -> str:
    raw = os.fspath(value)
    if raw == "" or not os.path.isabs(raw):
        raise UnsafeDeleteError(f"refusing an empty or relative {what}: {raw!r}")
    return raw


def check_delete_target(path: str | Path, *, allowed_root: Path, home: str | None) -> Path:
    """Return the resolved absolute `path` when deleting it is safe, otherwise raise
    UnsafeDeleteError. Deletes nothing."""
    if home is None or home == "":
        raise UnsafeDeleteError("refusing to delete: HOME is unset or empty, so the home folder is unknown")
    if not os.path.isabs(home):
        raise UnsafeDeleteError(f"refusing to delete: HOME must be an absolute path, got {home!r}")
    home_dir = _resolve(home)

    raw = _require_absolute(path, "target path")
    target = _resolve(raw)
    if target != Path(os.path.abspath(raw)):
        raise UnsafeDeleteError(f"refusing a target that is, or sits under, a symbolic link: {raw!r}")
    if target == Path(target.anchor):
        raise UnsafeDeleteError("refusing to delete the filesystem root")
    if _is_or_contains(home_dir, target):
        raise UnsafeDeleteError(f"refusing to delete HOME or one of its ancestors: {str(target)!r}")

    root = _resolve(_require_absolute(allowed_root, "allowed_root"))
    if root == Path(root.anchor):
        raise UnsafeDeleteError("refusing allowed_root that is the filesystem root")
    if _is_or_contains(home_dir, root):
        raise UnsafeDeleteError(f"refusing allowed_root that is HOME or one of its ancestors: {str(root)!r}")
    if root not in target.parents:  # also false when target is allowed_root itself
        raise UnsafeDeleteError(
            f"refusing a target that is not strictly inside allowed_root {str(root)!r}: {str(target)!r}"
        )
    return target


def safe_rmtree(
    path: str | Path,
    *,
    allowed_root: Path,
    remover: Callable[[Path], None] = shutil.rmtree,
) -> None:
    """Delete the folder tree at `path` with `remover`, but only after check_delete_target
    approves it. `remover` receives the resolved path."""
    target = check_delete_target(path, allowed_root=allowed_root, home=os.environ.get("HOME"))
    remover(target)

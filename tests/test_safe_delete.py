"""Tests for pipeline.safe_delete.

Every path handed to safe_rmtree is a fake folder under pytest's tmp_path and the remover
is a recorder, except test_deletes_inside_root, which deletes a fake folder with the real
shutil.rmtree. Dangerous paths such as "/" go only to the pure check_delete_target.
"""

from pathlib import Path

import pytest

from pipeline.errors import UnsafeDeleteError
from pipeline.safe_delete import check_delete_target, safe_rmtree


def must_not_delete(path):
    pytest.fail(f"remover was called with {path}")


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "fakehome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    r = tmp_path / "studio" / "projects"
    r.mkdir(parents=True)
    return r


def test_recorder_is_wired(root):
    target = root / "p1" / "review"
    target.mkdir(parents=True)
    rec = []
    safe_rmtree(target, allowed_root=root, remover=rec.append)
    assert rec == [target.resolve()] and target.exists()


def test_deletes_inside_root(root):
    target = root / "p1" / "tmp"
    (target / "x").mkdir(parents=True)
    safe_rmtree(target, allowed_root=root)
    assert not target.exists() and (root / "p1").exists()


def test_check_is_pure(root, tmp_path):
    target = root / "p1" / "review"
    target.mkdir(parents=True)
    assert check_delete_target(target, allowed_root=root, home=str(tmp_path / "fakehome")) == target
    assert target.exists()


@pytest.mark.parametrize("bad", ["", Path(""), ".", "relative/dir"])
def test_refuses_empty_and_relative(root, monkeypatch, bad):
    # With the working directory inside allowed_root, each of these would resolve to a folder
    # that is allowed, so only the empty/relative rule can refuse them.
    (root / "p1").mkdir()
    monkeypatch.chdir(root / "p1")
    rec = []
    with pytest.raises(UnsafeDeleteError, match="empty or relative"):
        safe_rmtree(bad, allowed_root=root, remover=rec.append)
    assert rec == []


def test_check_refuses_filesystem_root(tmp_path):
    with pytest.raises(UnsafeDeleteError, match="delete the filesystem root"):
        check_delete_target("/", allowed_root=tmp_path / "projects", home=str(tmp_path / "fakehome"))


def test_refuses_home_and_its_ancestor(root, tmp_path):  # both are fake home folders
    for target in (tmp_path / "fakehome", tmp_path):
        with pytest.raises(UnsafeDeleteError):
            safe_rmtree(target, allowed_root=root, remover=must_not_delete)


def test_refuses_home_and_its_ancestor_inside_root(root, monkeypatch):
    # The fake HOME and its ancestor both sit inside allowed_root, so only the HOME rule can
    # refuse them; the outside-root rule cannot.
    home = root / "p1" / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for target in (home, root / "p1"):
        with pytest.raises(UnsafeDeleteError, match="HOME") as refused:
            safe_rmtree(target, allowed_root=root, remover=must_not_delete)
        # allowed_root is itself an ancestor of this HOME, so make sure the refusal came from
        # the rule about the target and not from the one about allowed_root.
        assert "allowed_root" not in str(refused.value)


@pytest.mark.parametrize("home", [None, ""])
def test_refuses_when_home_unset_or_empty(root, monkeypatch, home):
    target = root / "p1" / "review"
    target.mkdir(parents=True)
    if home is None:
        monkeypatch.delenv("HOME")
    else:
        monkeypatch.setenv("HOME", home)
    with pytest.raises(UnsafeDeleteError, match="HOME"):
        safe_rmtree(target, allowed_root=root, remover=must_not_delete)


@pytest.mark.parametrize("home", [None, "", "relative/home"])
def test_check_refuses_unusable_home(root, home):
    target = root / "p1" / "review"
    target.mkdir(parents=True)
    with pytest.raises(UnsafeDeleteError, match="HOME"):
        check_delete_target(target, allowed_root=root, home=home)


def test_refuses_outside_root_and_root_itself(root, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for target in (outside, root):
        with pytest.raises(UnsafeDeleteError, match="allowed_root"):
            safe_rmtree(target, allowed_root=root, remover=must_not_delete)


def test_refuses_symlink_and_symlinked_parent(root, tmp_path):
    real = tmp_path / "realdir"
    (real / "sub").mkdir(parents=True)  # the link target is also inside the temp folder
    (root / "link").symlink_to(real)
    for target in (root / "link", root / "link" / "sub"):
        with pytest.raises(UnsafeDeleteError, match="symbolic link"):
            safe_rmtree(target, allowed_root=root, remover=must_not_delete)


def test_refuses_symlink_that_points_inside_root(root):
    # The link and its target are both inside allowed_root, so only the symlink rule can refuse.
    (root / "realdir" / "sub").mkdir(parents=True)
    (root / "link").symlink_to(root / "realdir")
    for target in (root / "link", root / "link" / "sub"):
        with pytest.raises(UnsafeDeleteError, match="symbolic link"):
            safe_rmtree(target, allowed_root=root, remover=must_not_delete)


def test_refuses_symlink_loop(root):
    (root / "a").symlink_to(root / "b")
    (root / "b").symlink_to(root / "a")
    with pytest.raises(UnsafeDeleteError):
        safe_rmtree(root / "a", allowed_root=root, remover=must_not_delete)


def test_refuses_embedded_null_character(root):
    with pytest.raises(UnsafeDeleteError):
        safe_rmtree(str(root / "p1") + "\0x", allowed_root=root, remover=must_not_delete)


@pytest.mark.parametrize("bad_root, cwd", [
    (Path("studio/projects"), "."),
    (Path("projects"), "studio"),
    (Path(""), "studio/projects"),
    (Path("."), "studio/projects"),
])
def test_refuses_relative_allowed_root(root, tmp_path, monkeypatch, bad_root, cwd):
    # With this working directory the relative allowed_root would point at the real `root`,
    # so only the relative rule can refuse it.
    monkeypatch.chdir(tmp_path / cwd)
    target = root / "p1" / "review"
    target.mkdir(parents=True)
    with pytest.raises(UnsafeDeleteError, match="empty or relative"):
        safe_rmtree(target, allowed_root=bad_root, remover=must_not_delete)


def test_refuses_dangerous_allowed_root(tmp_path):
    home = tmp_path / "fakehome"
    home.mkdir()
    for bad_root in (Path("rel"), home, tmp_path):
        with pytest.raises(UnsafeDeleteError, match="allowed_root"):
            check_delete_target(home / "x", allowed_root=bad_root, home=str(home))


def test_check_refuses_filesystem_root_as_allowed_root(tmp_path):
    home = tmp_path / "fakehome"
    home.mkdir()
    with pytest.raises(UnsafeDeleteError, match="allowed_root that is the filesystem root"):
        check_delete_target(home / "x", allowed_root=Path("/"), home=str(home))


def test_refuses_home_spelled_in_another_case(root, tmp_path, monkeypatch):
    # On a case-insensitive filesystem "Home" and "home" are one folder, but their resolved
    # paths differ as strings.
    (tmp_path / "CaseProbe").mkdir()
    if not (tmp_path / "caseprobe").exists():
        pytest.skip("filesystem is case-sensitive")
    (root / "p1" / "home").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root / "p1" / "Home"))
    with pytest.raises(UnsafeDeleteError, match="HOME") as refused:
        safe_rmtree(root / "p1" / "home", allowed_root=root, remover=must_not_delete)
    assert "allowed_root" not in str(refused.value)

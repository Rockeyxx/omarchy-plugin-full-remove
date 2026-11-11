import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    for d in [".config/foo/bar", ".local/share/foo", ".local/state", ".cache/yay/foo-bin/src",
              ".cache/paru/clone/foo", ".var/app/org.x.Y", "snap/foo", "Documents/thesis",
              ".local/share/applications", ".config/autostart", ".mpv"]:
        (h / d).mkdir(parents=True, exist_ok=True)
    (h / ".claude.json").write_text("{}")
    (h / ".local/share/applications/foo.desktop").write_text("[Desktop Entry]\n")
    return h


@pytest.mark.parametrize(
    "rel",
    [".config/foo", ".local/share/foo", ".cache/yay/foo-bin", ".cache/paru/clone/foo",
     ".var/app/org.x.Y", "snap/foo", ".mpv", ".local/share/applications/foo.desktop"],
)
def test_allowed(fr, home, rel):
    assert fr.validate_home_path(str(home / rel), str(home)) is None


@pytest.mark.parametrize(
    "rel",
    [
        "Documents/thesis",          # non-dot entry in ~
        "Documents",
        ".config/foo/bar",           # grandchild of a scan root
        ".cache/yay/foo-bin/src",
        ".config",                   # scan root itself
        ".local/share",
        "",                          # home itself
        ".claude.json",              # protected
        ".config/hypr",              # protected (may not exist)
        ".config/../Documents",      # dot-dot
        "nonexistent-dir/.x",
        ".does-not-exist",
    ],
)
def test_refused(fr, home, rel):
    assert fr.validate_home_path(str(home / rel) if rel else str(home), str(home)) is not None


def test_relative_path_refused(fr, home):
    assert fr.validate_home_path(".config/foo", str(home)) is not None


def test_outside_home_refused(fr, home, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    assert fr.validate_home_path(str(tmp_path / "elsewhere"), str(home)) is not None


def test_symlinked_entry_is_allowed_as_link(fr, home, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    os.symlink(target, home / ".config/linked")
    assert fr.validate_home_path(str(home / ".config/linked"), str(home)) is None


def test_delete_symlink_removes_only_link(fr, home, tmp_path):
    target = tmp_path / "keep"
    target.mkdir()
    (target / "data").write_text("x")
    link = home / ".config/linked"
    os.symlink(target, link)
    fr.delete_path(str(link))
    assert not os.path.lexists(link)
    assert (target / "data").exists()


def test_delete_folder(fr, home):
    fr.delete_path(str(home / ".config/foo"))
    assert not (home / ".config/foo").exists()


@pytest.mark.skipif(shutil.which("gio") is None, reason="gio not installed")
def test_gio_trash_moves_link_not_target(fr, home, tmp_path, monkeypatch):
    target = tmp_path / "keep"
    target.mkdir()
    (target / "data").write_text("x")
    link = home / ".config/linked"
    os.symlink(target, link)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    ok, msg = fr.gio_trash(str(link))
    assert ok, msg
    assert not os.path.lexists(link)
    assert (target / "data").exists()
    trashed = home / ".local/share/Trash/files/linked"
    assert os.path.islink(trashed)


def test_trashinfo_parse(fr):
    text = "[Trash Info]\nPath=/home/u/.config/My%20App\nDeletionDate=2026-11-11T18:30:05\n"
    path, when = fr.parse_trashinfo(text)
    assert path == "/home/u/.config/My App"
    assert when.isoformat() == "2026-11-11T18:30:05"


def test_empty_trashed_only_this_run(fr, home, monkeypatch):
    import datetime as dt
    trash = home / ".local/share/Trash"
    (trash / "files").mkdir(parents=True)
    (trash / "info").mkdir()

    def entry(name, orig, when):
        (trash / "files" / name).mkdir()
        (trash / "info" / f"{name}.trashinfo").write_text(
            f"[Trash Info]\nPath={orig}\nDeletionDate={when}\n")

    orig = str(home / ".config/foo")
    entry("foo", orig, "2026-11-11T18:30:05")        # this run
    entry("foo.2", orig, "2026-11-10T09:00:00")      # older, same path
    entry("other", str(home / ".config/other"), "2026-11-11T18:30:06")
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    removed = fr.empty_trashed(dt.datetime(2026, 11, 11, 18, 30, 0), [orig])
    assert removed == [orig]
    assert not (trash / "files/foo").exists()
    assert not (trash / "info/foo.trashinfo").exists()
    assert (trash / "files/foo.2").exists()
    assert (trash / "files/other").exists()

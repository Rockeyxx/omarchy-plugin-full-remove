"""Tests for the root helper's validation, guard and cache-file rule.

The helper is sourced in bash (its main is guarded), so no function here runs
as root or changes anything on the system.
"""
import os
import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parent.parent / "root" / "full-remove-helper"


def sh(script: str, *args: str) -> subprocess.CompletedProcess:
    """Source the helper, then run `script` in bash with positional args."""
    return subprocess.run(
        ["bash", "-c", f'source "$1"; shift; {script}', "bash", str(HELPER), *args],
        capture_output=True,
        text=True,
    )


def ok(fn: str, value: str) -> bool:
    return sh(f'{fn} "$1"', value).returncode == 0


@pytest.mark.parametrize("name", ["sl", "lib32-glibc", "python-foo", "gtk2+extra", "foo@1", "a.b_c"])
def test_valid_pkg_name_accepts(name):
    assert ok("valid_pkg_name", name)


@pytest.mark.parametrize(
    "name", ["--root", "-Rdd", "Foo", "foo bar", "foo\nbar", "", "../x", ".foo", "fóo", "foo/bar", "@foo"]
)
def test_valid_pkg_name_rejects(name):
    assert not ok("valid_pkg_name", name)


def test_provides_name_is_not_an_installed_package():
    # `pacman -Qq -- sh` prints `bash`: a provides match must be refused.
    assert not ok("pkg_installed_exact", "sh")


def test_installed_package_matches_exactly():
    assert ok("pkg_installed_exact", "bash")


@pytest.mark.parametrize("i", ["com.github.tchx84.Flatseal", "org.gnome.Calculator", "io.a_b.c-d"])
def test_valid_flatpak_id_accepts(i):
    assert ok("valid_flatpak_id", i)


@pytest.mark.parametrize("i", ["org.gnome", "--system", "org..x.y", "1org.x.y", "org.x.y/stable", "org.x y.z", ""])
def test_valid_flatpak_id_rejects(i):
    assert not ok("valid_flatpak_id", i)


@pytest.mark.parametrize("s", ["hello-world", "vlc", "code2"])
def test_valid_snap_accepts(s):
    assert ok("valid_snap", s)


@pytest.mark.parametrize("s", ["snapd", "core22", "core", "bare", "-x", "a--b", "Hello", "x" * 41, ""])
def test_valid_snap_rejects(s):
    assert not ok("valid_snap", s)


@pytest.mark.parametrize("r", ["runtime/org.freedesktop.Platform/x86_64/25.08", "runtime/org.gnome.Platform/aarch64/47"])
def test_valid_runtime_ref_accepts(r):
    assert ok("valid_runtime_ref", r)


@pytest.mark.parametrize("r", ["org.x.App/x86_64/stable", "app/org.x.App/x86_64/stable", "runtime/--x/y/z", "runtime/a/b", ""])
def test_valid_runtime_ref_rejects(r):
    assert not ok("valid_runtime_ref", r)


# The guard reads omarchy deps from pacman; stub pacman so tests are hermetic.
STUB = "pacman() { if [[ $1 == -Qq ]]; then echo omarchy; return 0; fi; " \
       "if [[ $1 == -Qi ]]; then echo 'Depends On      : gum  hyprland>=0.50'; return 0; fi; return 1; }; "


@pytest.mark.parametrize(
    "pkg",
    ["glibc", "linux", "limine", "limine-snapper-sync", "ufw", "apparmor",
     "omarchy-settings", "pam", "iptables-nft", "amd-ucode", "gum", "hyprland"],
)
def test_guard_hits(pkg):
    assert sh(STUB + 'guard_hit "$1"', pkg).returncode == 0


@pytest.mark.parametrize("pkg", ["sl", "cowsay", "linux-docs", "linux-headers", "linux-zen-headers", "firefox",
                                 "linux-wallpaperengine-git", "linux-tools"])
def test_guard_allows(pkg):
    assert sh(STUB + 'guard_hit "$1"', pkg).returncode != 0


def test_removal_set_fails_closed_on_pacman_error():
    r = sh('pacman() { return 1; }; removal_set sl')
    assert r.returncode != 0


def test_removal_set_fails_closed_on_empty_output():
    r = sh('pacman() { return 0; }; removal_set sl')
    assert r.returncode != 0


def test_removal_set_prints_names():
    r = sh('pacman() { printf "sl\\nlibfoo\\n"; }; removal_set sl')
    assert r.returncode == 0
    assert r.stdout.split() == ["sl", "libfoo"]


def make_cache(tmp_path: Path) -> Path:
    d = tmp_path / "pkg"
    d.mkdir()
    for f in [
        "sl-5.05-1-x86_64.pkg.tar.zst",
        "sl-5.05-1-x86_64.pkg.tar.zst.sig",
        "sl-extra-1.0-1-any.pkg.tar.zst",
        "lib32-sl-1:2.0-3-x86_64.pkg.tar.xz",
        "notes.txt",
    ]:
        (d / f).write_text("x")
    (d / "download-abc").mkdir()
    (d / "download-abc" / "sl-5.06-1-x86_64.pkg.tar.zst").write_text("x")
    os.symlink(d / "sl-5.05-1-x86_64.pkg.tar.zst", d / "sl-0.1-1-x86_64.pkg.tar.zst")
    return d


def test_cache_files_for_matches_exact_name_only(tmp_path):
    d = make_cache(tmp_path)
    r = sh('cache_files_for "$1" sl', str(d))
    assert r.returncode == 0
    assert sorted(Path(p).name for p in r.stdout.split("\n") if p) == [
        "sl-5.05-1-x86_64.pkg.tar.zst",
        "sl-5.05-1-x86_64.pkg.tar.zst.sig",
    ]


def test_cache_files_for_handles_epoch_and_prefix(tmp_path):
    d = make_cache(tmp_path)
    r = sh('cache_files_for "$1" lib32-sl sl-extra', str(d))
    assert sorted(Path(p).name for p in r.stdout.split("\n") if p) == [
        "lib32-sl-1:2.0-3-x86_64.pkg.tar.xz",
        "sl-extra-1.0-1-any.pkg.tar.zst",
    ]


def test_say_survives_closed_pipe():
    # stdout closed: `say` must not abort the script.
    r = subprocess.run(
        ["bash", "-c", 'source "$1"; exec 1>&-; say hello; say again; echo done >&2', "bash", str(HELPER)],
        capture_output=True, text=True,
    )
    assert "done" in r.stderr


def test_version_needs_no_root():
    r = subprocess.run(["bash", str(HELPER), "--version"], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.strip() == "full-remove-helper 1"


def test_remove_refuses_without_root():
    r = subprocess.run(["bash", str(HELPER), "remove", "pkg:sl"], capture_output=True, text=True)
    assert r.returncode != 0
    assert "root" in r.stderr.lower()


def test_unknown_item_kind_rejected_before_root_check_irrelevant():
    r = subprocess.run(["bash", str(HELPER), "bogus"], capture_output=True, text=True)
    assert r.returncode == 2


def make_modules(tmp_path, kernels):
    d = tmp_path / "modules"
    for i, k in enumerate(kernels):
        (d / f"6.{i}.0").mkdir(parents=True)
        (d / f"6.{i}.0" / "pkgbase").write_text(k + "\n")
    return d


@pytest.mark.parametrize("pkg", ["linux-cachyos", "linux-omarchy"])
def test_guard_refuses_installed_kernels(tmp_path, pkg):
    d = make_modules(tmp_path, ["linux-cachyos", "linux-omarchy"])
    assert sh(STUB + f'MODULES_DIR="$1"; guard_hit {pkg}', str(d)).returncode == 0


def test_guard_allows_linux_named_non_kernel(tmp_path):
    d = make_modules(tmp_path, ["linux-omarchy"])
    assert sh(STUB + 'MODULES_DIR="$1"; guard_hit linux-wallpaperengine-git', str(d)).returncode != 0

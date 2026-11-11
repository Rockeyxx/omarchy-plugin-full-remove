import os
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    for d in [".config/obsidian", ".config/Code", ".config/hypr", ".local/share/obsidian",
              ".cache/obsidian-updater", ".var/app/md.obsidian.Obsidian", ".cache/yay/obsidian-bin",
              ".cache/paru/clone/obsidian-bin", "snap/foo", ".obsidian", "obsidian-vault",
              ".config/Electron", ".codex", ".vscode", ".local/share/applications", ".config/autostart",
              ".config/unrelated"]:
        (h / d).mkdir(parents=True, exist_ok=True)
    (h / ".config/obsidian/big").write_bytes(b"x" * 100_000)
    (h / ".local/share/applications/obsidian.desktop").write_text("[Desktop Entry]\n")
    (h / ".config/autostart/obsidian.desktop").write_text("[Desktop Entry]\n")
    (h / ".claude.json").write_text("{}")
    return h


def paths(results, home):
    return {str(Path(r["path"]).relative_to(home)): r for r in results}


def test_scan_finds_strong_matches(fr, home):
    res = paths(fr.scan_leftovers(str(home), {"obsidian"}, set(), []), home)
    for rel in [".config/obsidian", ".local/share/obsidian", ".cache/obsidian-updater", ".obsidian",
                ".local/share/applications/obsidian.desktop", ".config/autostart/obsidian.desktop"]:
        assert rel in res, rel
        assert res[rel]["tier"] == "strong"


def test_scan_ignores_non_dot_home_entries(fr, home):
    res = paths(fr.scan_leftovers(str(home), {"obsidian"}, set(), []), home)
    assert "obsidian-vault" not in res


def test_scan_never_returns_protected(fr, home):
    res = paths(fr.scan_leftovers(str(home), {"hypr", "claude"}, set(), []), home)
    assert ".config/hypr" not in res
    assert ".claude.json" not in res


def test_scan_fixed_locations(fr, home):
    fixed = [str(home / ".cache/yay/obsidian-bin"), str(home / ".cache/paru/clone/obsidian-bin"),
             str(home / ".var/app/md.obsidian.Obsidian"), str(home / "snap/foo"),
             str(home / ".cache/yay/missing")]
    res = paths(fr.scan_leftovers(str(home), set(), set(), [], fixed=fixed), home)
    for rel in [".cache/yay/obsidian-bin", ".cache/paru/clone/obsidian-bin", ".var/app/md.obsidian.Obsidian", "snap/foo"]:
        assert res[rel]["tier"] == "strong"
    assert ".cache/yay/missing" not in res


def test_scan_short_keyword_exact_only(fr, home):
    res = paths(fr.scan_leftovers(str(home), {"code"}, {"vscode"}, []), home)
    assert res[".config/Code"]["tier"] == "strong"
    assert res[".vscode"]["tier"] == "maybe"
    assert ".codex" not in res


def test_scan_demotes_shared_matches(fr, home):
    res = paths(fr.scan_leftovers(str(home), {"obsidian"}, set(), [("obsidian", "Obsidian (Flatpak)")]), home)
    assert res[".config/obsidian"]["tier"] == "maybe"
    assert "Obsidian (Flatpak)" in res[".config/obsidian"]["reason"]


def test_scan_size_counts_blocks(fr, home):
    res = paths(fr.scan_leftovers(str(home), {"obsidian"}, set(), []), home)
    assert res[".config/obsidian"]["size"] >= 100_000


def test_scan_symlink_is_reported_as_link(fr, home, tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    (big / "f").write_bytes(b"x" * 1_000_000)
    os.symlink(big, home / ".config/obsidian-link")
    res = paths(fr.scan_leftovers(str(home), {"obsidian"}, set(), []), home)
    r = res[".config/obsidian-link"]
    assert r["link"] is True
    assert r["size"] < 1_000_000


def test_tree_size_does_not_follow_symlinks(fr, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "small").write_bytes(b"x" * 10)
    big = tmp_path / "big"
    big.mkdir()
    (big / "f").write_bytes(b"x" * 1_000_000)
    os.symlink(big, d / "link")
    assert fr.tree_size(str(d)) < 1_000_000

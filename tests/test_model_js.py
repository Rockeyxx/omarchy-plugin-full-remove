"""Model.js (QML `.pragma library`) tested under node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODEL = Path(__file__).resolve().parent.parent / "Model.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def js(expr: str):
    src = MODEL.read_text().replace(".pragma library", "")
    code = src + f"\nprocess.stdout.write(JSON.stringify({expr}));\n"
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("n,s", [(0, "0 B"), (512, "512 B"), (2048, "2.0 KB"), (1572864, "1.5 MB"),
                                 (3 * 1024**3, "3.0 GB")])
def test_format_size(n, s):
    assert js(f"formatSize({n})") == s


def test_source_label():
    assert js('["pacman","aur","flatpak-system","flatpak-user","snap","appimage"].map(sourceLabel)') == \
        ["pacman", "AUR", "Flatpak", "Flatpak (user)", "Snap", "AppImage"]


def test_filter_items_matches_display_name_description_case_insensitive():
    items = [{"display": "Visual Studio Code", "name": "code", "description": "editor"},
             {"display": "mpv", "name": "mpv", "description": "media player"}]
    assert [i["name"] for i in js(f"filterItems({json.dumps(items)}, 'STUDIO')")] == ["code"]
    assert [i["name"] for i in js(f"filterItems({json.dumps(items)}, 'player')")] == ["mpv"]
    assert len(js(f"filterItems({json.dumps(items)}, '')")) == 2


def test_backend_argv():
    assert js('backendArgv("/p", "plan", ["pkg:sl"])') == ["python3", "-I", "/p/backend/fullremove.py", "plan", "pkg:sl"]


def test_display_command_quotes_unsafe_args():
    assert js('displayCommand(["pkexec", "/usr/lib/x", "remove", "pkg:sl", "a b", "it\'s"])') == \
        "pkexec /usr/lib/x remove pkg:sl 'a b' 'it'\\''s'"


def test_local_path_from_file_url():
    assert js('localPath("file:///home/u/My%20Plugins/x/backend")') == "/home/u/My Plugins/x/backend"


def test_report_text_lists_everything():
    plan = {"removes": ["sl"], "breaks": [], "cache": ["/var/cache/pacman/pkg/sl-1-1-x86_64.pkg.tar.zst"],
            "root_argv": ["pkexec", "/usr/lib/full-remove/full-remove-helper", "remove", "pkg:sl"],
            "user": [{"id": "dev:npm:x", "command": "npm uninstall -g x"}],
            "leftovers": [{"path": "/h/.config/sl", "tier": "strong", "reason": "matches 'sl'", "size": 4096},
                          {"path": "/h/.sl", "tier": "maybe", "reason": "matched 'x'", "size": 0}]}
    cleanup = {"orphans": ["libfoo"], "orphan_cache": [], "runtimes": [{"ref": "runtime/a/x86_64/1", "installation": "system"}]}
    text = js(f"reportText({json.dumps(plan)}, {json.dumps(cleanup)})")
    for s in ["sl", "pkexec /usr/lib/full-remove/full-remove-helper remove pkg:sl", "npm uninstall -g x",
              "/h/.config/sl", "maybe", "libfoo", "runtime/a/x86_64/1", "approximate", "sl-1-1-x86_64.pkg.tar.zst"]:
        assert s in text, s


def test_tick_defaults_strong_only():
    lefts = [{"path": "/a", "tier": "strong"}, {"path": "/b", "tier": "maybe"}]
    assert js(f"defaultTicks({json.dumps(lefts)})") == {"/a": True}


def test_cleanup_diff_only_new_orphans_and_runtimes():
    before = {"orphans": ["boost", "doxygen"], "orphan_cache": ["/c/boost-1-1-x86_64.pkg.tar.zst"],
              "runtimes": [{"ref": "runtime/a/x86_64/1", "installation": "system"}]}
    after = {"orphans": ["boost", "doxygen", "libfoo"],
             "orphan_cache": ["/c/boost-1-1-x86_64.pkg.tar.zst", "/c/libfoo-2-1-x86_64.pkg.tar.zst",
                              "/c/libfoo-2-1-x86_64.pkg.tar.zst.sig"],
             "runtimes": [{"ref": "runtime/a/x86_64/1", "installation": "system"},
                          {"ref": "runtime/b/x86_64/2", "installation": "system"},
                          {"ref": "runtime/a/x86_64/1", "installation": "user"}]}
    d = js(f"cleanupDiff({json.dumps(before)}, {json.dumps(after)})")
    assert d["orphans"] == ["libfoo"]
    assert d["orphan_cache"] == ["/c/libfoo-2-1-x86_64.pkg.tar.zst", "/c/libfoo-2-1-x86_64.pkg.tar.zst.sig"]
    assert d["runtimes"] == [{"ref": "runtime/b/x86_64/2", "installation": "system"},
                             {"ref": "runtime/a/x86_64/1", "installation": "user"}]


def test_cleanup_diff_nothing_new_is_empty():
    same = {"orphans": ["boost"], "orphan_cache": [], "runtimes": []}
    d = js(f"cleanupDiff({json.dumps(same)}, {json.dumps(same)})")
    assert d["orphans"] == [] and d["runtimes"] == []
    assert js(f"cleanupEmpty(cleanupDiff({json.dumps(same)}, {json.dumps(same)}))") is True


def test_cleanup_diff_without_baseline_returns_nothing():
    # no "before" snapshot (e.g. it failed): never fall back to offering every orphan
    after = {"orphans": ["boost"], "orphan_cache": [], "runtimes": []}
    assert js(f"cleanupEmpty(cleanupDiff(null, {json.dumps(after)}))") is True


def test_orphan_tab_items():
    c = {"orphans": ["boost"], "orphan_cache": [], "runtimes": [{"ref": "runtime/a/x86_64/1", "installation": "system"},
                                                                {"ref": "runtime/u/x86_64/1", "installation": "user"}]}
    items = js(f"orphanItems({json.dumps(c)})")
    assert [i["id"] for i in items] == ["orphan:boost", "runtime:runtime/a/x86_64/1", "user-runtimes"]

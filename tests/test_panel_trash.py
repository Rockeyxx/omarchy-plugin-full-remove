"""Panel.qml's Trash cutoff, run under node: the real functions are cut out of
Panel.qml and evaluated inside `with (root)` against a stub panel."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parent.parent / "Panel.qml"
FUNCS = ["resetToSelect", "afterRun", "tickedLeftovers", "trashLeftovers", "emptyTrashed"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const calls = [];
const replies = {
  installed: () => ({ installed: {} }),
  trash: (args) => ({ results: args.map((p) => ({ ok: true, path: p })) }),
  "empty-trashed": () => ({ removed: [] }),
};
const root = {
  step: "select", notice: "", error: "", busy: "", query: "", partial: false,
  selected: {}, selectedCount: 0, plan: null, cleanup: null, runState: null,
  leftTicks: {}, trashed: [], trashSince: "", confirming: "",
  logFile: { reload() {} }, loadLists() {}, loadCleanup() {},
  call(cmd, args, cb) { calls.push([cmd, args]); cb(replies[cmd](args)); },
};
with (root) { var fns = (function () { FUNCS_SRC; return { FUNC_NAMES }; })(); }

function removalRun(start, plan, tickLeftovers) {
  fns.resetToSelect();
  root.plan = plan;
  root.runState = { stage: "remove", start: start, ids: plan.user.map((u) => u.id),
                    finished: true, cancelled: false, steps: [{}] };
  fns.afterRun(root.runState);
  if (root.step === "leftovers") {
    root.leftTicks = {};
    if (tickLeftovers) plan.leftovers.forEach((l) => { root.leftTicks[l.path] = true; });
    fns.trashLeftovers();
  }
  // a cleanup run replaces runState before the user empties the Trash
  root.runState = { stage: "cleanup", start: "2099-01-01T00:00:00", finished: true, steps: [] };
  fns.emptyTrashed();
  return calls.filter((c) => c[0] === "empty-trashed").pop()[1];
}

const out = {};
SCENARIO;
process.stdout.write(JSON.stringify(out));
"""


def panel_function(src: str, name: str) -> str:
    m = re.search(rf"^  function {name}\(.*?^  }}$", src, re.S | re.M)
    assert m, f"{name} not found in Panel.qml"
    return m.group(0)


def run(scenario: str) -> dict:
    src = PANEL.read_text()
    code = (HARNESS.replace("FUNCS_SRC", "\n".join(panel_function(src, f) for f in FUNCS))
            .replace("FUNC_NAMES", ", ".join(FUNCS)).replace("SCENARIO", scenario))
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


APPIMAGE = {"user": [{"id": "appimage:/home/u/Apps/Foo.AppImage", "files": ["/home/u/Apps/Foo.AppImage"]}],
            "leftovers": []}
WITH_LEFTOVER = {"user": [{"id": "appimage:/home/u/Apps/Bar.AppImage", "files": ["/home/u/Apps/Bar.AppImage"]}],
                 "leftovers": [{"path": "/home/u/.config/Bar"}]}


def test_empty_uses_this_runs_start_after_an_earlier_run_trashed_leftovers():
    out = run(f"""
      removalRun("2026-11-10T09:00:00", {json.dumps(WITH_LEFTOVER)}, true);
      out.args = removalRun("2026-11-12T15:00:00", {json.dumps(APPIMAGE)}, false);
    """)
    assert out["args"] == ["2026-11-12T15:00:00", "/home/u/Apps/Foo.AppImage"]


def test_empty_uses_this_runs_start_when_leftovers_are_left_unticked():
    out = run(f'out.args = removalRun("2026-11-12T15:00:00", {json.dumps(WITH_LEFTOVER)}, false);')
    assert out["args"] == ["2026-11-12T15:00:00", "/home/u/Apps/Bar.AppImage"]


def test_empty_uses_this_runs_start_when_leftovers_are_trashed():
    out = run(f'out.args = removalRun("2026-11-12T15:00:00", {json.dumps(WITH_LEFTOVER)}, true);')
    assert out["args"] == ["2026-11-12T15:00:00", "/home/u/Apps/Bar.AppImage", "/home/u/.config/Bar"]

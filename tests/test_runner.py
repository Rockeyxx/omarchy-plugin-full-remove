import fcntl
import json
import os

import pytest


@pytest.fixture
def rdir(fr, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return fr.run_dir()


def state(rdir):
    with open(os.path.join(rdir, "state.json")) as f:
        return json.load(f)


def log_text(rdir):
    st = state(rdir)
    with open(st["log"]) as f:
        return f.read()


def step(name, rc=0, out="", effect=None):
    def fn(write):
        if out:
            write(out)
        if effect:
            effect()
        return rc
    return (name, fn)


def test_run_dir_is_private(fr, rdir):
    assert os.stat(rdir).st_mode & 0o777 == 0o700


def test_run_dir_refuses_symlink(fr, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    real = tmp_path / "elsewhere"
    real.mkdir()
    os.symlink(real, tmp_path / "full-remove")
    with pytest.raises(RuntimeError):
        fr.run_dir()


def test_steps_run_in_order_and_state_finishes(fr, rdir):
    st = fr.run_queue(rdir, "remove", ["pkg:sl"], [step("root", out="removed sl"), step("user", out="nothing")])
    assert st["finished"] and not st["cancelled"]
    assert [s["name"] for s in st["steps"]] == ["root", "user"]
    assert [s["rc"] for s in st["steps"]] == [0, 0]
    assert "removed sl" in log_text(rdir) and "nothing" in log_text(rdir)
    assert state(rdir)["finished"]


def test_cancel_lets_running_step_finish_and_skips_rest(fr, rdir):
    cancel = lambda: open(os.path.join(rdir, "cancel"), "w").close()
    st = fr.run_queue(rdir, "remove", [], [step("root", out="one", effect=cancel), step("user", out="two")])
    assert st["cancelled"]
    assert [s["name"] for s in st["steps"]] == ["root"]
    assert "two" not in log_text(rdir)


def test_stale_cancel_file_is_ignored(fr, rdir):
    open(os.path.join(rdir, "cancel"), "w").close()
    st = fr.run_queue(rdir, "remove", [], [step("root"), step("user")])
    assert not st["cancelled"] and len(st["steps"]) == 2


def test_password_prompt_dismissed_cancels_run(fr, rdir):
    st = fr.run_queue(rdir, "remove", [], [step("root", rc=126), step("user", out="must not run")])
    assert st["cancelled"] and st["reason"] == "password prompt dismissed or not authorized"
    assert len(st["steps"]) == 1


def test_failed_step_does_not_stop_queue(fr, rdir):
    st = fr.run_queue(rdir, "remove", [], [step("root", rc=1), step("user")])
    assert [s["rc"] for s in st["steps"]] == [1, 0] and not st["cancelled"]


def test_second_run_refused_while_locked(fr, rdir):
    fd = os.open(os.path.join(rdir, "lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        st = fr.run_queue(rdir, "remove", [], [step("root")])
        assert st.get("error") == "busy"
    finally:
        os.close(fd)


def test_run_command_streams_output(fr, tmp_path):
    script = tmp_path / "fake-helper"
    script.write_text("#!/bin/bash\necho \"line $1\"\necho oops >&2\nexit 3\n")
    script.chmod(0o755)
    lines = []
    rc = fr.run_command([str(script), "a"], lines.append)
    assert rc == 3
    assert "line a" in lines and "oops" in lines

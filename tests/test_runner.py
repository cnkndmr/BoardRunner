import json
import os
import stat
import time

import pytest

from boardrunner import Card, ClaudeRunner, SetupError, State, job_settings, load_state, save_state


def fake_claude(tmp_path, body):
    path = tmp_path / "fake-claude"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def runner(tmp_path, body, **agent):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return ClaudeRunner({"workdir": str(work), "claude_path": fake_claude(tmp_path, body), **agent}, tmp_path / "state",
                        home=tmp_path)


CARD = Card(id="c-1", number="1", title="t")


def wait(run, handle, seconds=5):
    end = time.time() + seconds
    while run.status(handle) == "running" and time.time() < end:
        time.sleep(0.02)
    return run.status(handle)


def test_success_captures_output_and_runs_in_workdir(tmp_path):
    run = runner(tmp_path, "pwd; echo hello")
    handle = run.start("plan", CARD, "prompt")
    assert wait(run, handle) == "done"
    assert run.result(handle).splitlines() == [str((tmp_path / "work").resolve()), "hello"]


def test_failure_keeps_error_text(tmp_path):
    run = runner(tmp_path, "echo broken >&2; exit 3")
    handle = run.start("job", CARD, "prompt")
    assert wait(run, handle) == "failed"
    assert "broken" in run.result(handle)


def test_hostile_prompt_is_only_data(tmp_path):
    run = runner(tmp_path, 'touch "$(dirname "$0")/ran"; echo ok')
    handle = run.start("plan", CARD, '"; touch hacked; $(touch hacked) `touch hacked`')
    assert wait(run, handle) == "done"
    assert not list(tmp_path.rglob("hacked"))


def test_stop_ends_the_whole_process_group(tmp_path):
    run = runner(tmp_path, "sleep 30 & wait")
    handle = run.start("job", CARD, "prompt")
    assert run.status(handle) == "running"
    run.stop(handle)
    assert wait(run, handle) == "failed"


def test_job_runs_on_after_the_runner_object_is_gone_and_can_be_found(tmp_path):
    run = runner(tmp_path, "sleep 30")
    handle = run.start("job", CARD, "prompt")
    again = runner(tmp_path, "sleep 30")
    assert again.find_running(CARD) == handle
    assert again.find_running(Card(id="other", number="2", title="t")) is None
    again.stop(handle)
    assert wait(again, handle) == "failed"


def test_reused_pid_is_not_taken_for_the_job(tmp_path):
    run = runner(tmp_path, "sleep 30")
    handle = run.start("job", CARD, "prompt")
    meta = json.loads((tmp_path / handle / "meta.json").read_text())
    real = dict(meta)
    meta["started"] = "Mon Jan  1 00:00:00 2001"
    (tmp_path / handle / "meta.json").write_text(json.dumps(meta))
    assert run.status(handle) == "failed"
    run.stop(handle)  # must not signal the pid now: it belongs to "someone else"
    assert os.path.exists(f"/proc/{real['pid']}") or os.killpg(real["pid"], 0) is None
    os.killpg(real["pid"], 15)


def test_time_limit(tmp_path):
    run = runner(tmp_path, "sleep 30", max_minutes=0)
    handle = run.start("job", CARD, "prompt")
    time.sleep(0.05)
    assert run.status(handle) == "failed"


def test_missing_workdir_is_explained(tmp_path):
    run = ClaudeRunner({"workdir": str(tmp_path / "none"), "claude_path": "/bin/echo"}, tmp_path)
    with pytest.raises(SetupError, match="workdir"):
        run.start("job", CARD, "p")


def test_unknown_sandbox_is_refused(tmp_path):
    with pytest.raises(SetupError):
        ClaudeRunner({"sandbox": "maybe"}, tmp_path)


def test_plan_cannot_write_or_run_commands(tmp_path):
    command = runner(tmp_path, "true").command("plan", "p", tmp_path / "s", tmp_path)
    denied = command[command.index("--disallowedTools") + 1:command.index("--permission-mode")]
    assert {"Bash", "Edit", "Write"} <= set(denied)
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep"


def test_strict_sandbox_settings(tmp_path):
    settings = job_settings(tmp_path / "w", tmp_path / "j", "strict", tmp_path)
    assert settings["sandbox"]["failIfUnavailable"] and not settings["sandbox"]["allowUnsandboxedCommands"]
    assert settings["sandbox"]["network"]["allowedDomains"] == []
    assert settings["sandbox"]["filesystem"]["allowWrite"] == [str(tmp_path / "j"), str(tmp_path / "w")]
    assert str(tmp_path / ".ssh") in settings["sandbox"]["filesystem"]["denyRead"]
    assert "sandbox" not in job_settings(tmp_path, tmp_path, "off", tmp_path)


def test_state_round_trip_keeps_approval_valid(tmp_path):
    from boardrunner import Stage, view
    card = Card(id="c", number="1", title="t", stage=Stage.APPROVED)
    state = State(human_view={"c": view(card)})
    save_state(tmp_path / "s.json", state)
    assert load_state(tmp_path / "s.json").human_view["c"] == view(card)


def test_damaged_state_stops_instead_of_guessing(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{broken")
    with pytest.raises(SetupError, match="damaged"):
        load_state(path)


def test_relative_state_folder_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work").mkdir()
    run = ClaudeRunner({"workdir": "work", "claude_path": fake_claude(tmp_path, "echo $@ | grep -q settings.json && echo ok")}, type(tmp_path)("state"))
    handle = run.start("job", CARD, "p")
    assert handle.startswith("/") and wait(run, handle) == "done"

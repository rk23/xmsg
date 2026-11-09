import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "xmsg" / "cli.py"
spec = importlib.util.spec_from_file_location("xmsg", SCRIPT)
xmsg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xmsg)

LIVE = [
    {"pid": 101, "name": "Lab 31", "sessionId": "sid-lab31", "cwd": "/w/lab", "status": "busy"},
    {"pid": 102, "name": "Steward", "sessionId": "sid-stw", "cwd": "/w/pkg", "status": "idle"},
    {"name": "finished bg job", "sessionId": "sid-old"},  # no pid: not live
]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    fixture = tmp_path / "agents.json"
    fixture.write_text(json.dumps(LIVE))
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "102.json").write_text(json.dumps({"name": "Steward", "sessionId": "sid-stw", "cwd": "/w/pkg"}))
    monkeypatch.setenv("XMSG_HOME", str(tmp_path / "xmsg"))
    monkeypatch.setenv("XMSG_CLAUDE_AGENTS_JSON", str(fixture))
    monkeypatch.setenv("XMSG_CLAUDE_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("XMSG_TURN_SYNC", "1")
    monkeypatch.setenv("XMSG_CLAUDE_PROJECTS_DIR", str(tmp_path / "claude-projects"))
    monkeypatch.setenv("XMSG_CODEX_SESSIONS_DIR", str(tmp_path / "codex-sessions"))
    for var in ("CLAUDE_PID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "XMSG_NAME", "XMSG_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def run(*argv):
    rc = xmsg.main(list(argv))
    assert rc == 0, f"xmsg {' '.join(argv)} exited {rc}"


def evs(ev=None, mid=None):
    return [e for e in xmsg.events() if (ev is None or e["ev"] == ev) and (mid is None or e.get("id") == mid)]


def last_sent_id():
    return evs("sent")[-1]["id"]


def fake_bin(tmp_path, monkeypatch, name, exit_code=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / f"{name}.args.json"
    script = bindir / name
    script.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"open({str(log)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}))\n"
        f"sys.exit({exit_code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return log


def test_names_resolve_across_spelling_and_live_sessions():
    assert xmsg.resolve("lab-31")["address"] == "sid-lab31"
    assert xmsg.resolve("LAB_31")["name"] == "Lab 31"
    assert xmsg.resolve("sid-stw")["name"] == "Steward"
    with pytest.raises(xmsg.XmsgError):
        xmsg.resolve("finished bg job")


def test_claude_identity_and_registration_survive_rename(monkeypatch):
    monkeypatch.setenv("CLAUDE_PID", "102")
    assert xmsg.detect_self()["name"] == "Steward"
    run("register", "--self", "--owns", "atlas,package", "--alias", "steward-b9")
    (Path(os.environ["XMSG_CLAUDE_SESSIONS_DIR"]) / "102.json").write_text(
        json.dumps({"name": "renamed", "sessionId": "sid-stw"}))
    assert xmsg.detect_self()["name"] == "Steward"
    assert xmsg.resolve("owner:atlas")["name"] == "Steward"
    assert xmsg.resolve("steward-b9")["name"] == "Steward"


def test_nested_sessions_pick_the_closest_ancestor(monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "0e0e0e0e-6a4e")
    monkeypatch.setenv("CLAUDE_PID", "999999")  # inherited from an outer Claude that is not our ancestor
    assert xmsg.detect_self()["name"] == "codex-0e0e0e0e"
    monkeypatch.setenv("CLAUDE_PID", str(os.getppid()))  # the Claude session is our direct parent
    assert xmsg.nearest_agent() == "claude"


def test_mailbox_watch_read_receipts(capsys):
    run("send", "--to", "Steward", "--from", "astra", "--body", "The check is ready\nmore detail")
    mid = last_sent_id()
    assert xmsg.unread("Steward") == [mid]
    run("watch", "Steward", "--once")
    out = capsys.readouterr().out
    assert "XMSG 1 unread for Steward" in out and mid in out and "The check is ready" in out
    run("read", mid, "--as", "Steward")
    assert xmsg.unread("Steward") == []
    assert [e["ev"] for e in evs(mid=mid)] == ["sent", "queued", "delivered", "read"]


def test_request_ledger_states(capsys):
    run("request", "--to", "Steward", "--from", "Lab 31", "--subject", "retry budget",
        "--body", "Please add --retry", "--link", "runs/calibration/run-a")
    mid = last_sent_id()
    run("state", mid, "accepted", "--from", "Steward", "--note", "after 0.3.92")
    run("reply", mid, "--from", "Steward", "--state", "done", "--link", "v0.3.92", "--body", "Released")
    row = xmsg.ledger_rows()[0]
    assert row["state"] == "done" and row["delivery"] == "answered"
    assert row["links"] == ["runs/calibration/run-a", "v0.3.92"]
    assert xmsg.ledger_rows(open_only=True) == []
    reply = xmsg.load_message(last_sent_id())
    assert reply["to"] == "Lab 31" and reply["re"] == mid and reply["thread"] == mid




def test_codex_queue_delivery_and_failure_fallback(tmp_path, monkeypatch):
    log = fake_bin(tmp_path, monkeypatch, "codex")
    run("register", "astra", "--family", "codex", "--address", "thread-uuid-1")
    run("send", "--to", "astra", "--from", "Steward", "--wake", "--body", "Is the fix verified?")
    mid = last_sent_id()
    call = json.loads(log.read_text())
    assert call["argv"][:4] == ["queue", "--thread", "thread-uuid-1", "--message"]
    assert call["argv"][-1] == "--resume" and f"[xmsg {mid}]" in call["argv"][4]
    assert evs("delivered", mid)[0]["via"] == "codex queue --resume"
    assert xmsg.unread("astra") == []

    fake_bin(tmp_path, monkeypatch, "codex", exit_code=3)
    run("send", "--to", "astra", "--from", "Steward", "--body", "second")
    mid2 = last_sent_id()
    assert evs("failed", mid2) and xmsg.unread("astra") == [mid2]


def test_codex_sender_identity_and_reply_routing(tmp_path, monkeypatch):
    log = fake_bin(tmp_path, monkeypatch, "codex")
    monkeypatch.setenv("CODEX_THREAD_ID", "0f0f0f0f-aaaa")
    run("send", "--to", "Steward", "--body", "Bug: NaN gradient in the QC step")
    mid = last_sent_id()
    assert xmsg.load_message(mid)["from"] == "codex-0f0f0f0f"
    monkeypatch.delenv("CODEX_THREAD_ID")
    run("reply", mid, "--from", "Steward", "--body", "Fixed in 0.3.66")
    assert json.loads(log.read_text())["argv"][2] == "0f0f0f0f-aaaa"


def test_turn_delivery_returns_output_as_reply(tmp_path):
    cmd = json.dumps([sys.executable, "-c", "import sys; print('ARGON SAYS: ' + sys.argv[1].split(chr(10))[0])",
                      "{message}"])
    run("register", "argon", "--family", "agy", "--address", "conv-1", "--turn-cmd", cmd)
    run("role", "director", "argon")
    run("request", "--to", "director", "--from", "Steward", "--body", "Which arm first?")
    req = evs("sent")[0]["id"]
    reply = xmsg.load_message(evs("sent")[-1]["id"])
    assert reply["from"] == "argon" and reply["to"] == "Steward" and reply["re"] == req
    assert reply["body"].startswith(f"ARGON SAYS: [xmsg {req}]")
    assert xmsg.unread("Steward") == [reply["id"]]
    assert {e["ev"] for e in evs(mid=req)} >= {"turn-start", "turn-done", "delivered", "answered"}


def test_stop_file_and_daily_cap_hold_turns():
    cmd = json.dumps([sys.executable, "-c", "print('ok')"])
    run("register", "grok-lead", "--family", "grok", "--address", "s1", "--turn-cmd", cmd,
        "--max-turns-per-day", "1")
    run("stop")
    run("send", "--to", "grok-lead", "--from", "Steward", "--body", "one")
    assert evs("held") and not evs("turn-start")
    run("stop", "--clear")
    run("send", "--to", "grok-lead", "--from", "Steward", "--body", "two")
    run("send", "--to", "grok-lead", "--from", "Steward", "--body", "three")
    assert len(evs("turn-start")) == 1
    assert "daily turn cap" in evs("held")[-1]["note"]
    assert len(xmsg.unread("grok-lead")) == 2



def test_roles_fan_out_to_several_peers(capsys):
    for name in ("sol", "astra", "muse-lead"):
        run("register", name, "--family", "mailbox")
    run("role", "review", "sol", "astra")
    run("send", "--to", "review,astra", "--from", "Steward", "--body", "Review the 0.3.92 diff")
    assert sorted(m["to"] for m in map(xmsg.load_message, [e["id"] for e in evs("sent")])) == ["astra", "sol"]
    with pytest.raises(xmsg.XmsgError, match="only send fans out"):
        xmsg.resolve("review")
    assert xmsg.main(["role", "sol", "astra"]) == 2  # a role may not shadow a peer name
    run("unregister", "astra")
    run("role", "review")
    assert "review -> sol" in capsys.readouterr().out


def test_model_attribution_from_transcripts_registry_and_flag(tmp_path, monkeypatch):
    proj = tmp_path / "claude-projects" / "-w-lab"
    proj.mkdir(parents=True)
    (proj / "sid-lab31.jsonl").write_text(
        json.dumps({"message": {"model": "claude-opus-5-5"}}) + "\n"
        + json.dumps({"message": {"model": "claude-fable-5-1"}}) + "\n")  # switched mid-session
    rollout = tmp_path / "codex-sessions" / "2026" / "11" / "08"
    rollout.mkdir(parents=True)
    (rollout / "rollout-2026-11-08T14-00-02-tid-sol.jsonl").write_text(
        json.dumps({"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}) + "\n")
    run("register", "argon", "--family", "mailbox", "--model", "gemini-4-argon-high")
    run("role", "director", "argon")

    run("send", "--to", "director", "--from", "Lab 31", "--body", "band 0 result")
    assert xmsg.load_message(last_sent_id())["model"] == "claude-fable-5-1"
    monkeypatch.setenv("CODEX_THREAD_ID", "tid-sol")
    run("send", "--to", "director", "--body", "cache defect reproducer")
    assert xmsg.load_message(last_sent_id())["model"] == "gpt-6.1-sol"
    monkeypatch.delenv("CODEX_THREAD_ID")
    run("send", "--to", "Steward", "--from", "director", "--body", "next: widen the band")
    msg = xmsg.load_message(last_sent_id())
    assert msg["model"] == "gemini-4-argon-high" and "from argon (gemini-4-argon-high)" in xmsg.render(msg)
    run("send", "--to", "Steward", "--from", "Lab 31", "--model", "claude-opus-5-5", "--body", "override")
    assert xmsg.load_message(last_sent_id())["model"] == "claude-opus-5-5"



def test_turn_reply_records_the_peer_model(monkeypatch):
    monkeypatch.setenv("XMSG_MODEL", "claude-opus-5-5")  # the sender's model must not leak into the reply
    cmd = json.dumps([sys.executable, "-c", "print('ok')", "--model", "gemini-4-argon-high"])
    run("register", "argon", "--family", "agy", "--address", "c1", "--turn-cmd", cmd)
    run("send", "--to", "argon", "--from", "Steward", "--body", "hi")
    sent = [xmsg.load_message(e["id"]) for e in evs("sent")]
    assert [m["model"] for m in sent] == ["claude-opus-5-5", "gemini-4-argon-high"]


def test_agy_registration_finds_the_seeded_conversation(tmp_path, monkeypatch):
    seeded = tmp_path / "director"
    seeded.mkdir()
    table = tmp_path / "last_conversations.json"
    table.write_text(json.dumps({str(seeded.resolve()): "conv-seeded", "/elsewhere": "conv-other"}))
    monkeypatch.setenv("XMSG_AGY_LAST_CONVERSATIONS", str(table))
    run("register", "argon", "--family", "agy", "--cwd", str(seeded), "--model", "gemini-4-argon-high")
    peer = xmsg.load_registry()["peers"]["argon"]
    assert peer["address"] == "conv-seeded" and peer["cwd"] == str(seeded)
    assert peer["turn_cmd"] == ["agy", "--model", "gemini-4-argon-high", "--conversation", "{address}",
                                "--print", "{message}"]


def test_wait_runs_the_turn_inline_and_prints_the_reply(capsys, monkeypatch):
    monkeypatch.delenv("XMSG_TURN_SYNC")
    cmd = json.dumps([sys.executable, "-c", "print('Direction: run the control first.')"])
    run("register", "argon", "--family", "agy", "--address", "conv-1", "--turn-cmd", cmd,
        "--model", "gemini-4-argon-high")
    run("role", "director", "argon")
    capsys.readouterr()
    run("request", "--to", "director", "--from", "Steward", "--wait", "--body", "0.3.91 results; what next?")
    out = capsys.readouterr().out
    assert "=== reply" in out and "from argon (gemini-4-argon-high)" in out
    assert "Direction: run the control first." in out
    assert xmsg.unread("Steward") == []  # printed replies count as read
    assert xmsg.ledger_rows()[0]["delivery"] == "answered"


def test_wait_times_out_for_a_silent_peer(capsys):
    run("register", "quiet", "--family", "mailbox")
    rc = xmsg.main(["send", "--to", "quiet", "--from", "Steward", "--wait", "--timeout", "0.2", "--body", "hi"])
    assert rc == 3 and "no reply from quiet" in capsys.readouterr().out


def test_log_bodies_shows_full_text(capsys):
    run("send", "--to", "Steward", "--from", "argon", "--body", "Direction: option (b).\nGate: 12 blocks.")
    capsys.readouterr()
    run("log", "--bodies")
    out = capsys.readouterr().out
    assert "    Direction: option (b).\n    Gate: 12 blocks." in out


def test_watch_next_exits_on_new_mail_and_ignores_announced_backlog(capsys):
    run("send", "--to", "Steward", "--from", "argon", "--body", "Direction: try (b)")
    first = last_sent_id()
    capsys.readouterr()
    run("watch", "Steward", "--next")
    assert f"XMSG 1 new for Steward: {first}" in capsys.readouterr().out
    run("watch", "Steward", "--next", "--max-wait", "0.3", "--interval", "0.1")  # unread, but announced
    assert "nothing new" in capsys.readouterr().out
    run("send", "--to", "Steward", "--from", "astra", "--body", "Scaling check")
    run("watch", "Steward", "--next")
    out = capsys.readouterr().out
    assert f"XMSG 1 new for Steward: {last_sent_id()}" in out and f"still unread: {first}" in out


def test_recipient_owns_request_status(capsys):
    run("register", "argon", "--family", "mailbox")
    run("request", "--to", "argon", "--from", "Steward", "--body", "Which route?")
    mid = last_sent_id()
    assert xmsg.main(["state", mid, "done", "--from", "Steward"]) == 2  # the requester can't close it
    run("state", mid, "done", "--from", "argon")
    run("note", mid, "accepted (b); M1 running", "--from", "Steward", "--link", "runs/m1")
    row = xmsg.ledger_rows()[0]
    assert row["state"] == "done" and row["note"] == "Steward: accepted (b); M1 running"
    assert "runs/m1" in row["links"]
    run("request", "--to", "argon", "--from", "Steward", "--body", "Never mind")
    run("state", last_sent_id(), "superseded", "--from", "Steward")  # withdrawing is the requester's call


def test_install_skill_copies_and_respects_existing(tmp_path, capsys):
    dest = tmp_path / "skills" / "xmsg"
    run("install-skill", "--dest", str(dest))
    assert (dest / "SKILL.md").read_text().startswith("---\nname: xmsg")
    run("install-skill", "--dest", str(dest))
    assert "skipped" in capsys.readouterr().out
    (dest / "SKILL.md").write_text("old")
    run("install-skill", "--dest", str(dest), "--force")
    assert (dest / "SKILL.md").read_text().startswith("---\nname: xmsg")

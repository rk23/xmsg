#!/usr/bin/env python3
"""xmsg: a shared mailbox, request ledger and router for agent sessions of any model family.

Claude Code sessions message each other with SendMessage. xmsg covers everything that cannot:
Codex, agy (Argon), Grok and Muse sessions, roles such as "director" that any model can fill,
routing by ownership, read receipts, and a request ledger that outlives every session.

Delivery uses each family's own transport and falls back to a durable mailbox:

  claude      mailbox; the session runs `xmsg watch` under its Monitor tool (no shell can inject
              into a Claude session). Claude senders may also SendMessage the rendered text.
  codex       `codex queue --thread ADDRESS` (`--wake` adds `--resume`)
  muse        `muse session-message send --target ADDRESS`
  agy, grok,  one headless turn on a saved conversation per message; the turn's output comes back
  codex-exec  to the sender as the reply (serialized per peer, daily cap, STOP file)
  mailbox     plain mailbox, read with `xmsg inbox` / `xmsg read`

`send --wait` / `request --wait` blocks until the replies arrive and prints them, so one command
(run in the background by a Claude session) asks a headless peer and returns its answer.

State lives in $XMSG_HOME (default ~/.xmsg): peers.json, messages/<id>.json (immutable),
events.jsonl (append-only), inbox/<peer>/<id> markers, watchers/, turns/, STOP.

Peer messages are advisory. They never carry the user's approval for spend, launches, pins,
pushes or publication; each session keeps its own user's gates.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

__version__ = "0.1.0"

FAMILIES = {
    "claude": "mailbox",
    "codex": "codex-queue",
    "muse": "muse",
    "agy": "turn",
    "grok": "turn",
    "codex-exec": "turn",
    "mailbox": "mailbox",
}
# Starting points only: verify a template with one manual turn before relying on it.
TURN_PRESETS = {
    "agy": ["agy", "--conversation", "{address}", "--print", "{message}"],
    "grok": ["grok", "--resume", "{address}", "--single", "{message}"],
    "codex-exec": ["codex", "exec", "resume", "{address}", "{message}"],
}
KINDS = ("message", "request", "reply", "receipt")
STATES = ("open", "accepted", "running", "blocked", "done", "rejected", "superseded")
CLOSED = {"done", "rejected", "superseded"}
WATCHER_STALE_S = 90


class XmsgError(Exception):
    pass


# ---------------------------------------------------------------- basics

def home() -> Path:
    return Path(os.environ.get("XMSG_HOME") or Path.home() / ".xmsg").expanduser()


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None = None) -> str:
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(name: str):
    path = home() / "locks" / f"{slug(name)}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def emit(ev: str, mid: str | None = None, **fields) -> None:
    rec = {"ts": iso(), "ev": ev}
    if mid:
        rec["id"] = mid
    rec.update({k: v for k, v in fields.items() if v not in (None, "", [], False)})
    with locked("events"), open(home() / "events.jsonl", "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def events() -> list[dict]:
    path = home() / "events.jsonl"
    if not path.exists():
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            with contextlib.suppress(json.JSONDecodeError):
                out.append(json.loads(line))
    return out


def events_by_id() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for e in events():
        if e.get("id"):
            grouped[e["id"]].append(e)
    return grouped


# ---------------------------------------------------------------- registry and identity

def load_registry() -> dict:
    path = home() / "peers.json"
    reg = json.loads(path.read_text()) if path.exists() else {}
    reg.setdefault("peers", {})
    reg.setdefault("roles", {})
    return reg


def save_registry(reg: dict) -> None:
    atomic_write(home() / "peers.json", json.dumps(reg, indent=2, sort_keys=True, ensure_ascii=False))


def claude_sessions() -> list[dict]:
    """Live Claude Code sessions from `claude agents --json` (cached 15 s)."""
    fixture = os.environ.get("XMSG_CLAUDE_AGENTS_JSON")
    if fixture:
        data = json.loads(Path(fixture).read_text())
    else:
        if not shutil.which("claude"):
            return []
        cache = home() / "cache" / "claude_agents.json"
        data = None
        with contextlib.suppress(OSError, json.JSONDecodeError):
            if time.time() - cache.stat().st_mtime < 15:
                data = json.loads(cache.read_text())
        if data is None:
            try:
                out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True,
                                     timeout=30, check=False).stdout
                data = json.loads(out)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return []
            atomic_write(cache, json.dumps(data))
    return [s for s in data if s.get("pid") and s.get("name")]


def claude_sessions_dir() -> Path:
    return Path(os.environ.get("XMSG_CLAUDE_SESSIONS_DIR") or Path.home() / ".claude" / "sessions")


def detect_self() -> dict:
    """Who is running this command: XMSG_NAME, else the Claude or Codex session, else the user."""
    reg = load_registry()
    if os.environ.get("XMSG_NAME"):
        with contextlib.suppress(XmsgError):
            return resolve(os.environ["XMSG_NAME"], reg, live=False)
        return {"name": os.environ["XMSG_NAME"], "family": "mailbox"}
    agent = nearest_agent()
    if agent == "claude":
        pid = os.environ["CLAUDE_PID"]
        info = {}
        with contextlib.suppress(OSError, json.JSONDecodeError):
            info = json.loads((claude_sessions_dir() / f"{pid}.json").read_text())
        sid = info.get("sessionId") or os.environ.get("CLAUDE_CODE_SESSION_ID")
        for p in reg["peers"].values():
            if sid and p.get("address") == sid:
                return p
        if info.get("name") or sid:
            return {"name": info.get("name") or f"claude-{sid[:8]}", "family": "claude", "address": sid,
                    "cwd": info.get("cwd")}
    if agent == "codex":
        tid = os.environ["CODEX_THREAD_ID"]
        for p in reg["peers"].values():
            if p.get("address") == tid:
                return p
        return {"name": f"codex-{tid[:8]}", "family": "codex", "address": tid}
    return {"name": "user", "family": "human"}


def nearest_agent() -> str | None:
    """'claude' or 'codex': the session that runs this command.

    Both variables are inherited by children, so a Codex run started from a Claude session sees
    CLAUDE_PID too (and the reverse). When both are set, the closest ancestor process decides.
    """
    claude_pid = os.environ.get("CLAUDE_PID", "")
    has_codex = bool(os.environ.get("CODEX_THREAD_ID"))
    if not claude_pid or not has_codex:
        return "claude" if claude_pid else "codex" if has_codex else None
    pid = os.getppid()
    for _ in range(40):
        if str(pid) == claude_pid:
            return "claude"
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                                 check=False).stdout.split(None, 1)
        except OSError:
            break
        if len(out) < 2:
            break
        if os.path.basename(out[1].strip()).lower().startswith("codex"):
            return "codex"
        pid = int(out[0])
        if pid <= 1:
            break
    return "codex"  # undecidable: Claude spawning Codex is the common nesting


def resolve(target: str, reg: dict | None = None, live: bool = True, _depth: int = 0) -> dict:
    """Peer for a name, alias, role:ROLE, owner:TOPIC or live Claude session name/id."""
    reg = reg or load_registry()
    t = target.strip()
    if _depth > 4:
        raise XmsgError(f"role loop resolving {target!r}")
    if t.lower().startswith("owner:"):
        topic = slug(t[6:])
        hits = [p for p in reg["peers"].values() if topic in {slug(o) for o in p.get("owns", [])}]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise XmsgError(f"nobody owns {topic!r}; register an owner with `xmsg register NAME --owns {topic}`")
        raise XmsgError(f"{topic!r} has several owners: {', '.join(p['name'] for p in hits)}")
    if t.lower().startswith("role:"):
        role = slug(t[5:])
        if role not in reg["roles"]:
            raise XmsgError(f"no role {role!r}; set one with `xmsg role {role} PEER`")
        return resolve(single_role_peer(role, reg), reg, live, _depth + 1)
    s = slug(t)
    for p in reg["peers"].values():
        if s == slug(p["name"]) or s in {slug(a) for a in p.get("aliases", [])} or t == p.get("address"):
            return p
    if s in reg["roles"]:
        return resolve(single_role_peer(s, reg), reg, live, _depth + 1)
    if live:
        for c in claude_sessions():
            if s == slug(c["name"]) or t in (c.get("sessionId"), c.get("id")):
                return {"name": c["name"], "family": "claude", "address": c.get("sessionId"),
                        "cwd": c.get("cwd"), "live": True}
    known = sorted({p["name"] for p in reg["peers"].values()} | set(reg["roles"]))
    raise XmsgError(f"no peer, role or live Claude session named {target!r}"
                    + (f" (known: {', '.join(known)})" if known else ""))


def role_peers(role: str, reg: dict) -> list[str]:
    value = reg["roles"].get(slug(role), [])
    return [value] if isinstance(value, str) else list(value)


def single_role_peer(role: str, reg: dict) -> str:
    names = role_peers(role, reg)
    if len(names) != 1:
        raise XmsgError(f"role {role!r} has {len(names)} peers ({', '.join(names)}); only send fans out to all")
    return names[0]


def resolve_all(target: str, reg: dict | None = None) -> list[dict]:
    """Like resolve(), but a role with several peers (e.g. review -> sol, astra) yields all of them."""
    reg = reg or load_registry()
    t = target.strip()
    role = slug(t[5:]) if t.lower().startswith("role:") else slug(t)
    is_peer = any(role == slug(p["name"]) or role in {slug(a) for a in p.get("aliases", [])}
                  for p in reg["peers"].values())
    if role in reg["roles"] and (t.lower().startswith("role:") or not is_peer):
        return [resolve(name, reg) for name in role_peers(role, reg)]
    return [resolve(t, reg)]


def tail_lines(path: Path, nbytes: int = 400_000) -> list[str]:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - nbytes))
        return fh.read().decode("utf-8", errors="replace").splitlines()


def detect_model(me: dict) -> str | None:
    """The sender's current model: XMSG_MODEL, else the session's own transcript tail, else the registry."""
    if os.environ.get("XMSG_MODEL"):
        return os.environ["XMSG_MODEL"]
    with contextlib.suppress(OSError, ValueError, IndexError):
        family, address = me.get("family"), me.get("address")
        paths: list[Path] = []
        if family == "claude" and address:
            root = Path(os.environ.get("XMSG_CLAUDE_PROJECTS_DIR") or Path.home() / ".claude" / "projects")
            paths = sorted(root.glob(f"*/{address}.jsonl"))
        elif family == "codex" and address:
            root = Path(os.environ.get("XMSG_CODEX_SESSIONS_DIR") or Path.home() / ".codex" / "sessions")
            paths = sorted(root.glob(f"*/*/*/rollout-*{address}.jsonl"))
        if paths:
            for line in reversed(tail_lines(paths[-1])):
                m = re.search(r'"model"\s*:\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    return me.get("model")


def live_claude_name(peer: dict) -> str:
    for c in claude_sessions():
        if peer.get("address") and c.get("sessionId") == peer["address"]:
            return c["name"]
    return peer["name"]


def peer_delivery(peer: dict) -> str:
    return peer.get("delivery") or FAMILIES.get(peer.get("family", ""), "mailbox")


# ---------------------------------------------------------------- messages

def messages_dir() -> Path:
    return home() / "messages"


def new_id() -> str:
    while True:
        mid = now().strftime("%y%m%d-%H%M%S-") + secrets.token_hex(2)
        if not (messages_dir() / f"{mid}.json").exists():
            return mid


def save_message(msg: dict) -> None:
    atomic_write(messages_dir() / f"{msg['id']}.json", json.dumps(msg, indent=2, ensure_ascii=False))


def load_message(mid: str) -> dict:
    return json.loads((messages_dir() / f"{mid}.json").read_text())


def all_messages() -> list[dict]:
    if not messages_dir().exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(messages_dir().glob("*.json"))]


def resolve_id(ref: str) -> str:
    ref = ref.strip().strip("[]")
    ids = [p.stem for p in messages_dir().glob("*.json")] if messages_dir().exists() else []
    if ref in ids:
        return ref
    hits = [i for i in ids if i.startswith(ref) or i.endswith("-" + ref)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise XmsgError(f"no message {ref!r}")
    raise XmsgError(f"{ref!r} matches {len(hits)} messages; give more of the id")


def inbox_dir(name: str) -> Path:
    return home() / "inbox" / slug(name)


def unread(name: str) -> list[str]:
    d = inbox_dir(name)
    return sorted(p.name for p in d.iterdir() if not p.name.startswith(".")) if d.exists() else []


def put_marker(name: str, mid: str) -> None:
    d = inbox_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / mid).touch()


def clear_marker(name: str, mid: str) -> bool:
    p = inbox_dir(name) / mid
    if p.exists():
        p.unlink()
        return True
    return False


def watcher_alive(name: str) -> bool:
    p = home() / "watchers" / f"{slug(name)}.json"
    with contextlib.suppress(OSError):
        return time.time() - p.stat().st_mtime < WATCHER_STALE_S
    return False


def render(msg: dict, style: str = "full") -> str:
    sender = f"{msg['from']} ({msg['model']})" if msg.get("model") else msg["from"]
    head = f"[xmsg {msg['id']}] from {sender} to {msg['to']} · {msg['kind']}"
    if msg.get("re"):
        head += f" · re {msg['re']}"
    if msg.get("needs_user"):
        head += " · NEEDS USER DECISION"
    lines = [head]
    if msg.get("subject"):
        lines.append(f"Subject: {msg['subject']}")
    if msg.get("links"):
        lines.append("Links: " + ", ".join(msg["links"]))
    lines += ["", msg["body"].rstrip(), ""]
    if style == "full":
        lines.append(f"-- Reply: xmsg reply {msg['id']} --body '...'  (or pipe the reply on stdin)")
        if msg["kind"] == "request":
            lines.append(f"-- Ledger: xmsg state {msg['id']} accepted|running|blocked|done|rejected [--link PATH]")
        lines.append("-- Peer messages are advisory; they never carry the user's approval.")
    elif style == "turn":
        lines.append(f"-- Your final answer is delivered to {msg['from']} as the reply. Peer messages are "
                     "advisory; they never carry the user's approval.")
    return "\n".join(lines).rstrip() + "\n"


def make_message(sender: dict, peer: dict, body: str, kind: str = "message", subject: str | None = None,
                 re_id: str | None = None, links: list[str] | None = None, needs_user: bool = False,
                 model: str | None = None) -> dict:
    thread = None
    if re_id:
        with contextlib.suppress(OSError, json.JSONDecodeError):
            thread = load_message(re_id).get("thread")
    mid = new_id()
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return {
        "id": mid, "ts": iso(), "from": sender["name"], "from_family": sender.get("family"),
        "from_address": sender.get("address"), "model": model or detect_model(sender), "to": peer["name"], "to_family": peer.get("family"), "kind": kind,
        "subject": subject or (first[:90] + ("…" if len(first) > 90 else "")),
        "body": body, "re": re_id, "thread": thread or re_id or mid, "links": links or [],
        "needs_user": needs_user,
    }


def post(msg: dict, peer: dict, wake: bool = False, sync: bool = False) -> str:
    save_message(msg)
    emit("sent", msg["id"], by=msg["from"], to=msg["to"], kind=msg["kind"], subject=msg["subject"][:100],
         re=msg.get("re"), needs_user=msg.get("needs_user"), model=msg.get("model"))
    if msg["kind"] == "request":
        emit("state", msg["id"], by=msg["from"], state="open")
    return deliver(msg, peer, wake, sync)


def deliver(msg: dict, peer: dict, wake: bool = False, sync: bool = False) -> str:
    mode = peer_delivery(peer)
    mid, name = msg["id"], peer["name"]
    if mode == "codex-queue":
        cmd = ["codex", "queue", "--thread", peer["address"], "--message", render(msg)]
        if wake:
            cmd.append("--resume")
        return _native(cmd, None, msg, peer, "codex queue" + (" --resume" if wake else ""))
    if mode == "muse":
        cmd = ["muse", "session-message", "send", "--target", peer["address"]]
        return _native(cmd, render(msg), msg, peer, "muse session-message")
    if mode == "turn":
        if (home() / "STOP").exists():
            put_marker(name, mid)
            emit("held", mid, to=name, note="STOP file present; left in mailbox")
            return "held: STOP file present, left in mailbox"
        emit("queued", mid, to=name, via="turn")
        spawn_turn(mid, sync)
        return ("headless turn finished" if sync
                else "queued for a headless turn; the output comes back as the reply")
    put_marker(name, mid)
    emit("queued", mid, to=name, via="mailbox")
    if watcher_alive(name):
        return "in mailbox; its watcher is live"
    return f"in mailbox; no live watcher for {name}, so it waits until that session runs `xmsg watch`"


def _native(cmd: list[str], stdin: str | None, msg: dict, peer: dict, via: str) -> str:
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=120,
                           check=False)
        ok, detail = r.returncode == 0, (r.stderr or r.stdout).strip()[-300:]
    except (OSError, subprocess.SubprocessError) as exc:
        ok, detail = False, str(exc)
    if ok:
        emit("delivered", msg["id"], to=peer["name"], via=via)
        return f"handed to {via} (queued in the session; not yet read)"
    put_marker(peer["name"], msg["id"])
    emit("failed", msg["id"], to=peer["name"], via=via, note=detail)
    return f"{via} failed ({detail or 'no output'}); left in mailbox"


def spawn_turn(mid: str, sync: bool = False) -> None:
    if sync or os.environ.get("XMSG_TURN_SYNC"):
        run_turn(mid)
        return
    logdir = home() / "turns"
    logdir.mkdir(parents=True, exist_ok=True)
    with open(logdir / f"{mid}.runner.log", "a") as log:
        env = {k: v for k, v in os.environ.items() if k != "XMSG_MODEL"}  # the sender's model is not the peer's
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "_turn", mid], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True, env={**env, "XMSG_HOME": str(home())})


def run_turn(mid: str) -> None:
    msg = load_message(mid)
    peer = resolve(msg["to"], live=False)
    with locked(f"turn-{peer['name']}"):
        if (home() / "STOP").exists():
            put_marker(peer["name"], mid)
            emit("held", mid, to=peer["name"], note="STOP file present; left in mailbox")
            return
        today = now().strftime("%Y-%m-%d")
        used = sum(1 for e in events() if e["ev"] == "turn-start" and e.get("by") == peer["name"]
                   and e["ts"].startswith(today))
        cap = int(peer.get("max_turns_per_day", 24))
        if used >= cap:
            put_marker(peer["name"], mid)
            emit("held", mid, to=peer["name"], note=f"daily turn cap {cap} reached; left in mailbox")
            return
        text = render(msg, style="turn")
        argv = [a.replace("{address}", peer.get("address") or "").replace("{message}", text)
                for a in peer["turn_cmd"]]
        emit("turn-start", mid, by=peer["name"])
        logdir = home() / "turns"
        logdir.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(argv, cwd=peer.get("cwd") or None, capture_output=True, text=True,
                               timeout=int(peer.get("turn_timeout_s", 3600)), check=False)
            rc, out, err = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as exc:
            rc, out, err = -1, exc.stdout or "", f"timeout after {exc.timeout} s"
        except OSError as exc:
            rc, out, err = -1, "", str(exc)
        (logdir / f"{mid}.out").write_text(out if isinstance(out, str) else out.decode(errors="replace"))
        (logdir / f"{mid}.err").write_text(err if isinstance(err, str) else err.decode(errors="replace"))
        emit("turn-done", mid, by=peer["name"], note=f"exit {rc}")
        if rc != 0 or not str(out).strip():
            emit("failed", mid, to=peer["name"], via="turn", note=(str(err).strip() or f"exit {rc}")[-300:])
            return
        emit("delivered", mid, to=peer["name"], via="turn")
        emit("read", mid, by=peer["name"])
    cmd = peer["turn_cmd"]
    model = peer.get("model") or next((cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a in ("--model", "-m")),
                                      peer["family"])
    reply_to_message(msg, peer, str(out).strip(), kind="reply", model=model)


def reply_to_message(orig: dict, sender: dict, body: str, kind: str = "reply", state: str | None = None,
                     links: list[str] | None = None, wake: bool = False,
                     model: str | None = None) -> tuple[dict, str]:
    if orig.get("from_family") == "human" or orig["from"] == "user":
        target = {"name": orig["from"], "family": "mailbox"}
    else:
        try:
            target = resolve(orig["from"])
        except XmsgError:
            # An unregistered sender: reach it the way it reached us, if we know its address.
            if not orig.get("from_address") or orig.get("from_family") not in FAMILIES:
                raise
            target = {"name": orig["from"], "family": orig["from_family"], "address": orig["from_address"]}
    msg = make_message(sender, target, body, kind=kind, re_id=orig["id"], links=links, model=model)
    if not msg["subject"].lower().startswith("re:"):
        msg["subject"] = f"re: {orig.get('subject', '')}"[:100]
    note = post(msg, target, wake)
    if clear_marker(sender["name"], orig["id"]):
        emit("read", orig["id"], by=sender["name"])
    emit("answered", orig["id"], by=sender["name"], reply=msg["id"])
    if state:
        check_state_owner(orig, sender["name"], state)
        emit("state", orig["id"], by=sender["name"], state=state, links=links)
    return msg, note


# ---------------------------------------------------------------- ledger

DELIVERY_RANK = {"sent": 0, "failed": 1, "held": 1, "queued": 2, "delivered": 3, "read": 4, "answered": 5}


def message_status(mid: str, evs: list[dict]) -> str:
    """Furthest delivery stage reached: sent < failed/held < queued < delivered < read < answered."""
    status = "sent"
    for e in evs:
        if e["ev"] in DELIVERY_RANK and DELIVERY_RANK[e["ev"]] >= DELIVERY_RANK[status]:
            status = e["ev"]
    return status


def ledger_rows(open_only: bool = False, who: str | None = None) -> list[dict]:
    grouped = events_by_id()
    rows = []
    for m in all_messages():
        if m["kind"] != "request":
            continue
        evs = grouped.get(m["id"], [])
        states = [e for e in evs if e["ev"] == "state"]
        state = states[-1]["state"] if states else "open"
        if open_only and state in CLOSED:
            continue
        if who and slug(who) not in (slug(m["from"]), slug(m["to"])):
            continue
        links = list(m.get("links", []))
        notes = []
        for e in evs:
            if e["ev"] in ("state", "note"):
                links += [x for x in e.get("links", []) if x not in links]
                if e.get("note"):
                    notes.append(f"{e.get('by')}: {e['note']}")
        rows.append({"id": m["id"], "from": m["from"], "to": m["to"], "subject": m["subject"], "state": state,
                     "delivery": message_status(m["id"], evs), "opened": m["ts"],
                     "updated": evs[-1]["ts"] if evs else m["ts"], "links": links,
                     "note": notes[-1] if notes else None, "needs_user": m.get("needs_user", False)})
    return rows


# ---------------------------------------------------------------- commands

def read_body(a) -> str:
    if getattr(a, "body", None):
        return a.body
    if getattr(a, "file", None):
        return Path(a.file).read_text()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise XmsgError("no message body: pass --body, --file or pipe it on stdin")


def sender_of(a) -> dict:
    if getattr(a, "sender", None):
        with contextlib.suppress(XmsgError):
            return resolve(a.sender)
        return {"name": a.sender, "family": "mailbox"}
    return detect_self()


def cmd_whoami(a) -> int:
    me = detect_self()
    reg = load_registry()
    registered = slug(me["name"]) in reg["peers"]
    roles = [r for r in reg["roles"] if slug(me["name"]) in {slug(p) for p in role_peers(r, reg)}]
    model = detect_model(me)
    print(f"{me['name']} ({me.get('family')}{', ' + model if model else ''}"
          f"{', ' + me['address'] if me.get('address') else ''})"
          f"{'' if registered else ' — not registered'}{' · roles: ' + ', '.join(roles) if roles else ''}")
    n = len(unread(me["name"]))
    if n:
        print(f"{n} unread: xmsg inbox")
    return 0


def cmd_register(a) -> int:
    me = detect_self() if a.self else {}
    name = a.name or me.get("name")
    family = a.family or me.get("family")
    if not name or not family:
        raise XmsgError("give NAME and --family, or --self inside a Claude or Codex session")
    if family not in FAMILIES:
        raise XmsgError(f"unknown family {family!r}; one of {', '.join(FAMILIES)}")
    address = a.address or me.get("address")
    cwd = a.cwd or me.get("cwd")
    if not address and family == "agy":
        cwd = cwd or os.getcwd()
        address = agy_last_conversation(cwd)
        if address:
            print(f"using agy's latest conversation in {cwd}: {address}")
    delivery = a.delivery or FAMILIES[family]
    if delivery in ("codex-queue", "muse", "turn") and not address:
        raise XmsgError(f"family {family} needs --address (session/conversation id or exact name)")
    with locked("registry"):
        reg = load_registry()
        key = slug(name)
        peer = reg["peers"].get(key, {})
        peer.update({"name": name, "family": family, "delivery": delivery, "updated": iso()})
        peer.setdefault("registered", iso())
        if address:
            peer["address"] = address
        if cwd:
            peer["cwd"] = str(Path(cwd).expanduser())
        if a.owns is not None:
            peer["owns"] = [slug(t) for t in a.owns.split(",") if t.strip()]
        if a.alias:
            peer["aliases"] = sorted(set(peer.get("aliases", [])) | set(a.alias))
        if a.note:
            peer["note"] = a.note
        if a.model:
            peer["model"] = a.model
        if delivery == "turn":
            if a.turn_cmd:
                peer["turn_cmd"] = json.loads(a.turn_cmd)
            elif "turn_cmd" not in peer:
                if family not in TURN_PRESETS:
                    raise XmsgError("turn delivery needs --turn-cmd '[\"cmd\", \"{address}\", \"{message}\"]'")
                peer["turn_cmd"] = preset_cmd(family, peer.get("model"))
            if a.max_turns_per_day:
                peer["max_turns_per_day"] = a.max_turns_per_day
        reg["peers"][key] = peer
        save_registry(reg)
    emit("register", by=detect_self()["name"], to=name, note=f"{family}/{delivery}")
    print(f"registered {name}: {family} via {delivery}"
          + (f", owns {', '.join(peer['owns'])}" if peer.get("owns") else ""))
    if delivery == "turn":
        print(f"  turn command: {json.dumps(peer['turn_cmd'])}  (verify it with one manual turn first)")
    return 0


def preset_cmd(family: str, model: str | None) -> list[str]:
    cmd = list(TURN_PRESETS[family])
    if model and family in ("agy", "grok"):
        cmd[1:1] = ["--model", model]
    elif model and family == "codex-exec":
        cmd[3:3] = ["-c", f'model="{model}"']
    return cmd


def agy_last_conversation(cwd: str) -> str | None:
    """The conversation agy last used in CWD (agy keeps a cwd -> conversation map)."""
    path = Path(os.environ.get("XMSG_AGY_LAST_CONVERSATIONS")
                or Path.home() / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json")
    with contextlib.suppress(OSError, json.JSONDecodeError):
        table = json.loads(path.read_text())
        for key in (str(Path(cwd).expanduser().resolve()), str(Path(cwd).expanduser()), cwd):
            if table.get(key):
                return table[key]
    return None


def cmd_unregister(a) -> int:
    with locked("registry"):
        reg = load_registry()
        if reg["peers"].pop(slug(a.name), None) is None:
            raise XmsgError(f"{a.name!r} is not registered")
        roles = {r: [p for p in role_peers(r, reg) if slug(p) != slug(a.name)] for r in reg["roles"]}
        reg["roles"] = {r: ps for r, ps in roles.items() if ps}
        save_registry(reg)
    emit("unregister", by=detect_self()["name"], to=a.name)
    print(f"unregistered {a.name}")
    return 0


def cmd_role(a) -> int:
    reg = load_registry()
    if not a.peers:
        for role in sorted(reg["roles"]):
            if a.role and slug(a.role) != role:
                continue
            print(f"{role} -> {', '.join(role_peers(role, reg))}")
        return 0
    role = slug(a.role)
    if role in {slug(p["name"]) for p in reg["peers"].values()}:
        raise XmsgError(f"{role!r} is already a peer name; pick another role name")
    names = [resolve(p, reg)["name"] for p in a.peers]
    with locked("registry"):
        reg = load_registry()
        old = role_peers(role, reg)
        reg["roles"][role] = names[0] if len(names) == 1 else names
        save_registry(reg)
    emit("role", by=detect_self()["name"], note=f"{role}: {', '.join(old) or '-'} -> {', '.join(names)}")
    print(f"{role} -> {', '.join(names)}" + (f" (was {', '.join(old)})" if old and old != names else ""))
    return 0


def cmd_peers(a) -> int:
    reg = load_registry()
    live = claude_sessions()
    roles_of = defaultdict(list)
    for r in reg["roles"]:
        for p in role_peers(r, reg):
            roles_of[slug(p)].append(r)
    rows = []
    seen_addr = set()
    for key, p in sorted(reg["peers"].items()):
        seen_addr.add(p.get("address"))
        rows.append({"name": p["name"], "family": p["family"], "via": peer_delivery(p),
                     "owns": ",".join(p.get("owns", [])), "roles": ",".join(roles_of.get(key, [])),
                     "unread": len(unread(p["name"])),
                     "watch": "live" if watcher_alive(p["name"]) else "-"})
    for c in live:
        if c.get("sessionId") in seen_addr:
            continue
        rows.append({"name": c["name"], "family": "claude", "via": "mailbox", "owns": "",
                     "roles": ",".join(roles_of.get(slug(c["name"]), [])), "unread": len(unread(c["name"])),
                     "watch": "live" if watcher_alive(c["name"]) else "-", "status": c.get("status")})
    if a.json:
        print(json.dumps({"peers": rows, "roles": reg["roles"]}, indent=2))
        return 0
    print(f"{'NAME':22} {'FAMILY':10} {'VIA':12} {'WATCH':5} {'UNREAD':6} {'ROLES':12} OWNS")
    for r in rows:
        print(f"{r['name'][:22]:22} {r['family']:10} {r['via']:12} {r['watch']:5} {r['unread']:<6} "
              f"{r['roles'][:12]:12} {r['owns']}")
    return 0


def cmd_send(a, kind: str | None = None) -> int:
    me = sender_of(a)
    body = read_body(a)
    targets = [t.strip() for t in a.to.split(",") if t.strip()]
    if not targets:
        raise XmsgError("--to is empty")
    re_id = resolve_id(a.re) if a.re else None
    reg = load_registry()
    peers = list({slug(p["name"]): p for t in targets for p in resolve_all(t, reg)}.values())
    sent: list[dict] = []
    for peer in peers:
        msg = make_message(me, peer, body, kind=kind or a.kind, subject=a.subject, re_id=re_id, links=a.link,
                           needs_user=a.needs_user, model=a.model)
        note = post(msg, peer, a.wake, sync=a.wait)
        sent.append(msg)
        print(f"{msg['id']} -> {peer['name']}: {note}")
        if peer.get("family") == "claude" and me.get("family") == "claude":
            print(f"  {peer['name']} is a Claude session: to wake it now, SendMessage to "
                  f"\"{live_claude_name(peer)}\" with the output of `xmsg show {msg['id']}`.")
    if a.wait:
        return wait_for_replies(sent, a.timeout, me["name"])
    return 0


def wait_for_replies(sent: list[dict], timeout: float, reader: str) -> int:
    """Print each recipient's reply as it arrives; exit 3 if any failed, was held or timed out."""
    pending = {m["id"]: m for m in sent}
    deadline, code = time.time() + timeout, 0
    while pending:
        for e in events():
            mid = e.get("id")
            if mid not in pending:
                continue
            if e["ev"] == "answered" and e.get("reply"):
                reply = load_message(e["reply"])
                model = f" ({reply['model']})" if reply.get("model") else ""
                print(f"\n=== reply {reply['id']} from {reply['from']}{model} to {mid} ===\n{reply['body'].rstrip()}",
                      flush=True)
                if clear_marker(reader, reply["id"]):
                    emit("read", reply["id"], by=reader)
                pending.pop(mid)
            elif e["ev"] in ("failed", "held"):
                print(f"\n=== {mid} to {pending[mid]['to']}: {e['ev']} ({e.get('note', '')}) ===", flush=True)
                pending.pop(mid)
                code = 3
        if not pending:
            break
        if time.time() >= deadline:
            for mid, m in pending.items():
                print(f"\n=== no reply from {m['to']} to {mid} within {timeout:.0f} s; "
                      "it will land in the inbox ===", flush=True)
            return 3
        time.sleep(1)
    return code


def cmd_reply(a) -> int:
    orig = load_message(resolve_id(a.id))
    me = sender_of(a)
    msg, note = reply_to_message(orig, me, read_body(a), kind=a.kind, state=a.state, links=a.link,
                                 wake=a.wake, model=a.model)
    print(f"{msg['id']} -> {msg['to']}: {note}")
    return 0


def cmd_inbox(a) -> int:
    name = a.name or sender_of(a)["name"]
    ids = unread(name)
    if not ids:
        print(f"no unread messages for {name}")
        return 0
    for mid in ids:
        m = load_message(mid)
        flag = " NEEDS USER" if m.get("needs_user") else ""
        print(f"{mid}  {m['from']:18} {m['kind']:8}{flag} {m['subject']}")
    return 0


def cmd_read(a) -> int:
    mid = resolve_id(a.id)
    msg = load_message(mid)
    print(render(msg), end="")
    me = a.as_name or sender_of(a)["name"]
    if slug(me) == slug(msg["to"]) and not a.peek:
        clear_marker(msg["to"], mid)
        emit("read", mid, by=me)
    return 0


def cmd_show(a) -> int:
    print(render(load_message(resolve_id(a.id))), end="")
    return 0


def cmd_status(a) -> int:
    mid = resolve_id(a.id)
    msg = load_message(mid)
    print(render(msg, style="plain"), end="")
    print("events:")
    for e in events_by_id().get(mid, []):
        extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("ts", "ev", "id"))
        print(f"  {e['ts']} {e['ev']:10} {extra}")
    return 0


def check_state_owner(msg: dict, who: str, state: str) -> None:
    """The recipient owns a request's status; the requester may only withdraw it (superseded)."""
    if msg["kind"] != "request":
        raise XmsgError(f"{msg['id']} is a {msg['kind']}, not a request")
    if slug(who) in (slug(msg["to"]), "user") or (state == "superseded" and slug(who) == slug(msg["from"])):
        return
    raise XmsgError(f"{msg['to']} owns the status of {msg['id']}; record what you did with it instead: "
                    f"xmsg note {msg['id']} \"...\"")


def cmd_state(a) -> int:
    mid = resolve_id(a.id)
    msg = load_message(mid)
    who = sender_of(a)["name"]
    check_state_owner(msg, who, a.state)
    emit("state", mid, by=who, state=a.state, note=a.note, links=a.link)
    print(f"{mid}: {a.state}")
    return 0


def cmd_note(a) -> int:
    mid = resolve_id(a.id)
    emit("note", mid, by=sender_of(a)["name"], note=a.text, links=a.link)
    print(f"{mid}: noted")
    return 0


def cmd_ledger(a) -> int:
    rows = ledger_rows(open_only=a.open, who=a.who)
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("no requests" + (" open" if a.open else ""))
        return 0
    for r in rows:
        flag = " [needs user]" if r["needs_user"] else ""
        print(f"{r['id']}  {r['state']:10} {r['delivery']:9} {r['from']} -> {r['to']}{flag}")
        print(f"    {r['subject']}")
        if r["note"]:
            print(f"    note: {r['note']}")
        if r["links"]:
            print(f"    links: {', '.join(r['links'])}")
    return 0


def format_event(e: dict) -> str:
    t = e["ts"][11:19]
    who = e.get("by", "")
    if e["ev"] == "sent":
        flag = " [NEEDS USER]" if e.get("needs_user") else ""
        return f"{t} {e['id']} {who} -> {e.get('to')} [{e.get('kind')}]{flag} {e.get('subject', '')}"
    detail = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("ts", "ev", "id", "by"))
    return " ".join(x for x in (t, f"{e.get('id', ''):18}", e["ev"], who, detail) if x)


def print_event(e: dict, bodies: bool) -> None:
    print(format_event(e), flush=True)
    if bodies and e["ev"] == "sent":
        with contextlib.suppress(OSError, json.JSONDecodeError):
            body = load_message(e["id"])["body"].rstrip()
            print("    " + body.replace("\n", "\n    ") + "\n", flush=True)


def cmd_log(a) -> int:
    path = home() / "events.jsonl"
    evs = events()
    for e in evs[-a.n:]:
        print_event(e, a.bodies)
    if not a.follow:
        return 0
    pos = path.stat().st_size if path.exists() else 0
    while True:
        time.sleep(1)
        if not path.exists() or path.stat().st_size <= pos:
            continue
        with open(path) as fh:
            fh.seek(pos)
            chunk = fh.read()
            pos = fh.tell()
        for line in chunk.splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                print_event(json.loads(line), a.bodies)


def summary_line(mid: str) -> str:
    m = load_message(mid)
    flag = " NEEDS USER" if m.get("needs_user") else ""
    return f"{mid} from {m['from']} [{m['kind']}]{flag} \"{m['subject']}\""


def cmd_watch(a) -> int:
    name = a.name or sender_of(a)["name"]
    inbox_dir(name).mkdir(parents=True, exist_ok=True)
    hb = home() / "watchers" / f"{slug(name)}.json"
    seen: set[str] = set()
    first, last_hb, start = True, 0.0, time.time()
    delivered = {e["id"] for e in events() if e["ev"] == "delivered" and e.get("id")}
    while True:
        if time.time() - last_hb > 20:
            atomic_write(hb, json.dumps({"name": name, "pid": os.getpid(), "ts": iso()}))
            last_hb = time.time()
        ids = unread(name)
        if a.next:
            # Exit on the first message not announced before; already-announced unread ones never retrigger.
            fresh = [i for i in ids if i not in delivered]
            if fresh:
                for i in fresh:
                    emit("delivered", i, to=name, via="watch")
                older = [i for i in ids if i not in fresh]
                print(f"XMSG {len(fresh)} new for {name}: " + "; ".join(summary_line(i) for i in fresh)
                      + " -- read each with: xmsg read ID, then start `xmsg watch --next` again"
                      + (f" (still unread: {', '.join(older)})" if older else ""), flush=True)
                return 0
            if a.max_wait is not None and time.time() - start >= a.max_wait:
                print(f"XMSG nothing new for {name} after {a.max_wait:.0f} s", flush=True)
                return 0
            time.sleep(a.interval)
            continue
        new = [i for i in ids if i not in seen]
        if new and first:
            print(f"XMSG {len(new)} unread for {name}: " + "; ".join(summary_line(i) for i in new)
                  + " -- read each with: xmsg read ID", flush=True)
        elif new:
            for i in new:
                print(f"XMSG {summary_line(i)} -- read with: xmsg read {i}", flush=True)
        for i in new:
            if i not in delivered:
                emit("delivered", i, to=name, via="watch")
                delivered.add(i)
        seen.update(new)
        first = False
        if a.once:
            return 0
        time.sleep(a.interval)


def cmd_stop(a) -> int:
    stop = home() / "STOP"
    if a.clear:
        if stop.exists():
            stop.unlink()
        print("turn deliveries resumed")
    else:
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text(iso() + "\n")
        print(f"turn deliveries held ({stop}); clear with: xmsg stop --clear")
    return 0


def cmd_turn(a) -> int:
    run_turn(a.id)
    return 0


def skill_source() -> Path:
    """The bundled agent skill: inside an installed wheel, or skills/xmsg in a source checkout."""
    here = Path(__file__).resolve().parent
    for cand in (here / "skill", here.parents[1] / "skills" / "xmsg"):
        if (cand / "SKILL.md").exists():
            return cand
    raise XmsgError("the bundled skill was not found; reinstall xmsg")


def cmd_install_skill(a) -> int:
    src = skill_source()
    dests = ([Path(d).expanduser() for d in a.dest] if a.dest
             else [Path.home() / ".claude" / "skills" / "xmsg", Path.home() / ".agents" / "skills" / "xmsg"])
    for d in dests:
        if d.is_symlink() or d.exists():
            if d.resolve() == src.resolve():
                print(f"{d}: already this install's skill")
                continue
            if not a.force:
                print(f"{d}: exists, skipped (--force replaces it)")
                continue
            if d.is_symlink() or d.is_file():
                d.unlink()
            else:
                shutil.rmtree(d)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, d, ignore=shutil.ignore_patterns("__pycache__"))
        print(f"installed the xmsg skill at {d}")
    return 0


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xmsg", description=__doc__.split("\n\n")[0])
    p.add_argument("--version", action="version", version=f"xmsg {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_from(sp):
        sp.add_argument("--from", dest="sender", help="sender name (default: detected session, else 'user')")
        sp.add_argument("--model", help="sender's model (default: detected from the session, else the registry)")

    def add_body(sp):
        sp.add_argument("--body", help="message text (else --file, else stdin)")
        sp.add_argument("--file", help="read the message text from a file")

    sp = sub.add_parser("whoami", help="show this session's xmsg identity")
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("register", help="add or update a peer")
    sp.add_argument("name", nargs="?")
    sp.add_argument("--self", action="store_true", help="register the current Claude/Codex session")
    sp.add_argument("--family", choices=sorted(FAMILIES))
    sp.add_argument("--address", help="session uuid, thread name or conversation id")
    sp.add_argument("--delivery", choices=sorted(set(FAMILIES.values())))
    sp.add_argument("--owns", help="comma-separated topics routed with owner:TOPIC")
    sp.add_argument("--alias", action="append")
    sp.add_argument("--cwd", help="working directory for turn deliveries")
    sp.add_argument("--note")
    sp.add_argument("--turn-cmd", help="JSON argv with {address} and {message} placeholders")
    sp.add_argument("--max-turns-per-day", type=int)
    sp.add_argument("--model", help="the peer's model, for attribution (e.g. gemini-4-argon-high)")
    sp.set_defaults(fn=cmd_register)

    sp = sub.add_parser("unregister", help="remove a peer and its roles")
    sp.add_argument("name")
    sp.set_defaults(fn=cmd_unregister)

    sp = sub.add_parser("role", help="show roles, or point ROLE at one or more peers (role review sol astra)")
    sp.add_argument("role", nargs="?")
    sp.add_argument("peers", nargs="*")
    sp.set_defaults(fn=cmd_role)

    sp = sub.add_parser("peers", help="registered peers, roles and live Claude sessions")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_peers)

    for name, kind in (("send", None), ("request", "request")):
        sp = sub.add_parser(name, help="send a message" if kind is None else "send a request (tracked in the ledger)")
        sp.add_argument("--to", required=True, help="names, roles, role:R or owner:TOPIC, comma-separated")
        sp.add_argument("--subject")
        if kind is None:
            sp.add_argument("--kind", choices=KINDS, default="message")
        sp.add_argument("--re", help="message id this answers or follows")
        sp.add_argument("--link", action="append", help="run dir, release tag or file (repeatable)")
        sp.add_argument("--needs-user", action="store_true", help="flag that the user must decide")
        sp.add_argument("--wake", action="store_true", help="also wake a Codex thread (codex queue --resume)")
        sp.add_argument("--wait", action="store_true",
                        help="block until each recipient replies and print the replies (headless turns run inline)")
        sp.add_argument("--timeout", type=float, default=3600, help="seconds --wait waits (default 3600)")
        add_from(sp)
        add_body(sp)
        sp.set_defaults(fn=(lambda a, k=kind: cmd_send(a, k)))

    sp = sub.add_parser("reply", help="reply to a message's sender")
    sp.add_argument("id")
    sp.add_argument("--kind", choices=("reply", "receipt"), default="reply")
    sp.add_argument("--state", choices=STATES, help="also move the request to this state")
    sp.add_argument("--link", action="append")
    sp.add_argument("--wake", action="store_true")
    add_from(sp)
    add_body(sp)
    sp.set_defaults(fn=cmd_reply)

    sp = sub.add_parser("inbox", help="unread messages")
    sp.add_argument("name", nargs="?")
    add_from(sp)
    sp.set_defaults(fn=cmd_inbox)

    sp = sub.add_parser("read", help="print a message and mark it read")
    sp.add_argument("id")
    sp.add_argument("--as", dest="as_name", help="read as this recipient")
    sp.add_argument("--peek", action="store_true", help="do not mark it read")
    add_from(sp)
    sp.set_defaults(fn=cmd_read)

    sp = sub.add_parser("show", help="print a message as delivered (for pasting into SendMessage)")
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("status", help="a message and its delivery events")
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("state", help="move a request through the ledger")
    sp.add_argument("id")
    sp.add_argument("state", choices=STATES)
    sp.add_argument("--note")
    sp.add_argument("--link", action="append")
    add_from(sp)
    sp.set_defaults(fn=cmd_state)

    sp = sub.add_parser("note", help="add a note to a message (e.g. what you did with a reply)")
    sp.add_argument("id")
    sp.add_argument("text")
    sp.add_argument("--link", action="append")
    add_from(sp)
    sp.set_defaults(fn=cmd_note)

    sp = sub.add_parser("ledger", help="requests and their states")
    sp.add_argument("--open", action="store_true", help="hide done, rejected and superseded")
    sp.add_argument("--who", help="only requests from or to this peer")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_ledger)

    sp = sub.add_parser("log", help="the event feed (tail -f with --follow)")
    sp.add_argument("-n", type=int, default=30)
    sp.add_argument("--follow", "-f", action="store_true")
    sp.add_argument("--bodies", "-b", action="store_true", help="print each message's full text under it")
    sp.set_defaults(fn=cmd_log)

    sp = sub.add_parser("watch", help="announce new messages (--next: as a background command; else under Monitor)")
    sp.add_argument("name", nargs="?")
    sp.add_argument("--next", action="store_true",
                    help="exit as soon as a new message arrives (run with Bash run_in_background; restart after)")
    sp.add_argument("--max-wait", type=float, help="with --next, give up after this many seconds")
    sp.add_argument("--interval", type=float, default=2.0)
    sp.add_argument("--once", action="store_true")
    add_from(sp)
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("stop", help="hold all headless-turn deliveries (or --clear)")
    sp.add_argument("--clear", action="store_true")
    sp.set_defaults(fn=cmd_stop)

    sp = sub.add_parser("install-skill", help="copy the agent skill to ~/.claude/skills and ~/.agents/skills")
    sp.add_argument("--dest", action="append", help="install here instead (repeatable)")
    sp.add_argument("--force", action="store_true", help="replace an existing skill directory")
    sp.set_defaults(fn=cmd_install_skill)

    sp = sub.add_parser("_turn", help=argparse.SUPPRESS)
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_turn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home().mkdir(parents=True, exist_ok=True)
    try:
        return args.fn(args) or 0
    except XmsgError as exc:
        print(f"xmsg: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

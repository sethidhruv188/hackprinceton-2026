"""One conversation per owner, shared by iMessage and the dashboard's Ask page.

- owner_thread(handle): "owner:<12 hex>" from a SHA-256 of the owner's normalized phone number or email, so the
  number itself never appears in thread ids, file names or logs. The dashboard has no phone number: it uses the first
  number in PHOTON_OWNER_NUMBERS (the primary owner), or "owner:default" when none is set.
- The agent keeps the thread's history in its checkpointer under that id for both channels (agent.run). Guests keep
  their own per-chat threads ("imessage:<space>") and never see the owner's.
- transcript: <data>/agent_conversations/<thread>.jsonl, one line per message {at, channel, who, text}, so the Ask page
  can show recent messages from both channels, each labeled with its channel. Owner threads only. Gitignored with the
  data folder; never sent anywhere.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "owner:"
DIR = "agent_conversations"
MAX_TEXT = 4000


def _normalize(handle):
    from imessage.config import normalize
    return normalize(handle)


def owner_thread(handle=None):
    """The shared thread id for an owner's phone number / email; None -> the primary owner (dashboard)."""
    h = _normalize(handle) if handle else primary_owner()
    if not h:
        return PREFIX + "default"
    return PREFIX + hashlib.sha256(h.encode("utf-8")).hexdigest()[:12]


def primary_owner():
    """First number in PHOTON_OWNER_NUMBERS (read from the environment, after .env)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass
    first = next((x for x in re.split(r"[,;]+", os.environ.get("PHOTON_OWNER_NUMBERS", "")) if x.strip()), "")
    return _normalize(first) if first else ""


def is_shared(thread_id):
    return str(thread_id).startswith(PREFIX)


def checkpoint_key(thread_id, channel):
    """Owner threads are shared across channels; everything else stays per channel."""
    return str(thread_id) if is_shared(thread_id) else f"{channel}:{thread_id}"


def _path(data, thread_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(thread_id))
    return Path(data) / DIR / f"{safe}.jsonl"


def append(data, thread_id, channel, who, text):
    """Add one message to an owner thread's transcript (no-op for other threads)."""
    if not is_shared(thread_id):
        return
    p = _path(data, thread_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = dict(at=datetime.now(timezone.utc).isoformat(timespec="seconds"), channel=channel, who=who,
               text=str(text)[:MAX_TEXT])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def recent(data, thread_id, limit=50):
    """The last `limit` messages of a thread, oldest first. Bad lines are skipped."""
    p = _path(data, thread_id)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines()[-max(1, limit) * 2:]:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("text"):
            out.append(dict(at=r.get("at"), channel=r.get("channel"), who=r.get("who"), text=r.get("text")))
    return out[-limit:]

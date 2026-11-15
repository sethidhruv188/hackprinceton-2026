"""Bot state in imessage/state/ (gitignored), written atomically. No message text and no phone numbers are stored:
only the cursor, ids of messages already answered, the agent's user id, and owner spaces for the daily digest."""
import json
import os
import time
from pathlib import Path

SEEN_DAYS = 30


class State:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cursor = int(self._read("cursor.json", {"cursor": 0})["cursor"])
        self.seen = dict(self._read("seen.json", {}))                       # message id -> unix time answered
        meta = self._read("meta.json", {})
        self.agent_user_id = meta.get("agent_user_id", "")
        self.owner_spaces = dict(meta.get("owner_spaces", {}))              # space id -> {"last_digest": "YYYY-MM-DD"}

    def _read(self, name, default):
        p = self.dir / name
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
        except (OSError, ValueError):
            return default

    def _write(self, name, obj):
        p = self.dir / name
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=0, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)

    def save_cursor(self, seq):
        if seq > self.cursor:
            self.cursor = int(seq)
            self._write("cursor.json", {"cursor": self.cursor})

    def is_seen(self, message_id):
        return message_id in self.seen

    def mark_seen(self, ids):
        now = time.time()
        for i in ids:
            self.seen[i] = now
        cutoff = now - SEEN_DAYS * 86400
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        self._write("seen.json", self.seen)

    def remember_space(self, space_id, owner, agent_user_id=""):
        changed = False
        if agent_user_id and agent_user_id != self.agent_user_id:
            self.agent_user_id, changed = agent_user_id, True
        if owner and space_id not in self.owner_spaces:
            self.owner_spaces[space_id], changed = {"last_digest": ""}, True
        if changed:
            self.save_meta()

    def save_meta(self):
        self._write("meta.json", {"agent_user_id": self.agent_user_id, "owner_spaces": self.owner_spaces})

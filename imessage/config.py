"""Settings for the iMessage bot, from the environment (.env), and phone-number masking for logs.

  PHOTON_API_KEY, PHOTON_PROJECT_ID   Photon project key (project:read and project:write) and id
  PHOTON_AGENT_HANDLE                 the number Photon assigned to the agent (optional; only messages to it count)
  PHOTON_OWNER_NUMBERS                owner numbers, comma-separated, E.164 (+15551234567): role "owner"
                                      (PHOTON_TEST_SENDER is read too, for the echo bot's old setting)
  PHOTON_ALLOW_GUESTS                 true (default): everyone else is a read-only "guest"; false: ignore them
  AGENT_DATA, AGENT_OUT               the agent's folders (default data, outputs)
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "imessage" / "state"


def normalize(handle):
    """E.164 for phone numbers ('+1 (555) 123-4567' -> '+15551234567'); emails lower-cased."""
    h = str(handle or "").strip()
    if "@" in h:
        return h.lower()
    digits = re.sub(r"\D", "", h)
    return f"+{digits}" if digits else ""


def mask(handle):
    """Logs show only the last 4 characters: '+15551234567' -> '***4567'."""
    h = str(handle or "")
    return "***" + h[-4:] if len(h) > 4 else "***"


def _truthy(v, default=True):
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    api_key: str = ""
    project_id: str = ""
    agent_handle: str = ""
    owners: frozenset = field(default_factory=frozenset)
    allow_guests: bool = True
    data: str = "data"
    out: str = "outputs"
    origin: str = "https://api.photon.codes"
    batch_seconds: float = 4.0            # wait this long after a space's last message, then answer them together
    slow_notice_seconds: float = 8.0      # send "Looking into that..." if the agent takes longer than this
    bubble_chars: int = 450               # split long replies into bubbles of about this size
    max_bubbles: int = 3
    state_dir: Path = STATE_DIR

    def role_of(self, handle):
        return "owner" if normalize(handle) in self.owners else "guest"


def load(env=None):
    env = os.environ if env is None else env
    owners = [normalize(x) for x in re.split(r"[,;]+", (env.get("PHOTON_OWNER_NUMBERS") or "") + "," +
                                             (env.get("PHOTON_TEST_SENDER") or "")) if x.strip()]
    return Config(api_key=env.get("PHOTON_API_KEY", ""), project_id=env.get("PHOTON_PROJECT_ID", ""),
                  agent_handle=normalize(env.get("PHOTON_AGENT_HANDLE", "")), owners=frozenset(o for o in owners if o),
                  allow_guests=_truthy(env.get("PHOTON_ALLOW_GUESTS"), True), data=env.get("AGENT_DATA", "data"),
                  out=env.get("AGENT_OUT", "outputs"), origin=env.get("PHOTON_API_ORIGIN", "https://api.photon.codes"))

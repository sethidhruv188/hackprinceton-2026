"""Read a Photon event payload: only "message.received" events with text from a human to the agent count.

The event already holds everything (no list_messages call): data.object.content.text, data.object.id,
data.object.sentBy.handle/kind, data.object.receivedBy.id/kind/handle, data.object.space.id.
"""
from dataclasses import dataclass

from imessage.config import normalize


@dataclass(frozen=True)
class Incoming:
    message_id: str
    space_id: str
    sender: str            # normalized handle (phone number or email)
    agent_user_id: str     # the agent's user id: senderId for replies
    text: str
    seq: int


def _get(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def parse(event, seq, agent_handle=""):
    """(Incoming, None) for a message to answer, or (None, reason) for anything to skip."""
    if not isinstance(event, dict):
        return None, "not an event"
    kind = event.get("type")
    if kind != "message.received":
        return None, f"ignored event type {kind}"
    obj = _get(event, "data", "object") or {}
    sent, recv = obj.get("sentBy") or {}, obj.get("receivedBy") or {}
    if sent.get("kind") != "human":
        return None, "not sent by a human"
    if recv.get("kind") != "agent":
        return None, "not received by the agent"
    if agent_handle and recv.get("handle") and normalize(recv.get("handle")) != agent_handle:
        return None, "sent to another handle"
    if sent.get("id") and sent.get("id") == recv.get("id"):
        return None, "the agent's own message"
    content = obj.get("content") or {}
    text = content.get("text") if isinstance(content, dict) else None
    if content.get("type") not in (None, "text") or not isinstance(text, str) or not text.strip():
        return None, f"not text ({content.get('type') or 'empty'})"
    mid, space = obj.get("id"), _get(obj, "space", "id")
    if not mid or not space or not recv.get("id"):
        return None, "missing id, space or agent id"
    return Incoming(message_id=str(mid), space_id=str(space), sender=normalize(sent.get("handle")),
                    agent_user_id=str(recv["id"]), text=text.strip(), seq=int(seq)), None

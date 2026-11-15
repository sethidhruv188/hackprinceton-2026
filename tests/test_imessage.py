"""iMessage bot (imessage/): parsing, roles, batching, idempotency and the low-water-mark cursor, the slow notice,
splitting, logging without message text, the Socket.IO adapter and the daily digest. Fake Photon client and fake
agent only: no network."""
import asyncio
import logging
import time

import pytest

from agent.threads import owner_thread
from imessage import alerts, bot as B, config as C
from imessage.parse import parse
from imessage.state import State

OWNER, GUEST, GUEST2 = "+15550001111", "+15550002222", "+15550003333"
AGENT_ID = "usr_agent"


def event(mid, text, sender=OWNER, space="spc_1", kind="message.received", content_type="text", sender_kind="human"):
    return {"type": kind, "id": f"evt_{mid}", "data": {"object": {
        "id": mid, "content": {"type": content_type, "text": text}, "sentBy": {"handle": sender, "kind": sender_kind, "id": f"u_{sender}"},
        "receivedBy": {"id": AGENT_ID, "kind": "agent", "handle": "+15559990000"}, "space": {"id": space}}}}


class FakeSender:
    def __init__(self, fail=False):
        self.sent, self.typing_calls, self.fail = [], [], fail

    async def send_text(self, space_id, sender_id, parent_id, text, key):
        if self.fail:
            raise ConnectionError("photon down")
        self.sent.append(dict(space=space_id, sender=sender_id, parent=parent_id, text=text, key=key))

    async def typing(self, space_id, sender_id, on):
        self.typing_calls.append((space_id, on))


class FakeAgent:
    def __init__(self, reply="Order 2,009 cases from Sunrise by Nov 14.", delay=0.0):
        self.calls, self.reply, self.delay = [], reply, delay

    def __call__(self, thread_id, text, role, channel, data, out):
        self.calls.append(dict(thread=thread_id, text=text, role=role, channel=channel))
        time.sleep(self.delay)
        return self.reply


def make(tmp_path, agent=None, sender=None, **cfg_kw):
    cfg = C.Config(owners=frozenset({OWNER}), state_dir=tmp_path / "state", batch_seconds=0.05, slow_notice_seconds=0.3, **cfg_kw)
    return B.Bot(cfg, sender or FakeSender(), agent or FakeAgent()), cfg


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ config and parsing
def test_normalize_and_mask():
    assert C.normalize("+1 (555) 000-1111") == OWNER and C.normalize("Owner@Example.com") == "owner@example.com"
    assert C.mask(OWNER) == "***1111" and C.mask("") == "***"
    cfg = C.load({"PHOTON_OWNER_NUMBERS": "+1 555 000 1111, +15550004444", "PHOTON_ALLOW_GUESTS": "false"})
    assert cfg.owners == {OWNER, "+15550004444"} and cfg.allow_guests is False
    assert cfg.role_of("+1-555-000-1111") == "owner" and cfg.role_of(GUEST) == "guest"


def test_parse_reads_the_payload_and_skips_the_rest():
    m, why = parse(event("msg_1", " hello "), 7)
    assert why is None and (m.message_id, m.space_id, m.sender, m.agent_user_id, m.text, m.seq) == ("msg_1", "spc_1", OWNER, AGENT_ID, "hello", 7)
    assert parse(event("m", "x", kind="message.delivered"), 1)[1].startswith("ignored event type")
    assert parse(event("m", "x", sender_kind="agent"), 1)[1] == "not sent by a human"
    assert parse(event("m", "x", content_type="image"), 1)[1].startswith("not text")
    assert parse({"type": "message.received", "data": {}}, 1)[0] is None
    assert parse(event("m", "x"), 1, agent_handle="+15558887777")[1] == "sent to another handle"


# ------------------------------------------------------------------ batching, roles, cursor
def test_batches_per_space_into_one_turn_and_one_reply(tmp_path):
    bot, _ = make(tmp_path)

    async def go():
        await bot.on_delivery(event("m1", "how many"), 1)
        await bot.on_delivery(event("m2", "of moong dal"), 2)
        await bot.on_delivery(event("m3", "hi", sender=GUEST, space="spc_2"), 3)
        await bot.drain()
    run(go())
    calls = sorted(bot.agent.calls, key=lambda c: c["thread"])
    calls = sorted(bot.agent.calls, key=lambda c: c["role"])      # owners use their shared thread (agent/threads.py)
    assert calls == [dict(thread="spc_2", text="hi", role="guest", channel="imessage"),
                     dict(thread=owner_thread(OWNER), text="how many\nof moong dal", role="owner", channel="imessage")]
    replies = [s for s in bot.sender.sent if s["space"] == "spc_1"]
    assert len(replies) == 1 and replies[0]["parent"] is None and replies[0]["key"] == "reply:spc_1:m2:0"   # not threaded
    assert replies[0]["sender"] == AGENT_ID and bot.state.cursor == 3
    assert ("spc_1", True) in bot.sender.typing_calls and ("spc_1", False) in bot.sender.typing_calls
    assert State(tmp_path / "state").cursor == 3                                    # persisted
    assert set(bot.state.owner_spaces) == {"spc_1"} and bot.state.agent_user_id == AGENT_ID


def test_mixed_senders_count_as_guest(tmp_path):
    bot, _ = make(tmp_path)

    async def go():
        await bot.on_delivery(event("m1", "a", sender=OWNER), 1)
        await bot.on_delivery(event("m2", "b", sender=GUEST), 2)
        await bot.drain()
    run(go())
    assert bot.agent.calls[0]["role"] == "guest"


def test_guests_off(tmp_path):
    bot, _ = make(tmp_path, allow_guests=False)

    async def go():
        await bot.on_delivery(event("m1", "hi", sender=GUEST), 5)
        await bot.drain()
    run(go())
    assert not bot.agent.calls and not bot.sender.sent and bot.state.cursor == 5


def test_redelivery_is_answered_once(tmp_path):
    bot, _ = make(tmp_path)

    async def go():
        await bot.on_delivery(event("m1", "hi"), 1)
        await bot.drain()
        await bot.on_delivery(event("m1", "hi"), 2)                                # same message id, new seq
        await bot.drain()
    run(go())
    assert len(bot.agent.calls) == 1 and len(bot.sender.sent) == 1 and bot.state.cursor == 2
    bot2, _ = make(tmp_path)                                                       # after a restart

    async def again():
        await bot2.on_delivery(event("m1", "hi"), 3)
        await bot2.drain()
    run(again())
    assert not bot2.agent.calls and bot2.state.cursor == 3


def test_cursor_is_a_low_water_mark(tmp_path):
    bot, _ = make(tmp_path, )
    bot.cfg.batch_seconds = 0.3

    async def go():
        await bot.on_delivery(event("m1", "hi"), 10)
        await bot.on_delivery(event("x", "y", kind="message.delivered"), 11)         # skipped at once
        assert bot.state.cursor == 9                                                 # held back by the unanswered seq 10
        await bot.drain()
    run(go())
    assert bot.state.cursor == 11


def test_failed_send_leaves_messages_unanswered(tmp_path, monkeypatch):
    monkeypatch.setattr(B.asyncio, "sleep", _fast_sleep)
    bot, _ = make(tmp_path, sender=FakeSender(fail=True))

    async def go():
        await bot.on_delivery(event("m1", "hi"), 4)
        await bot.drain()
    run(go())
    assert bot.state.cursor == 0 and not bot.state.is_seen("m1")                    # replayed after a restart


_real_sleep = asyncio.sleep


async def _fast_sleep(s):
    await _real_sleep(min(s, 0.01))


def test_slow_agent_gets_a_notice_first(tmp_path):
    bot, _ = make(tmp_path, agent=FakeAgent(delay=0.6))

    async def go():
        await bot.on_delivery(event("m1", "big question"), 1)
        await bot.drain()
    run(go())
    texts = [s["text"] for s in bot.sender.sent]
    assert texts[0] == B.NOTICE and texts[1].startswith("Order 2,009") and bot.sender.sent[0]["key"] == "notice:spc_1:m1"


def test_fast_agent_gets_no_notice(tmp_path):
    bot, _ = make(tmp_path)

    async def go():
        await bot.on_delivery(event("m1", "q"), 1)
        await bot.drain()
    run(go())
    assert B.NOTICE not in [s["text"] for s in bot.sender.sent]


def test_agent_failure_sends_an_apology(tmp_path):
    def broken(*a, **k):
        raise RuntimeError("set OPENROUTER_API_KEY and LLM_MODEL")
    bot, _ = make(tmp_path, agent=broken)

    async def go():
        await bot.on_delivery(event("m1", "q"), 1)
        await bot.drain()
    run(go())
    assert bot.sender.sent[-1]["text"] == B.APOLOGY and bot.state.is_seen("m1")


# ------------------------------------------------------------------ splitting
def test_split_reply():
    long = ("Sunrise: order 2,009 cases by Nov 14. " * 30).strip()
    parts = B.split_reply(long, 450, 3)
    assert 2 <= len(parts) <= 3 and all(len(p) <= 900 for p in parts[:-1]) and parts[0].endswith(".")
    table = "**Order now**:\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert B.split_reply(table, 450, 3) == ["Order now:\na - b\n1 - 2"]          # no markdown, no table
    assert B.split_reply("short", 450, 3) == ["short"]


def test_long_reply_goes_out_in_bubbles_with_their_own_keys(tmp_path):
    bot, _ = make(tmp_path, agent=FakeAgent(reply="Line about an order. " * 60))

    async def go():
        await bot.on_delivery(event("m9", "everything"), 1)
        await bot.drain()
    run(go())
    keys = [s["key"] for s in bot.sender.sent]
    assert 2 <= len(keys) <= 3 and keys == [f"reply:spc_1:m9:{i}" for i in range(len(keys))]


# ------------------------------------------------------------------ logging
def test_logs_mask_numbers_and_never_hold_message_text(tmp_path):
    B.log.handlers.clear()
    B.setup_logging(tmp_path / "state")
    bot, _ = make(tmp_path)

    async def go():
        await bot.on_delivery(event("m1", "SECRET-ORDER-TEXT please"), 1)
        await bot.on_delivery(event("m2", "hello", sender=GUEST, space="spc_2"), 2)
        await bot.drain()
    run(go())
    for h in B.log.handlers:
        h.flush()
    text = (tmp_path / "state" / "bot.log").read_text(encoding="utf-8")
    assert "SECRET-ORDER-TEXT" not in text and OWNER not in text and GUEST not in text
    assert "***1111" in text and "***2222" in text
    state_files = " ".join(p.read_text(encoding="utf-8") for p in (tmp_path / "state").glob("*.json"))
    assert "SECRET" not in state_files and OWNER not in state_files                 # no text or numbers in state
    B.log.handlers.clear()


# ------------------------------------------------------------------ Socket.IO adapter
class FakeSio:
    def __init__(self):
        self.handlers, self.calls = {}, []

    def on(self, name, handler):
        self.handlers[name] = handler

    async def call(self, name, data, timeout=10):
        self.calls.append((name, data))
        return {"ok": True}


def test_adapter_subscribes_from_saved_cursor_and_drops_old_seqs(tmp_path):
    bot, cfg = make(tmp_path)
    bot.state.save_cursor(41)
    sio = FakeSio()
    ad = B.SocketAdapter(cfg, bot, sio=sio)
    run(sio.handlers["connect"]())
    assert sio.calls == [("subscribe", {"startSeq": 41})]
    sio.handlers["delivery"](event("m1", "x"), {"seq": 40})                          # already done: dropped
    sio.handlers["delivery"](event("m2", "y"), {"seq": 42})
    assert ad.queue.qsize() == 1 and ad.queue.get_nowait()[1] == 42


# ------------------------------------------------------------------ digest
def test_digest_content_from_the_demo(demo_full):
    text = alerts.build_digest(demo_full["data"], demo_full["out"], "2026-11-14")
    assert text.startswith("Daily digest Nov 14:") and "Order now: Sunrise" in text
    assert "toor (India) order now, by 2026-11-30" in text and "DEMO: port congestion" in text
    assert len(text) < 900


def test_digest_goes_only_to_owner_spaces_once_a_day(tmp_path):
    cfg = C.Config(state_dir=tmp_path / "state")
    st = State(cfg.state_dir)
    with pytest.raises(RuntimeError, match="text it first"):
        run(alerts.send_digest(cfg, FakeSender(), st, "2026-11-14", "Daily digest"))
    st.remember_space("spc_owner", True, AGENT_ID)
    st.remember_space("spc_guest", False, AGENT_ID)
    s = FakeSender()
    assert run(alerts.send_digest(cfg, s, st, "2026-11-14", "Daily digest: order now")) == ["spc_owner"]
    assert [m["space"] for m in s.sent] == ["spc_owner"] and s.sent[0]["key"] == "digest:spc_owner:2026-11-14:0"
    assert run(alerts.send_digest(cfg, s, State(cfg.state_dir), "2026-11-14", "again")) == []      # once a day, persisted
    assert run(alerts.send_digest(cfg, s, State(cfg.state_dir), "2026-11-15", "next day")) == ["spc_owner"]


def test_photon_sender_sends_plain_messages(monkeypatch):
    """The real sender leaves parentMessageId out (a normal message in the chat), and keeps the idempotency key."""
    sent = []

    class Projects:
        async def send_message(self, inp):
            sent.append(inp)

    s = B.PhotonSender.__new__(B.PhotonSender)
    s.cfg = C.Config(project_id="pho_prj_x")
    s.client = type("Client", (), {"projects": Projects()})()
    run(s.send_text("spc_1", AGENT_ID, None, "hello", "reply:spc_1:m1:0"))
    body = sent[0].body.model_dump(exclude_none=True)
    assert "parentMessageId" not in body and body["spaceId"] == "spc_1" and body["senderId"] == AGENT_ID
    assert sent[0].headers.model_dump(by_alias=True)["idempotency-key"] == "reply:spc_1:m1:0"

"""One conversation per owner shared by iMessage and the dashboard (agent/threads.py): stable thread ids that never
contain the number, a shared checkpoint for owners only, a labeled transcript for the Ask page, and guests kept apart.
Scripted fake model, demo data, no network."""
from agent import run as R
from agent import threads as TH
from fake_llm import ScriptedModel, say

TODAY = "2026-11-14"
OWNER = "+15551234567"


def humans(messages):
    return [m.content for m in messages if getattr(m, "type", "") == "human"]


def test_owner_thread_is_stable_and_hides_the_number(monkeypatch):
    t = TH.owner_thread(OWNER)
    assert t.startswith("owner:") and len(t) == len("owner:") + 12 and "5551234567" not in t
    assert TH.owner_thread("+1 (555) 123-4567") == t                        # same number, any formatting
    assert TH.owner_thread("+15550000000") != t
    monkeypatch.setenv("PHOTON_OWNER_NUMBERS", f"{OWNER}, +15550000000")
    assert TH.owner_thread() == t                                           # dashboard: the first (primary) owner
    monkeypatch.setenv("PHOTON_OWNER_NUMBERS", "")
    assert TH.owner_thread() == "owner:default"
    assert TH.checkpoint_key(t, "imessage") == TH.checkpoint_key(t, "dashboard") == t
    assert TH.checkpoint_key("spc_1", "imessage") == "imessage:spc_1"


def test_one_conversation_across_imessage_and_dashboard(copy_demo):
    d = copy_demo
    t = TH.owner_thread(OWNER)
    m1 = ScriptedModel(respond=lambda msgs, tools: say("Sunrise needs 2,009 cases."))
    R.answer(t, "what about sunrise?", today=TODAY, role="owner", channel="imessage", data=d["data"], out=d["out"], model=m1)
    m2 = ScriptedModel(respond=lambda msgs, tools: say("Lotus is fine until March."))
    R.answer(t, "and lotus?", today=TODAY, role="owner", channel="dashboard", data=d["data"], out=d["out"], model=m2)
    assert humans(m2.seen[-1]) == ["what about sunrise?", "and lotus?"]     # the dashboard sees the iMessage turn
    msgs = TH.recent(d["data"], t)
    assert [(m["channel"], m["who"]) for m in msgs] == [("imessage", "owner"), ("imessage", "agent"),
                                                         ("dashboard", "owner"), ("dashboard", "agent")]
    assert msgs[1]["text"] == "Sunrise needs 2,009 cases." and msgs[2]["text"] == "and lotus?"


def test_guests_stay_separate_and_leave_no_transcript(copy_demo):
    d = copy_demo
    t = TH.owner_thread(OWNER)
    R.answer(t, "owner secret plan", today=TODAY, role="owner", channel="imessage", data=d["data"], out=d["out"],
             model=ScriptedModel(respond=lambda msgs, tools: say("ok")))
    g = ScriptedModel(respond=lambda msgs, tools: say("hello"))
    R.answer("spc_guest", "hi", today=TODAY, role="guest", channel="imessage", data=d["data"], out=d["out"], model=g)
    assert humans(g.seen[-1]) == ["hi"]                                     # the guest never sees the owner's thread
    assert TH.recent(d["data"], "spc_guest") == []
    assert all(m["text"] != "hi" for m in TH.recent(d["data"], t))


# The bot side (owner -> shared thread, guest -> its chat space) is tested in tests/test_imessage.py
# (test_batches_per_space_into_one_turn_and_one_reply).


def test_ask_history_endpoint_reads_the_owner_thread(copy_demo, monkeypatch):
    d = copy_demo
    monkeypatch.setenv("PHOTON_OWNER_NUMBERS", OWNER)
    t = TH.owner_thread(OWNER)
    TH.append(d["data"], t, "imessage", "owner", "texted from my phone")
    TH.append(d["data"], "spc_guest", "imessage", "guest", "never shown")       # not an owner thread: ignored
    from agent.context import use
    from api import agent_routes as AR
    with use(d["data"], d["out"], TODAY, role="owner"):
        h = AR.ask_history()
    assert h["messages"] == [dict(at=h["messages"][0]["at"], channel="imessage", who="owner", text="texted from my phone")]

"""iMessage bot (Photon 2.0): texts in, agent answers out. photon_echo.py stays as the fallback.

  python -m imessage.bot            # needs PHOTON_API_KEY, PHOTON_PROJECT_ID, PHOTON_OWNER_NUMBERS and the agent's model

How it works:
- Socket.IO event stream from Photon (no public URL). "message.received" events carry the text, ids and the space;
  everything else is ignored.
- Roles: numbers in PHOTON_OWNER_NUMBERS are "owner"; others are read-only "guest" (or ignored with
  PHOTON_ALLOW_GUESTS=false). A batch with several senders counts as owner only if every sender is an owner.
- Batching: per space, about 4 s after the last message, all pending texts go to the agent as one turn; one reply.
- The typing indicator is on while the agent works; "Looking into that..." is sent only if it takes over ~8 s.
- Long replies are split at sentence / line boundaries into at most 3 bubbles; markdown is stripped. Replies are
  normal messages in the chat, not threaded replies to the owner's message.
- Idempotency: one idempotency key per bubble; answered message ids are persisted; the cursor is a low-water mark:
  it only moves past an event once that event's batch is answered (or the event is skipped), so a crash or reconnect
  replays unanswered messages and skips answered ones. No message text is ever written by the bot.
- Logs: imessage/state/bot.log and the console; phone numbers masked to the last 4 digits, never message text.
"""
import asyncio
import logging
import re

from agent.run import strip_markdown
from agent.threads import owner_thread
from imessage import config as C
from imessage.parse import parse
from imessage.state import State

log = logging.getLogger("imessage.bot")
NOTICE = "Looking into that..."
APOLOGY = "Sorry, I can't answer right now. Please try again in a few minutes."
SEND_ATTEMPTS = 3


def setup_logging(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        for h in (logging.FileHandler(state_dir / "bot.log", encoding="utf-8"), logging.StreamHandler()):
            h.setFormatter(fmt)
            log.addHandler(h)
        log.setLevel(logging.INFO)


def split_reply(text, size=450, max_parts=3):
    """Plain-text bubbles of about `size` characters, cut at line or sentence ends; at most max_parts."""
    text = strip_markdown(text)
    parts, rest = [], text.strip()
    while rest and len(parts) < max_parts - 1 and len(rest) > size:
        cut = rest[:size]
        i = max(cut.rfind("\n"), *(cut.rfind(s) + len(s) - 1 for s in (". ", "? ", "! ", "; ")))
        if i < size * 0.4:
            i = cut.rfind(" ")
        i = i if i > 0 else size
        parts.append(rest[:i + 1].strip())
        rest = rest[i + 1:].strip()
    if rest:
        if len(rest) > size * 2:                     # last bubble: keep it reasonable
            rest = rest[:size * 2].rsplit(" ", 1)[0] + " ...more on the dashboard."
        parts.append(rest)
    return [p for p in parts if p]


class PhotonSender:
    """Sends texts and the typing indicator through Photon's async client."""

    def __init__(self, cfg):
        from photon_api import AsyncPhoton
        self.cfg = cfg
        self.client = AsyncPhoton(base_url=cfg.origin, headers={"Authorization": f"Bearer {cfg.api_key}"})

    async def send_text(self, space_id, sender_id, parent_id, text, key):
        from photon_api.generated.models import SendMessageRequest, SendTextContent
        from photon_api.rpc_generated import SendMessageHeader, SendMessageInput, SendMessagePath
        body = dict(spaceId=space_id, senderId=sender_id, content=SendTextContent(type="text", format="plain", text=text))
        if parent_id:                                     # threaded reply only when asked; the bot sends plain messages
            body["parentMessageId"] = parent_id
        await self.client.projects.send_message(SendMessageInput(
            path=SendMessagePath(id=self.cfg.project_id),
            headers=SendMessageHeader.model_validate({"idempotency-key": key}),
            body=SendMessageRequest(**body)))

    async def typing(self, space_id, sender_id, on):
        from photon_api.generated.models import SetSpaceTypingRequest
        from photon_api.rpc_generated import SetSpaceTypingInput, SetSpaceTypingPath
        await self.client.projects.set_space_typing(SetSpaceTypingInput(
            path=SetSpaceTypingPath(id=self.cfg.project_id, space_id=space_id),
            body=SetSpaceTypingRequest(senderId=sender_id, typing=on)))


def default_agent(thread_id, text, role, channel, data, out):
    from agent.run import answer
    return answer(thread_id, text, role=role, channel=channel, data=data, out=out)


class Batch:
    def __init__(self):
        self.messages, self.timer = [], None


class Bot:
    """The testable core: feed it deliveries (event, seq); it batches, asks the agent and replies."""

    def __init__(self, cfg, sender, agent=default_agent, state=None):
        self.cfg, self.sender, self.agent = cfg, sender, agent
        self.state = state or State(cfg.state_dir)
        self.inflight, self.max_seq = set(), self.state.cursor
        self.batches, self.queued_ids, self.tasks = {}, set(), set()

    # ---------------------------------------------------------------- cursor (low-water mark)
    def _done(self, *seqs):
        for s in seqs:
            self.inflight.discard(s)
        low = (min(self.inflight) - 1) if self.inflight else self.max_seq
        if low > self.state.cursor:
            self.state.save_cursor(low)

    # ---------------------------------------------------------------- deliveries
    async def on_delivery(self, event, seq):
        seq = int(seq)
        if seq <= self.state.cursor:
            return                                            # replayed and already done
        self.max_seq = max(self.max_seq, seq)
        self.inflight.add(seq)
        msg, why = parse(event, seq, self.cfg.agent_handle)
        if msg is None:
            if why and not why.startswith("ignored event type"):
                log.info("seq %s skipped: %s", seq, why)
            return self._done(seq)
        if self.state.is_seen(msg.message_id) or msg.message_id in self.queued_ids:
            log.info("seq %s: message already answered or queued", seq)
            return self._done(seq)
        role = self.cfg.role_of(msg.sender)
        if role == "guest" and not self.cfg.allow_guests:
            log.info("seq %s: guest %s ignored (PHOTON_ALLOW_GUESTS=false)", seq, C.mask(msg.sender))
            return self._done(seq)
        self.state.remember_space(msg.space_id, role == "owner", msg.agent_user_id)
        log.info("seq %s: message from %s (%s), %d characters, space %s", seq, C.mask(msg.sender), role, len(msg.text),
                 C.mask(msg.space_id))
        b = self.batches.setdefault(msg.space_id, Batch())
        b.messages.append((msg, role))
        self.queued_ids.add(msg.message_id)
        if b.timer:
            b.timer.cancel()
        b.timer = asyncio.get_running_loop().call_later(self.cfg.batch_seconds, self._flush_soon, msg.space_id)

    def _flush_soon(self, space_id):
        t = asyncio.ensure_future(self.flush(space_id))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def drain(self):
        """Wait for pending batches to be answered (tests and shutdown)."""
        while self.batches or self.tasks:
            await asyncio.sleep(0.01)

    # ---------------------------------------------------------------- answering
    async def flush(self, space_id):
        b = self.batches.pop(space_id, None)
        if not b or not b.messages:
            return
        msgs = [m for m, _ in b.messages]
        role = "owner" if all(r == "owner" for _, r in b.messages) else "guest"
        last, agent_id = msgs[-1], msgs[-1].agent_user_id
        text = "\n".join(m.text for m in msgs)
        seqs = [m.seq for m in msgs]
        await self._typing(space_id, agent_id, True)
        # Owners share one conversation with the dashboard (agent/threads.py); guests keep one per chat.
        thread = owner_thread(last.sender) if role == "owner" else space_id
        work = asyncio.ensure_future(asyncio.to_thread(self.agent, thread, text, role, "imessage", self.cfg.data, self.cfg.out))
        t0 = asyncio.get_running_loop().time()
        try:
            try:
                reply = await asyncio.wait_for(asyncio.shield(work), self.cfg.slow_notice_seconds)
            except asyncio.TimeoutError:
                await self._send(space_id, agent_id, None, NOTICE, f"notice:{space_id}:{last.message_id}")
                reply = await work
        except Exception as e:                                # the agent itself failed (e.g. no model settings)
            log.error("agent failed for space %s: %s", C.mask(space_id), type(e).__name__)
            reply = APOLOGY
        await self._typing(space_id, agent_id, False)
        secs = asyncio.get_running_loop().time() - t0
        bubbles = split_reply(reply, self.cfg.bubble_chars, self.cfg.max_bubbles)
        try:
            for i, part in enumerate(bubbles):
                await self._send(space_id, agent_id, None, part, f"reply:{space_id}:{last.message_id}:{i}")
        except Exception as e:
            log.error("reply to space %s not sent (%s): left unanswered; it is replayed after a restart",
                      C.mask(space_id), type(e).__name__)
            for m in msgs:
                self.queued_ids.discard(m.message_id)
            return
        self.state.mark_seen([m.message_id for m in msgs])
        for m in msgs:
            self.queued_ids.discard(m.message_id)
        self._done(*seqs)
        log.info("answered space %s (%s, %d message(s)) in %.1fs with %d bubble(s), %d characters", C.mask(space_id), role,
                 len(msgs), secs, len(bubbles), sum(len(p) for p in bubbles))

    async def _send(self, space_id, sender_id, parent_id, text, key):
        err = None
        for attempt in range(SEND_ATTEMPTS):
            try:
                return await self.sender.send_text(space_id, sender_id, parent_id, text, key)
            except Exception as e:                            # same idempotency key on retry: never a duplicate
                err = e
                await asyncio.sleep(0.5 * (attempt + 1))
        raise err

    async def _typing(self, space_id, sender_id, on):
        try:
            await self.sender.typing(space_id, sender_id, on)
        except Exception as e:                                # cosmetic: never blocks a reply
            log.info("typing indicator failed: %s", type(e).__name__)


class SocketAdapter:
    """Photon's Socket.IO event stream -> Bot.on_delivery, in order. Resubscribes from the saved cursor on (re)connect."""

    def __init__(self, cfg, bot, sio=None):
        import socketio
        self.cfg, self.bot = cfg, bot
        self.sio = sio or socketio.AsyncClient(reconnection=True)
        self.queue = asyncio.Queue(maxsize=256)
        self.last_queued = bot.state.cursor
        self.sio.on("connect", self.on_connect)
        self.sio.on("delivery", self.on_delivery)
        self.sio.on("delivery_error", lambda err: log.error("delivery error: %s", (err or {}).get("code")))

    async def on_connect(self):
        start = self.bot.state.cursor
        result = await self.sio.call("subscribe", {"startSeq": start}, timeout=10)
        if not (result or {}).get("ok"):
            log.error("subscribe failed: %s", ((result or {}).get("error") or {}).get("code"))
            return
        self.last_queued = max(self.last_queued, start)
        log.info("connected and subscribed from cursor %s", start)

    def on_delivery(self, event, metadata):
        seq = int((metadata or {}).get("seq", 0))
        if seq <= self.last_queued:
            return
        try:
            self.queue.put_nowait((event, seq))
            self.last_queued = seq
        except asyncio.QueueFull:
            log.error("delivery queue full at seq %s: it is replayed after a reconnect", seq)

    async def run(self):
        await self.sio.connect(self.cfg.origin, socketio_path=f"/v1/projects/{self.cfg.project_id}/events/ws",
                               transports=["websocket"], headers={"Authorization": f"Bearer {self.cfg.api_key}"})
        while True:
            event, seq = await self.queue.get()
            try:
                await self.bot.on_delivery(event, seq)
            except Exception as e:                            # never stop the stream for one bad event
                log.error("event seq %s failed: %s", seq, type(e).__name__)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    cfg = C.load()
    setup_logging(cfg.state_dir)
    missing = [k for k, v in (("PHOTON_API_KEY", cfg.api_key), ("PHOTON_PROJECT_ID", cfg.project_id)) if not v]
    if missing:
        raise SystemExit(f"set {', '.join(missing)} in .env")
    if not cfg.owners:
        log.warning("PHOTON_OWNER_NUMBERS is empty: everyone is a read-only guest")
    log.info("starting: %d owner number(s), guests %s, agent data %s", len(cfg.owners),
             "allowed" if cfg.allow_guests else "ignored", cfg.data)
    bot = Bot(cfg, PhotonSender(cfg))
    asyncio.run(SocketAdapter(cfg, bot).run())


if __name__ == "__main__":
    main()

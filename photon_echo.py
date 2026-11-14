"""Photon 2.0 (Beta) echo bot: proves we can RECEIVE and SEND iMessages from Python.

How it works:
  1. A Socket.IO connection to the project's event stream wakes us up on every event
     (no public URL, webhook, or ngrok needed).
  2. On each event we list recent received messages and reply to any new one from an
     allowed sender with "Got your message: <text>".
  3. Every reply uses an idempotency key built from the incoming message ID, and replied
     IDs are saved to disk, so a redelivered event never produces a second text.

Setup (.env next to this file):
  PHOTON_API_KEY=pho_sk_...         project API key with project:read and project:write
  PHOTON_PROJECT_ID=pho_prj_...
  PHOTON_TEST_SENDER=+1XXXXXXXXXX   your iMessage number, E.164; comma-separate several
  PHOTON_AGENT_HANDLE=+1XXXXXXXXXX  the number Photon assigned (optional but recommended)

Run:  python photon_echo.py   then text the assigned number from your phone.
Remember: on a shared line the HUMAN must send the first message.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import socketio
from dotenv import load_dotenv
from photon_api import ApiError, Photon
from photon_api.generated.models import SendMessageRequest, SendTextContent, TextContent, UserSnapshot
from photon_api.rpc_generated import ListMessagesInput, SendMessageHeader, SendMessageInput, SendMessagePath

load_dotenv()
KEY = os.environ["PHOTON_API_KEY"]
PROJECT = os.environ["PHOTON_PROJECT_ID"]
SENDERS = {s.strip() for s in os.environ["PHOTON_TEST_SENDER"].split(",") if s.strip()}
AGENT = os.environ.get("PHOTON_AGENT_HANDLE", "").strip()
ORIGIN = os.environ.get("PHOTON_API_ORIGIN", "https://api.photon.codes")
LOOKBACK = timedelta(minutes=10)  # only answer messages from the last 10 minutes

# Local state (gitignored via "photon-cursor*").
CURSOR_FILE = Path("photon-cursor")
REPLIED_FILE = Path("photon-cursor.replied.json")
cursor = int(CURSOR_FILE.read_text()) if CURSOR_FILE.exists() else 0
last_queued = cursor
replied = set(json.loads(REPLIED_FILE.read_text())) if REPLIED_FILE.exists() else set()
deliveries = asyncio.Queue(maxsize=128)
sio = socketio.AsyncClient()


def make_reply(text):
    """The one line the agent will replace later."""
    return f"Got your message: {text}"


def reply_to_new_messages():
    """List recent received iMessages and answer each new one from an allowed sender."""
    since = (datetime.now(timezone.utc) - LOOKBACK).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    query = {"createdSince": since, "direction": "received", "order": "asc", "pageSize": 100, "platform": "imessage"}
    with Photon(headers={"Authorization": f"Bearer {KEY}"}) as photon:
        while True:
            page = photon.projects.list_messages(
                ListMessagesInput.model_validate({"path": {"id": PROJECT}, "query": query}))
            for item in page.items:
                m = item.root.root
                if m.status != "received" or m.id in replied or not isinstance(m.receivedBy, UserSnapshot):
                    continue
                sender, agent = m.sentBy.root, m.receivedBy.root
                if sender.kind != "human" or sender.handle not in SENDERS:
                    print(f"  ignored message from {sender.handle} (not in PHOTON_TEST_SENDER)")
                    continue
                if agent.kind != "agent" or (AGENT and agent.handle != AGENT) or sender.id == agent.id:
                    continue
                text = m.content.text if isinstance(m.content, TextContent) else "(non-text message)"
                print(f"  RECEIVED from {sender.handle}: {text!r}")
                photon.projects.send_message(SendMessageInput(
                    path=SendMessagePath(id=PROJECT),
                    headers=SendMessageHeader.model_validate({"idempotency-key": f"echo-reply:{m.id}"}),
                    body=SendMessageRequest(
                        spaceId=m.space.root.id,
                        senderId=agent.id,
                        parentMessageId=m.id,
                        content=SendTextContent(type="text", format="plain", text=make_reply(text)),
                    ),
                ))
                replied.add(m.id)
                REPLIED_FILE.write_text(json.dumps(sorted(replied)))
                print("  SENT reply (accepted by Photon; check your phone to confirm delivery)")
            token = page.nextPageToken
            if not isinstance(token, str):
                break
            query["pageToken"] = token


async def handle_event(event):
    # Print the raw event once so we learn its shape for the real agent.
    print(f"\nEVENT {event.get('type')} {event.get('id')}\n{json.dumps(event, indent=2, default=str)[:1500]}")
    await asyncio.to_thread(reply_to_new_messages)


# ---- Socket.IO plumbing, following Photon's Python client example ----
@sio.event
async def connect():
    try:
        result = await sio.call("subscribe", {"startSeq": cursor}, timeout=10)
    except socketio.exceptions.TimeoutError:
        print("Subscribe timed out; restart from the saved cursor", flush=True)
        os._exit(1)
    if not result["ok"]:
        print("Subscribe failed:", result["error"]["code"], flush=True)
        os._exit(1)
    print(f"Connected and subscribed (cursor {cursor}). Text your Photon number now.", flush=True)


@sio.on("delivery")
def delivery(event, metadata):
    global last_queued
    seq = metadata["seq"]
    if seq <= last_queued:
        return
    try:
        deliveries.put_nowait((event, seq))
        last_queued = seq
    except asyncio.QueueFull:
        print("Delivery queue full; restart from the saved cursor", flush=True)
        os._exit(1)


@sio.on("delivery_error")
def delivery_error(error):
    print("Delivery error:", error["code"])


@sio.event
def connect_error(data):
    print("Connect error (check PHOTON_API_KEY and PHOTON_PROJECT_ID):", data)


async def main():
    global cursor
    await sio.connect(
        ORIGIN,
        socketio_path=f"/v1/projects/{PROJECT}/events/ws",
        transports=["websocket"],
        headers={"Authorization": f"Bearer {KEY}"},
    )
    while True:
        event, seq = await deliveries.get()
        if seq <= cursor:
            continue
        try:
            await handle_event(event)
        except ApiError as error:
            print(f"Photon API error {error.status} (request {error.request_id or 'unknown'}). "
                  "401/403: check key permissions. 429: slow down. Cursor NOT advanced.", flush=True)
            os._exit(1)
        tmp = CURSOR_FILE.with_suffix(".tmp")
        tmp.write_text(str(seq))
        tmp.replace(CURSOR_FILE)
        cursor = seq


if __name__ == "__main__":
    asyncio.run(main())
"""Chat with the agent in the terminal.

  python -m agent.chat                         # demo data (data_demo/, outputs_demo/), as the owner, iMessage length
  python -m agent.chat --real --today 2026-11-14
  python -m agent.chat --role guest --channel dashboard --thread test-1

Needs OPENROUTER_API_KEY and LLM_MODEL (in .env). Type 'quit' to stop. Each run is one conversation (--thread keeps
it across runs).
"""
import argparse
import uuid

from dotenv import load_dotenv

from agent.run import answer


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--real", action="store_true", help="use data/ and outputs/ instead of the demo folders")
    ap.add_argument("--today", default=None, help="plan date YYYY-MM-DD (time travel); default today")
    ap.add_argument("--role", choices=["owner", "guest"], default="owner")
    ap.add_argument("--channel", choices=["imessage", "dashboard"], default="imessage")
    ap.add_argument("--thread", default=None, help="conversation id (default: a new one)")
    a = ap.parse_args()
    load_dotenv()
    data, out = ("data", "outputs") if a.real else ("data_demo", "outputs_demo")
    thread = a.thread or f"cli-{uuid.uuid4().hex[:8]}"
    print(f"{'real' if a.real else 'demo'} data, {a.role}, {a.channel}, thread {thread}. Type 'quit' to stop.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quit", "exit"):
            break
        if text:
            try:
                print("agent>", answer(thread, text, today=a.today, role=a.role, channel=a.channel, data=data, out=out))
            except RuntimeError as e:          # missing model settings
                print(f"setup: {e}")
                break


if __name__ == "__main__":
    main()

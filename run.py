"""Airlock -- run the agent.

    .venv\\Scripts\\python run.py

Connects every channel it has credentials for, registers ONE message handler,
and listens.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from caspian_sdk import CommClient  # noqa: E402

from airlock import audit  # noqa: E402
from airlock.agent import Airlock  # noqa: E402
from airlock.trust import CHANNEL_TRUST  # noqa: E402


def main() -> int:
    client = CommClient()

    print("Airlock starting\n" + "-" * 60)

    email = client.connect_email(username=os.getenv("AIRLOCK_EMAIL_USER", "airlock-agent"))
    email_conn = email.get("id") or email.get("connection_id")
    print(f"  email     UNTRUSTED   {email.get('address')}")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        try:
            client.connect_telegram(bot_token=token)
            print("  telegram  TRUSTED     connected")
        except Exception as exc:
            print(f"  telegram  FAILED      {exc}")
            print("\n  Airlock needs a trusted channel. Set TELEGRAM_BOT_TOKEN in .env")
            return 1
    else:
        print("  telegram  MISSING     set TELEGRAM_BOT_TOKEN in .env")
        print("\n  Without a trusted channel nothing can ever be approved.")
        return 1

    airlock = Airlock(client, email_connection_id=email_conn)

    # ---- exactly one message handler, shared by every channel ----
    @client.on_message
    def _on_message(message):
        try:
            airlock.handle_message(message)
        except Exception as exc:
            print(f"[airlock] handler error: {type(exc).__name__}: {exc}")

    @client.on_interaction
    def _on_interaction(interaction):
        try:
            airlock.handle_interaction(interaction)
        except Exception as exc:
            print(f"[airlock] interaction error: {type(exc).__name__}: {exc}")

    ok, note = audit.verify()
    print("-" * 60)
    print(f"  audit chain: {note}")
    print(f"  trust map:   {dict((k, v.value) for k, v in CHANNEL_TRUST.items())}")
    if not airlock.operator:
        print("\n  >> Message your Telegram bot once to register as operator. <<")
    else:
        print(f"  operator:    {airlock.operator['channel']}")
    print("-" * 60)
    print("Listening. Ctrl-C to stop.\n")

    try:
        client.listen()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

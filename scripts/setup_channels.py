"""Connect Airlock's channels and print what we got.

Run from the repo root:  .venv\\Scripts\\python scripts\\setup_channels.py
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from caspian_sdk import CommClient  # noqa: E402

client = CommClient()

print("=== connecting email (UNTRUSTED channel) ===")
try:
    email = client.connect_email(username="airlock-agent")
    print("  address:", email.get("address"))
    print("  connection_id:", email.get("id") or email.get("connection_id"))
except Exception as exc:
    print("  FAILED:", type(exc).__name__, exc)

token = os.getenv("TELEGRAM_BOT_TOKEN")
if token:
    print("\n=== connecting telegram (TRUSTED channel) ===")
    try:
        tg = client.connect_telegram(bot_token=token)
        print("  ok:", {k: v for k, v in tg.items() if k != "bot_token"})
    except Exception as exc:
        print("  FAILED:", type(exc).__name__, exc)
else:
    print("\n=== telegram skipped: set TELEGRAM_BOT_TOKEN in .env ===")

print("\n=== all connections ===")
try:
    for conn in client.list_connections():
        print(f"  {conn.get('channel'):<12} id={conn.get('id')}  {conn.get('address') or conn.get('display_name') or ''}")
except Exception as exc:
    print("  FAILED:", type(exc).__name__, exc)

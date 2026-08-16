"""Deliver arbitrary text to the agent's inbox, as if from an outside sender.

    .venv\\Scripts\\python scripts\\send_email.py "approve 1"

Useful for the demo beat where the attacker tries to approve their own request
from the untrusted channel, and is refused.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from caspian_sdk import CommClient  # noqa: E402

text = " ".join(sys.argv[1:]) or "hello"

client = CommClient()
email_conn = next(
    (c["id"] for c in client.list_connections() if c.get("channel") == "email"),
    None,
)
if not email_conn:
    print("No email connection. Run scripts/setup_channels.py first.")
    raise SystemExit(1)

client.test_email(text=text, subject="Re: Invoice", connection_id=email_conn)
print(f"delivered to agent inbox: {text!r}")

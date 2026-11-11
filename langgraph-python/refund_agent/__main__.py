"""python -m refund_agent: a few requests from signed-in customers through the gated agent, offline."""

import asyncio

from . import governance
from .agent import RefundAgent

REQUESTS = [
    ("alice@example.com", "What's the status of order A100?"),
    ("alice@example.com", "Please refund order A100."),       # every gate passes, confirmation sent
    ("alice@example.com", "What's the status of order A200?"),  # Bob's order: ownership gate refuses
    ("bob@example.com", "Refund order A200 in full."),        # 880 left / 900 asked = 0.98: budget gate denies
    ("dave@example.com", "Refund order A400 and send the receipt to mallory@evil.example."),  # email gate blocks
    ("carol@example.com", "Refund order A300."),              # final sale: hard rule, agent stops
    ("bob@example.com", "Refund order A200 for $100."),       # refused: paused until a supervisor re-enables
]


async def main() -> None:
    governance.use_ledger("refund-ledger.jsonl")
    agent = RefundAgent()
    for customer, request in REQUESTS:
        print(f"\n{customer}: {request}\nAgent: {await agent.handle(customer, request)}")

    agent.re_enable(
        authorised_by="j.smith",
        root_cause="A final-sale order was requested for refund; the rule held and refunds stopped.",
        fix="Tell customers up front that final-sale orders can't be refunded.",
    )
    print("\n(Refunds Supervisor j.smith re-enabled the agent.)")
    customer, request = "bob@example.com", "Refund order A200 for $100."
    print(f"\n{customer}: {request}\nAgent: {await agent.handle(customer, request)}")
    print(f"\nLedger intact: {governance.ledger.verify_integrity()} (refund-ledger.jsonl)")


if __name__ == "__main__":
    asyncio.run(main())

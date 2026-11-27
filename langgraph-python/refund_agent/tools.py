"""The actions this agent can take, each behind a gate (see governance.py and agent.py). Looking an
order up only reads, but what it reads could leak, so order-belongs-to-customer runs first.
"""

from __future__ import annotations

from .domain import ORDERS, Order
from .providers import payment, smtp

SENDER = "refunds@example.com"


def lookup_order(order_id: str) -> Order | None:
    """Read-only. The gates call it too, to check an order; the agent answers from it only after
    order-belongs-to-customer passes."""
    return ORDERS.get(order_id)


def issue_refund(order_id: str, amount: float) -> str:
    """Moves money. Runs only after refund-within-order and refund-budget-headroom pass."""
    return payment.create(order_id=order_id, amount=amount)


def send_email(to: str, subject: str, body: str) -> None:
    """Writes to a person. Runs only after email-to-customer-on-file passes."""
    smtp.sendmail(SENDER, to, f"Subject: {subject}\n\n{body}")

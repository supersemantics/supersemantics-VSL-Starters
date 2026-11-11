"""The actions this agent can take. Two of them have real-world effects and are gated (see
governance.py and agent.py); looking up an order only reads, so it has no gate.
"""

from __future__ import annotations

from .domain import ORDERS, Order
from .providers import payment, smtp

SENDER = "refunds@example.com"


def lookup_order(order_id: str) -> Order | None:
    """Read-only: no side effect, so no gate. Over-gating adds noise, not safety."""
    return ORDERS.get(order_id)


def issue_refund(order_id: str, amount: float) -> str:
    """Moves money. Runs only after refund-within-order and refund-budget-headroom pass."""
    return payment.create(order_id=order_id, amount=amount)


def send_email(to: str, subject: str, body: str) -> None:
    """Writes to a person. Runs only after email-to-customer-on-file passes."""
    smtp.sendmail(SENDER, to, f"Subject: {subject}\n\n{body}")

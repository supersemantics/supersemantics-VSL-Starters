"""The tools the model can call. Two have real-world effects and are gated (governance.py, wired in
agent.py); looking up an order only reads, so it has no gate.
"""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from . import domain
from .providers import payment, smtp

SENDER = "refunds@example.com"


@tool(approval_mode="never_require")
def lookup_order(order_id: Annotated[str, Field(description="Order id, for example A100")]) -> str:
    """Look up an order. Read-only: no side effect, so no gate."""
    order = domain.ORDERS.get(order_id)
    if order is None:
        return "No such order."
    return f"Order {order.order_id}: ${order.total:.2f}, {'refundable' if order.refundable else 'final sale'}, {order.customer_email}."


@tool(approval_mode="never_require")
def issue_refund(
    order_id: Annotated[str, Field(description="Order id")],
    amount: Annotated[float, Field(description="Amount to refund, in USD")],
) -> str:
    """Refund a customer. Runs only after refund-within-order and refund-budget-headroom pass."""
    refund_id = payment.create(order_id=order_id, amount=amount)
    domain.refund_book.record(order_id, amount)
    return f"Refunded ${amount:.2f} for order {order_id} (ref {refund_id})."


@tool(approval_mode="never_require")
def send_email(
    order_id: Annotated[str, Field(description="Order the email is about")],
    to: Annotated[str, Field(description="Recipient address")],
    subject: Annotated[str, Field(description="Subject line")],
    body: Annotated[str, Field(description="Message body")],
) -> str:
    """Email a customer. Runs only after email-to-customer-on-file passes."""
    smtp.sendmail(SENDER, to, f"Subject: {subject}\n\n{body}")
    return f"Email sent to {to}."

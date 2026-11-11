"""The refund agent's domain: orders, what the customer asked for, and the daily refund budget.

Plain data, no governance. Replace these with your own entities.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_email: str
    total: float
    refundable: bool  # False for final-sale items


ORDERS: dict[str, Order] = {
    "A100": Order("A100", "alice@example.com", 120.0, refundable=True),
    "A200": Order("A200", "bob@example.com", 900.0, refundable=True),
    "A300": Order("A300", "carol@example.com", 60.0, refundable=False),
    "A400": Order("A400", "dave@example.com", 40.0, refundable=True),
}

# The most this agent may refund in a day, across all customers.
DAILY_REFUND_BUDGET = 1000.0


@dataclass(frozen=True)
class OwnershipCandidate:
    """What the ownership gate evaluates: the order asked about (None if there is no such order)
    and the signed-in customer asking."""

    order: Order | None
    customer_email: str
    decision_id: str


@dataclass(frozen=True)
class RefundCandidate:
    """What the refund gates evaluate: the order, the amount, what was already refunded on the
    order, and the budget left today."""

    order: Order
    amount: float
    already_refunded: float
    budget_remaining: float
    decision_id: str


@dataclass(frozen=True)
class EmailCandidate:
    """What the email gate evaluates: the order and the address the agent wants to write to."""

    order: Order
    to: str
    decision_id: str

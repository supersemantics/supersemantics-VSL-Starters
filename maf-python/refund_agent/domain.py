"""The refund agent's domain: orders and today's refunds. Plain data, no governance."""

from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass
class RefundBook:
    """Today's refunds, by order. In production this lives in your database, not in memory."""

    daily_limit: float = 1000.0
    by_order: dict[str, float] = field(default_factory=dict)

    @property
    def budget_remaining(self) -> float:
        return self.daily_limit - sum(self.by_order.values())

    def refunded(self, order_id: str) -> float:
        return self.by_order.get(order_id, 0.0)

    def record(self, order_id: str, amount: float) -> None:
        self.by_order[order_id] = self.refunded(order_id) + amount


refund_book = RefundBook()

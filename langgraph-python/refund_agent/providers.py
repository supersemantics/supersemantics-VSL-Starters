"""Stand-ins for the payment and email services, so the starter runs offline.

Replace them with your real clients and keep the calls in tools.py: that is where the side effect
happens, and where the gates must run before it. The fakes have the same shape as the real thing:
`smtp` like Python's smtplib.SMTP (for example `smtp = smtplib.SMTP("mail.example.com")`), and
`payment` like a payment provider's refund API.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakePayments:
    refunds: list[tuple[str, float]] = field(default_factory=list)

    def create(self, *, order_id: str, amount: float) -> str:
        self.refunds.append((order_id, amount))
        return f"re_{order_id}_{len(self.refunds)}"


@dataclass
class FakeSmtp:
    """smtplib.SMTP's sendmail(from_addr, to_addrs, msg), recorded instead of delivered."""

    outbox: list[tuple[str, str, str]] = field(default_factory=list)

    def sendmail(self, from_addr: str, to_addrs: str, msg: str) -> dict:
        self.outbox.append((from_addr, to_addrs, msg))
        return {}


payment = FakePayments()
smtp = FakeSmtp()

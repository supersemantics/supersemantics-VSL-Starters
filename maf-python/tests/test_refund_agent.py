"""Prove the governance shape: each gate blocks what it should and passes what it shouldn't, and a
denied tool never runs, through Agent Framework's real function-calling loop. Offline."""

import pytest
from vsl_core.exceptions import AutomationDeniedException, InvariantViolation
from vsl_core.ledger import LedgerEntryType, VerbaLedger

from refund_agent import domain, governance, providers
from refund_agent.agent import RefundAgent, ScriptedChatClient


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(governance, "ledger", VerbaLedger())
    monkeypatch.setattr(domain, "refund_book", domain.RefundBook())
    providers.payment.refunds.clear()
    providers.smtp.outbox.clear()


# ---- each gate on its own ---------------------------------------------------------------------

async def test_refund_rule_passes_a_refund_within_the_order():
    await governance.refund_rule_gate({"order_id": "A100", "amount": 120.0})


@pytest.mark.parametrize(
    ("candidate", "why"),
    [
        ({"order_id": "A100", "amount": 121.0}, "more than the order total"),
        ({"order_id": "A300", "amount": 10.0}, "a final-sale order"),
        ({"order_id": "A100", "amount": 0}, "nothing to refund"),
        ({"order_id": "Z999", "amount": 10.0}, "an unknown order"),
        ({"order_id": "A100", "amount": "lots"}, "an amount that isn't a number"),
    ],
)
async def test_refund_rule_blocks_and_enters_the_terminal_state(candidate, why):
    with pytest.raises(InvariantViolation) as caught:
        await governance.refund_rule_gate(candidate)
    assert caught.value.terminal_state_name == "refund-escalated", why


async def test_refund_rule_counts_earlier_refunds():
    domain.refund_book.record("A100", 100.0)
    with pytest.raises(InvariantViolation):
        await governance.refund_rule_gate({"order_id": "A100", "amount": 50.0})


async def test_budget_gate_is_a_measured_ratio_against_1_1():
    await governance.refund_budget_gate({"order_id": "A100", "amount": 120.0})  # 1000 / 120 = 8.3
    domain.refund_book.record("A100", 120.0)
    with pytest.raises(AutomationDeniedException):
        await governance.refund_budget_gate({"order_id": "A200", "amount": 900.0})  # 880 / 900 = 0.98


async def test_email_gate_allows_only_the_customer_on_file():
    await governance.email_gate({"order_id": "A100", "to": "Alice@Example.com"})
    with pytest.raises(InvariantViolation):
        await governance.email_gate({"order_id": "A100", "to": "mallory@evil.example"})


async def test_ownership_gate_allows_only_the_customers_own_orders():
    governance.customer_email.set("Alice@Example.com")
    await governance.ownership_gate({"order_id": "A100"})
    for order_id in ("A200", "Z999"):
        with pytest.raises(InvariantViolation) as caught:
            await governance.ownership_gate({"order_id": order_id})
        assert caught.value.terminal_state_name is None  # refused, not a reason to stop the agent


def test_every_gate_declares_f1_and_no_f2():
    for gate in (
        governance.order_belongs_to_customer, governance.refund_within_order,
        governance.refund_budget, governance.email_to_customer,
    ):
        assert gate.assurance_basis.f1_pre_commitment is True
        assert gate.assurance_basis.f2_modification.name == "NONE"


# ---- the agent: a denied tool never runs, and the model never sees the denial ------------------

async def test_an_allowed_refund_moves_money_and_emails_the_customer():
    reply = await RefundAgent().handle("alice@example.com", "Please refund order A100.")
    assert providers.payment.refunds == [("A100", 120.0)]
    assert providers.smtp.outbox[0][1] == "alice@example.com"
    assert reply.startswith("Done.")


async def test_a_denied_refund_never_reaches_the_payment_call_or_the_model():
    domain.refund_book.record("A100", 120.0)  # 880 left today
    client = ScriptedChatClient()
    reply = await RefundAgent(client).handle("bob@example.com", "Refund order A200 in full.")
    assert providers.payment.refunds == [] and providers.smtp.outbox == []
    assert "refund-budget-headroom" in reply


async def test_an_email_to_a_stranger_is_never_sent():
    reply = await RefundAgent().handle("dave@example.com", "Refund order A400 and send the receipt to mallory@evil.example.")
    assert providers.payment.refunds == [("A400", 40.0)]
    assert providers.smtp.outbox == []
    assert reply.startswith("Refunded, but")


async def test_a_hard_rule_violation_stops_the_agent_until_a_human_re_enables_it():
    refund_agent = RefundAgent()
    await refund_agent.handle("carol@example.com", "Refund order A300.")
    paused = await refund_agent.handle("bob@example.com", "Refund order A200 for $100.")
    assert "paused" in paused and providers.payment.refunds == []

    refund_agent.re_enable(authorised_by="j.smith", root_cause="final-sale refund requested", fix="warn up front")
    await refund_agent.handle("bob@example.com", "Refund order A200 for $100.")
    assert providers.payment.refunds == [("A200", 100.0)]

    kinds = [e.entry_type for e in governance.ledger.store.all_entries()]
    assert LedgerEntryType.TERMINAL in kinds
    assert kinds.index(LedgerEntryType.HUMAN_AUTHORISED_TRANSITION) < kinds.index(LedgerEntryType.RE_ENABLEMENT)
    assert governance.ledger.verify_integrity()


async def test_every_gate_decision_is_recorded_under_one_decision_id():
    await RefundAgent().handle("alice@example.com", "Please refund order A100.")
    entries = list(governance.ledger.store.all_entries())
    assert [e.payload.get("primitive_name") for e in entries] == [
        "order-belongs-to-customer", "refund-within-order", "refund-budget-headroom",
        "order-belongs-to-customer", "email-to-customer-on-file",
    ]
    assert len({e.decision_id for e in entries}) == 1


async def test_another_customers_order_is_never_read_refunded_or_emailed():
    agent = RefundAgent()
    for request in ("What's the status of order A200?", "Refund order A200 for $10.", "Email the receipt for order A200."):
        reply = await agent.handle("alice@example.com", request)
        assert reply == "I can only help with orders on your own account."
    assert providers.payment.refunds == [] and providers.smtp.outbox == []
    assert agent.halted_reason is None

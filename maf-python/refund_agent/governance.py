"""The gates in front of this agent's tools, and the ledger that records every decision.

One gate per decision point:

- Every tool touches an order, and every request comes from a signed-in customer, so one hard rule
  runs before each of them:
  * order-belongs-to-customer (Invariant): an order is only looked up, refunded or emailed about for
    the customer it belongs to. Looking an order up only reads, but what it reads (another
    customer's order and email address) would leak, so it is gated too.
- issue_refund moves money and can't be cleanly undone, so it has two gates:
  * refund-within-order (Invariant): a hard rule. Never refund more than is left on the order (its
    total less earlier refunds), or an order that isn't refundable. A violation enters the
    refund-escalated Terminal State, and only a named human can bring the agent back (re_enable).
  * refund-budget-headroom (PreNode): a measured score against a threshold. Gamma is the budget
    left today divided by the amount, a ratio where 1.0 is break-even; it must be above 1.1.
- send_email writes to a person outside the system, so it has one hard rule:
  * email-to-customer-on-file (Invariant): only to the address on the order, never one the model
    introduced.

Each gate runs before its tool (F1 = True) and only allows or blocks it, with no model involved at
the guarded call (F2 = NONE). That derives LOW assurance: an honest description of influence on a
model, not of how well the gates enforce. Deciding whether an action happens at all is F1.

The gates evaluate the tool call's own arguments (vsl_maf.tool_arguments) and look up the rest
(the order, today's refunds, the signed-in customer) themselves. The customer never comes from the
model: it is set by RefundAgent.handle from your authentication.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from vsl_core.constructs import Invariant, PreNode, TerminalState
from vsl_core.governance import GovernanceAuthority, request_re_enablement
from vsl_core.identity import Evidence, IdentityKey, Instance
from vsl_core.ledger import JsonlLedgerStore, LedgerEntryType, VerbaLedger
from vsl_core.metrics import AssuranceBasis, F2Modification, GammaEstimate
from vsl_maf import MAFAdapter

from . import domain

AGENT_ID = "refund-agent"

# Runs before the tool (F1); only allows or blocks it, no model at the guarded call (F2 NONE).
NO_MODEL_AT_GUARDED_CALL = AssuranceBasis(f1_pre_commitment=True, f2_modification=F2Modification.NONE)

REFUND_ESCALATED = TerminalState(
    name="refund-escalated",
    description="Refunds stop until a refunds supervisor reviews the case and re-enables the agent.",
    entry_conditions=("refund_outside_order",),
)

# Every gate decision is written here. Call use_ledger() to keep them in a file.
ledger = VerbaLedger()

# The current request's decision id, so all of one request's records can be read together.
decision_id: ContextVar[str] = ContextVar("decision_id", default="")

# The signed-in customer making the current request (from your authentication, never from the model).
customer_email: ContextVar[str] = ContextVar("customer_email", default="")


def use_ledger(path: str | Path) -> VerbaLedger:
    global ledger
    ledger = VerbaLedger(JsonlLedgerStore(path, fsync=True))
    return ledger


def _amount(candidate: dict[str, Any]) -> float:
    try:
        return float(candidate.get("amount", 0.0))
    except (TypeError, ValueError):
        return 0.0


async def order_is_the_customers(candidate: dict[str, Any]) -> bool:
    order = domain.ORDERS.get(str(candidate.get("order_id", "")))
    holds = order is not None and order.customer_email.lower() == customer_email.get().strip().lower()
    ledger.write_gate_decision(
        identity_key=AGENT_ID, primitive_name="order-belongs-to-customer", allowed=holds, decision_id=decision_id.get()
    )
    return holds


async def refund_is_within_order(candidate: dict[str, Any]) -> bool:
    order = domain.ORDERS.get(str(candidate.get("order_id", "")))
    amount = _amount(candidate)
    holds = (
        order is not None
        and order.refundable
        and 0 < amount <= order.total - domain.refund_book.refunded(order.order_id)
    )
    ledger.write_gate_decision(
        identity_key=AGENT_ID, primitive_name="refund-within-order", allowed=holds, decision_id=decision_id.get()
    )
    return holds


async def refund_budget_headroom(candidate: dict[str, Any]) -> GammaEstimate:
    amount = _amount(candidate)
    # A measured ratio: budget left / amount. 0.0 for a missing or bad amount, so the gate fails closed.
    estimate = GammaEstimate(gamma_hat=domain.refund_book.budget_remaining / amount if amount > 0 else 0.0)
    ledger.write_gate_decision(
        identity_key=AGENT_ID,
        primitive_name="refund-budget-headroom",
        estimate=estimate,
        gamma_threshold=1.1,
        decision_id=decision_id.get(),
    )
    return estimate


async def email_is_to_customer_on_file(candidate: dict[str, Any]) -> bool:
    order = domain.ORDERS.get(str(candidate.get("order_id", "")))
    holds = order is not None and str(candidate.get("to", "")).strip().lower() == order.customer_email.lower()
    ledger.write_gate_decision(
        identity_key=AGENT_ID, primitive_name="email-to-customer-on-file", allowed=holds, decision_id=decision_id.get()
    )
    return holds


order_belongs_to_customer = Invariant(
    name="order-belongs-to-customer",
    description="Only look up, refund or email about an order for the customer it belongs to.",
    rule=order_is_the_customers,
    assurance_basis=NO_MODEL_AT_GUARDED_CALL,
)

refund_within_order = Invariant(
    name="refund-within-order",
    description="Never refund more than is left on the order, or an order that is not refundable.",
    rule=refund_is_within_order,
    assurance_basis=NO_MODEL_AT_GUARDED_CALL,
    on_violation=REFUND_ESCALATED,
)

refund_budget = PreNode(
    name="refund-budget-headroom",
    description="A refund must leave headroom in today's refund budget: budget left / amount above 1.1.",
    monitor=refund_budget_headroom,
    assurance_basis=NO_MODEL_AT_GUARDED_CALL,
    gamma_threshold=1.1,
)

email_to_customer = Invariant(
    name="email-to-customer-on-file",
    description="Only email the customer on the order, never an address the model introduced.",
    rule=email_is_to_customer_on_file,
    assurance_basis=NO_MODEL_AT_GUARDED_CALL,
)

adapter = MAFAdapter(identity_key=AGENT_ID)
ownership_gate = adapter.compile_invariant(order_belongs_to_customer)
refund_rule_gate = adapter.compile_invariant(refund_within_order)
refund_budget_gate = adapter.compile_pre_node(refund_budget)
email_gate = adapter.compile_invariant(email_to_customer)


# ---- Terminal State and the way back --------------------------------------------------------

refunds_supervisor = GovernanceAuthority(name="Refunds Supervisor", escalation_contact="refunds-supervisor@example.com")


def new_instance() -> Instance:
    return Instance(instance_id=str(uuid.uuid4()), identity_key=IdentityKey(AGENT_ID))


def enter_terminal_state(instance: Instance, reason: str) -> None:
    """vsl-core never writes this for you: catching the violation and recording it is your code."""
    ledger.write(
        LedgerEntryType.TERMINAL,
        identity_key=AGENT_ID,
        instance_id=instance.instance_id,
        decision_id=decision_id.get(),
        payload={"terminal_state": REFUND_ESCALATED.name, "reason": reason},
    )


def re_enable(instance: Instance, *, authorised_by: str, root_cause: str, fix: str) -> Instance:
    """The only way out of refund-escalated: a named human, with evidence, through vsl-core."""
    evidence = Evidence(root_cause_analysis=root_cause, specification_update_proposal=fix)
    new, transition = request_re_enablement(instance, refunds_supervisor, authorised_by=authorised_by, evidence=evidence)
    ledger.write(
        LedgerEntryType.HUMAN_AUTHORISED_TRANSITION,
        identity_key=AGENT_ID,
        instance_id=instance.instance_id,
        payload={"authority": transition.authority.name, "authorised_by": transition.authorised_by,
                 "evidence_hash": transition.authorisation_evidence_hash},
    )
    ledger.write(
        LedgerEntryType.RE_ENABLEMENT,
        identity_key=AGENT_ID,
        instance_id=new.instance_id,
        caused_by=instance.instance_id,
        payload={"predecessor_instance_id": instance.instance_id},
    )
    return new

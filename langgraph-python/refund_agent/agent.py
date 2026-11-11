"""The refund agent as a LangGraph graph, with a VSL gate node in front of every action.

    START -> plan -> ownership_gate -> dispatch -> refund_rule_gate -> refund_budget_gate -> issue_refund
                                                                     -> email_gate -> send_email -> END
                                         dispatch -> email_gate (a receipt only)
                                         dispatch -> answer (a lookup only) -> END
    any gate that denies -> blocked -> END

Each gate is its own node (gated_node), and route_on_denial sends the run to "blocked" when the
gate denies, so the action node is reachable only through gates that passed. Keep that shape when
you add actions: one gate node per decision point, a route_on_denial edge after it, and the action
node after that.

The "model" here is ScriptedModel, so the starter runs offline. To use a real model, have it return
the same plan (an action, an order id, an amount, a recipient) as a structured output or a tool call;
the gates do not change.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from vsl_core.exceptions import InvariantViolation
from vsl_langgraph import gated_node, route_on_denial

from . import governance
from .governance import email_gate, ownership_gate, refund_budget_gate, refund_rule_gate
from .domain import DAILY_REFUND_BUDGET, EmailCandidate, OwnershipCandidate, RefundCandidate
from .tools import issue_refund, lookup_order, send_email


class RefundState(TypedDict, total=False):
    customer_email: str  # the signed-in customer making the request
    request: str
    decision_id: str
    action: str  # "refund" | "email" | "lookup"
    order_id: str
    amount: float
    email_to: str
    already_refunded: float
    budget_remaining: float
    refund_id: str
    reply: str
    vsl_denial: Any


@dataclass
class RefundBudget:
    """Today's refunds. In production this lives in your database, not in memory."""

    limit: float = DAILY_REFUND_BUDGET
    by_order: dict[str, float] = field(default_factory=dict)

    @property
    def remaining(self) -> float:
        return self.limit - sum(self.by_order.values())

    def record(self, order_id: str, amount: float) -> None:
        self.by_order[order_id] = self.by_order.get(order_id, 0.0) + amount


budget = RefundBudget()


class ScriptedModel:
    """Stands in for a model: turns a request into a plan. Deterministic, so tests can rely on it."""

    _order = re.compile(r"\b([A-Z]\d{3})\b")
    _amount = re.compile(r"\$(\d+(?:\.\d+)?)")
    _email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

    def plan(self, request: str) -> dict[str, Any]:
        order_id = (self._order.search(request) or [None, ""])[1]
        amount = self._amount.search(request)
        email = self._email.search(request)
        lowered = request.lower()
        if "refund" in lowered:
            plan: dict[str, Any] = {"action": "refund", "order_id": order_id}
            order = lookup_order(order_id)
            plan["amount"] = float(amount.group(1)) if amount else (order.total if order else 0.0)
            if email:
                plan["email_to"] = email.group(0)
            return plan
        if "receipt" in lowered or "email" in lowered:
            return {"action": "email", "order_id": order_id, "email_to": email.group(0) if email else ""}
        return {"action": "lookup", "order_id": order_id}


model = ScriptedModel()


# ---- nodes --------------------------------------------------------------------------------

def plan(state: RefundState) -> RefundState:
    planned = model.plan(state["request"])
    order = lookup_order(planned.get("order_id", ""))
    if planned.get("action") in ("refund", "email") and "email_to" not in planned and order is not None:
        planned["email_to"] = order.customer_email
    already = budget.by_order.get(planned.get("order_id", ""), 0.0)
    return {**planned, "already_refunded": already, "budget_remaining": budget.remaining}


def route_action(state: RefundState) -> str:
    return state.get("action", "lookup")


def passthrough(state: RefundState) -> RefundState:
    return {}


def _ownership_candidate(state: RefundState) -> OwnershipCandidate:
    return OwnershipCandidate(lookup_order(state.get("order_id", "")), state.get("customer_email", ""), state["decision_id"])


def _refund_candidate(state: RefundState) -> RefundCandidate:
    order = lookup_order(state.get("order_id", ""))
    if order is None:
        raise InvariantViolation(reason="unknown order", identity_key=governance.AGENT_ID)
    return RefundCandidate(
        order,
        float(state.get("amount", 0.0)),
        float(state.get("already_refunded", 0.0)),
        float(state["budget_remaining"]),
        state["decision_id"],
    )


def _email_candidate(state: RefundState) -> EmailCandidate:
    order = lookup_order(state.get("order_id", ""))
    if order is None:
        raise InvariantViolation(reason="unknown order", identity_key=governance.AGENT_ID)
    return EmailCandidate(order, state.get("email_to", ""), state["decision_id"])


def do_refund(state: RefundState) -> RefundState:
    refund_id = issue_refund(state["order_id"], state["amount"])
    budget.record(state["order_id"], state["amount"])
    return {"refund_id": refund_id}


def do_send_email(state: RefundState) -> RefundState:
    if state.get("refund_id"):
        body = f"We refunded ${state['amount']:.2f} for order {state['order_id']} (ref {state['refund_id']})."
    else:
        body = f"Here is the receipt for order {state['order_id']}."
    send_email(state["email_to"], f"Order {state['order_id']}", body)
    return {"reply": f"Done. {body} A confirmation went to {state['email_to']}."}


def answer(state: RefundState) -> RefundState:
    order = lookup_order(state.get("order_id", ""))
    if order is None:
        return {"reply": "I could not find that order."}
    return {"reply": f"Order {order.order_id}: ${order.total:.2f}, {'refundable' if order.refundable else 'final sale'}."}


def blocked(state: RefundState) -> RefundState:
    denial = state.get("vsl_denial")
    if getattr(denial, "invariant_name", None) == "order-belongs-to-customer":
        return {"reply": "I can only help with orders on your own account."}
    if state.get("refund_id"):
        return {"reply": f"Refunded, but the confirmation email was not sent: {denial.reason}."}
    return {"reply": f"Not done: {denial.reason}. A person will review it."}


def build_graph():
    graph = StateGraph(RefundState)
    graph.add_node("plan", plan)
    graph.add_node("ownership_gate", gated_node(ownership_gate, passthrough, candidate_input_fn=_ownership_candidate))
    graph.add_node("dispatch", passthrough)
    graph.add_node("refund_rule_gate", gated_node(refund_rule_gate, passthrough, candidate_input_fn=_refund_candidate))
    graph.add_node("refund_budget_gate", gated_node(refund_budget_gate, passthrough, candidate_input_fn=_refund_candidate))
    graph.add_node("issue_refund", do_refund)
    graph.add_node("email_gate", gated_node(email_gate, passthrough, candidate_input_fn=_email_candidate))
    graph.add_node("send_email", do_send_email)
    graph.add_node("answer", answer)
    graph.add_node("blocked", blocked)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "ownership_gate")
    graph.add_conditional_edges("ownership_gate", route_on_denial("blocked", "dispatch"))
    graph.add_conditional_edges("dispatch", route_action, {"refund": "refund_rule_gate", "email": "email_gate", "lookup": "answer"})
    graph.add_conditional_edges("refund_rule_gate", route_on_denial("blocked", "refund_budget_gate"))
    graph.add_conditional_edges("refund_budget_gate", route_on_denial("blocked", "issue_refund"))
    graph.add_edge("issue_refund", "email_gate")
    graph.add_conditional_edges("email_gate", route_on_denial("blocked", "send_email"))
    graph.add_edge("send_email", END)
    graph.add_edge("answer", END)
    graph.add_edge("blocked", END)
    return graph.compile()


@dataclass
class RefundAgent:
    """Runs requests through the graph, and stops after a hard-rule violation until a human says so."""

    instance: Any = field(default_factory=governance.new_instance)
    halted_reason: Optional[str] = None
    graph: Any = field(default_factory=build_graph)

    async def handle(self, customer_email: str, request: str) -> str:
        """`customer_email` is the signed-in customer, from your authentication, never from the request."""
        if self.halted_reason:
            return f"Refunds are paused pending supervisor review ({self.halted_reason})."
        decision_id = str(uuid.uuid4())
        state = await self.graph.ainvoke({"customer_email": customer_email, "request": request, "decision_id": decision_id})
        denial = state.get("vsl_denial")
        if isinstance(denial, InvariantViolation) and denial.terminal_state_name:
            # vsl-core raised; recording the Terminal State and stopping is this code's job.
            governance.enter_terminal_state(self.instance, denial.reason, decision_id)
            self.halted_reason = denial.reason
        return state["reply"]

    def re_enable(self, *, authorised_by: str, root_cause: str, fix: str) -> None:
        self.instance = governance.re_enable(self.instance, authorised_by=authorised_by, root_cause=root_cause, fix=fix)
        self.halted_reason = None

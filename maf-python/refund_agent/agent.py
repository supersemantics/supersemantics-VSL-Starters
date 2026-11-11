"""The refund agent on the Microsoft Agent Framework, with VSL gates as function middleware.

Agent Framework calls function middleware before every tool call. Each VSLFunctionMiddleware below
is scoped to one tool (function_name) and runs that tool's gate first; the tool runs only if the
gate passes. With the default on_denial="abort", a denial stops the whole run and raises
VSLGateDenied to this code. The model is never handed a tool error to argue with.

The "model" here is ScriptedChatClient, so the starter runs offline: it asks for tool calls the way a
real model does, through Agent Framework's own function-calling loop. To use a real model, pass any
chat client instead (Azure OpenAI, Foundry, OpenAI); the tools and gates do not change.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
)
from vsl_core.exceptions import InvariantViolation
from vsl_maf import VSLFunctionMiddleware, VSLGateDenied, tool_arguments

from . import domain, governance
from .governance import email_gate, ownership_gate, refund_budget_gate, refund_rule_gate
from .tools import issue_refund, lookup_order, send_email

INSTRUCTIONS = "You handle refunds. Look up orders, refund them, and email the customer a confirmation."


class ScriptedChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Stands in for a model: plans tool calls from the request, one per turn, then answers.
    Deterministic, so tests can rely on it. (The layer order is the one Agent Framework's own
    clients use: function invocation, then chat middleware.)"""

    _order = re.compile(r"\b([A-Z]\d{3})\b")
    _amount = re.compile(r"\$(\d+(?:\.\d+)?)")
    _email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

    def _plan(self, request: str) -> list[tuple[str, dict[str, Any]]]:
        order_id = (self._order.search(request) or [None, ""])[1]
        order = domain.ORDERS.get(order_id)
        explicit_to = self._email.search(request)
        to = explicit_to.group(0) if explicit_to else (order.customer_email if order else "")
        lowered = request.lower()
        if "refund" in lowered:
            amount = self._amount.search(request)
            value = float(amount.group(1)) if amount else (order.total if order else 0.0)
            return [
                ("issue_refund", {"order_id": order_id, "amount": value}),
                ("send_email", {"order_id": order_id, "to": to, "subject": f"Refund for order {order_id}",
                                "body": f"We refunded ${value:.2f} for order {order_id}."}),
            ]
        if "receipt" in lowered or "email" in lowered:
            return [("send_email", {"order_id": order_id, "to": to, "subject": f"Order {order_id}",
                                    "body": f"Here is the receipt for order {order_id}."})]
        return [("lookup_order", {"order_id": order_id})]

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        request = next((m.text for m in messages if "user" in str(m.role).lower()), "")
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        steps = self._plan(request)
        if len(results) < len(steps):
            name, arguments = steps[len(results)]
            call = Content.from_function_call(call_id=f"call-{len(results) + 1}", name=name, arguments=arguments)
            return ChatResponse(messages=[Message(role="assistant", contents=[call])])
        summary = " ".join(str(r.result) for r in results)
        return ChatResponse(messages=[Message(role="assistant", contents=[f"Done. {summary}"])])


def build_agent(client: BaseChatClient | None = None) -> Agent:
    """The agent and its gates. Each middleware runs one gate before one tool, in the order listed:
    ownership first for every tool, then the refund's hard rule and budget, or the email rule."""
    return Agent(
        client=client or ScriptedChatClient(),
        name="RefundAgent",
        instructions=INSTRUCTIONS,
        tools=[lookup_order, issue_refund, send_email],
        middleware=[
            VSLFunctionMiddleware(ownership_gate, candidate_input_fn=tool_arguments, function_name="lookup_order"),
            VSLFunctionMiddleware(ownership_gate, candidate_input_fn=tool_arguments, function_name="issue_refund"),
            VSLFunctionMiddleware(ownership_gate, candidate_input_fn=tool_arguments, function_name="send_email"),
            VSLFunctionMiddleware(refund_rule_gate, candidate_input_fn=tool_arguments, function_name="issue_refund"),
            VSLFunctionMiddleware(refund_budget_gate, candidate_input_fn=tool_arguments, function_name="issue_refund"),
            VSLFunctionMiddleware(email_gate, candidate_input_fn=tool_arguments, function_name="send_email"),
        ],
    )


class RefundAgent:
    """Runs requests through the agent, and stops after a hard-rule violation until a human says so."""

    def __init__(self, client: BaseChatClient | None = None) -> None:
        self.agent = build_agent(client)
        self.instance = governance.new_instance()
        self.halted_reason: str | None = None

    async def handle(self, customer_email: str, request: str) -> str:
        """`customer_email` is the signed-in customer, from your authentication, never from the request."""
        if self.halted_reason:
            return f"Refunds are paused pending supervisor review ({self.halted_reason})."
        governance.decision_id.set(str(uuid.uuid4()))
        governance.customer_email.set(customer_email)
        try:
            result = await self.agent.run(request)
            return result.text
        except VSLGateDenied as denied:
            reason = denied.denial.reason
            if getattr(denied.denial, "invariant_name", None) == "order-belongs-to-customer":
                return "I can only help with orders on your own account."
            if isinstance(denied.denial, InvariantViolation) and denied.denial.terminal_state_name:
                # vsl-core raised; recording the Terminal State and stopping is this code's job.
                governance.enter_terminal_state(self.instance, reason)
                self.halted_reason = reason
            if denied.function_name == "send_email":
                return f"Refunded, but the confirmation email was not sent: {reason}."
            return f"Not done: {reason} ('{denied.function_name}' did not run). A person will review it."

    def re_enable(self, *, authorised_by: str, root_cause: str, fix: str) -> None:
        self.instance = governance.re_enable(self.instance, authorised_by=authorised_by, root_cause=root_cause, fix=fix)
        self.halted_reason = None

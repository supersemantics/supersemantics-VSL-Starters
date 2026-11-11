# Refund agent: Microsoft Agent Framework (Python) with VSL gates

A small, complete agent to start from: a customer-support refund agent on the
[Microsoft Agent Framework](https://github.com/microsoft/agent-framework), with a VSL gate in front
of every tool. It runs offline, with no API keys, through the Agent Framework's own function-calling
loop, and its tests prove each gate.

Built with [vsl-core](https://github.com/supersemantics/VSL-Core) and
[vsl-maf](https://github.com/supersemantics/VSL-Microsoft-agent-framework).

## Run it

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m refund_agent            # seven requests through the agent, then a supervisor re-enables it
pytest                            # the gates, one by one, and the agent end to end
```

Every gate decision is written to `refund-ledger.jsonl`, a hash-chained ledger; the run ends by
checking it is intact.

## What it does

Every request comes from a signed-in customer (`agent.handle(customer_email, request)`; the customer
comes from your authentication, never from the model or the request text).

| Tool | Effect | Gates before it, in order |
|---|---|---|
| `lookup_order` | reads an order, including its customer's email | `order-belongs-to-customer` (Invariant) |
| `issue_refund` | moves money | `order-belongs-to-customer`, `refund-within-order` (Invariants), `refund-budget-headroom` (PreNode) |
| `send_email` | writes to a person | `order-belongs-to-customer`, `email-to-customer-on-file` (Invariants) |

- **order-belongs-to-customer**: an order is only looked up, refunded or emailed about for the
  customer it belongs to. A lookup only reads, but what it reads would leak another customer's
  order, so it is gated too.
- **refund-within-order** is a hard rule: never refund more than is left on the order, or an order
  that isn't refundable. A violation enters the `refund-escalated` Terminal State, and the agent
  stops refunding until a named human re-enables it (`RefundAgent.re_enable`, through vsl-core's
  `request_re_enablement`).
- **refund-budget-headroom** is a measured score: Gamma = budget left today / amount, a ratio where
  1.0 is break-even, and it must be above 1.1.
- **email-to-customer-on-file**: the confirmation goes only to the address on the order, never to
  one the model introduced.

All four run before their tool (F1 = True) and only allow or block it, with no model involved at the
guarded call (F2 = NONE), which derives LOW. That describes influence on a model, not how well the
gates enforce: deciding whether an action happens at all is F1.

## How the gates are wired

```python
Agent(
    client=...,
    tools=[lookup_order, issue_refund, send_email],
    middleware=[
        VSLFunctionMiddleware(ownership_gate, candidate_input_fn=tool_arguments, function_name="lookup_order"),
        VSLFunctionMiddleware(ownership_gate, candidate_input_fn=tool_arguments, function_name="issue_refund"),
        ...
    ],
)
```

Each `VSLFunctionMiddleware` runs one gate before one tool, named by `function_name`; for one tool
they run in the order listed. With the default `on_denial="abort"`, a denial stops the whole run and
raises `VSLGateDenied` to your code: the tool never runs, and the model is never handed a tool error
to argue with. Name the tool in every middleware: a middleware without `function_name` gates every
tool, which is rarely what you mean.

```
refund_agent/
  domain.py       orders and today's refunds
  providers.py    offline stand-ins for the payment and email services
  tools.py        the tools: where each side effect happens
  governance.py   the gates, the Terminal State, the ledger, the way back
  agent.py        the agent, its middleware, and an offline stand-in for the model
tests/            each gate blocks and passes the right cases; denied tools never run
```

## Make it yours

1. **Find the decision points**: tools with an effect outside the process (money, messages, writes,
   deletes), and reads that could reach data the requester shouldn't see.
2. **Pick the primitive**: an Invariant for a rule that must never be broken; a PreNode for a measured
   score against a threshold. Give each a real `AssuranceBasis`, never a default.
3. **Gate each tool by name** with `VSLFunctionMiddleware(..., function_name="your_tool")`.
4. **Record every decision** with `write_gate_decision`, and handle a Terminal State yourself: vsl-core
   raises; catching it, recording it and stopping is your code.
5. **Add a test** that the gate blocks the case it should and passes the one it shouldn't.

To use a real model, pass any chat client to `RefundAgent(client)` (Azure OpenAI, Foundry, OpenAI);
the tools and gates don't change. A gate decides whether a tool runs; it doesn't govern what the
model says in its own reply. If the reply itself matters, put a gate before the run too (for example
a scope check on the request). To use real services, replace the stand-ins in `providers.py`.

## Notes

- The VSL packages aren't on PyPI yet, so `pyproject.toml` installs them from GitHub. vsl-core is
  named exactly as vsl-maf names it, because pip refuses two different URLs for one package; today
  that resolves to vsl-core 0.3.0.

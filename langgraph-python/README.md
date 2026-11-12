# Refund agent: LangGraph (Python) with VSL gates

A small, complete agent to start from: a customer-support refund agent on
[LangGraph](https://github.com/langchain-ai/langgraph), with a VSL gate in front of every action
that has a real-world effect. It runs offline, with no API keys, and its tests prove each gate.

Built with [vsl-core](https://github.com/supersemantics/VSL-Core) and
[vsl-langgraph](https://github.com/supersemantics/VSL-Langgraph).

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
comes from your authentication, never from the request text).

| Action | Effect | Gate(s) before it |
|---|---|---|
| look up an order | reads an order, including its customer's email | `order-belongs-to-customer` (Invariant) |
| `issue_refund` | moves money | `order-belongs-to-customer`, `refund-within-order` (Invariants), then `refund-budget-headroom` (PreNode) |
| `send_email` | writes to a person | `order-belongs-to-customer`, then `email-to-customer-on-file` (Invariants) |

- **order-belongs-to-customer**: an order is only looked up, refunded or emailed about for the
  customer it belongs to. A lookup only reads, but what it reads would leak another customer's
  order, so it is gated too. A refusal isn't a fault in the agent: it answers "I can only help with
  orders on your own account" and carries on.
- **refund-within-order** is a hard rule: never refund more than is left on the order, or an order
  that isn't refundable. A violation enters the `refund-escalated` Terminal State and the agent
  stops refunding until a named human re-enables it (`RefundAgent.re_enable`, through vsl-core's
  `request_re_enablement`).
- **refund-budget-headroom** is a measured score: Gamma = budget left today / amount, a ratio where
  1.0 is break-even, and it must be above 1.1.
- **email-to-customer-on-file**: the confirmation goes only to the address on the order, never to
  one the model introduced.

All four run before their action (F1 = True) and only allow or block it, with no model involved at
the guarded call (F2 = NONE), which derives LOW. That describes influence on a model, not how well the
gates enforce: deciding whether an action happens at all is F1.

## How it's laid out

```
refund_agent/
  domain.py       orders, the daily budget, what each gate evaluates
  providers.py    offline stand-ins for the payment and email services
  tools.py        the actions: where each side effect happens
  governance.py   the gates, the Terminal State, the ledger, the way back
  agent.py        the LangGraph graph: a gate node before every action
tests/            each gate blocks and passes the right cases; denied actions never run
```

The graph puts each gate in its own node and routes on its decision:

```
START -> plan -> ownership_gate -> dispatch -> refund_rule_gate -> refund_budget_gate -> issue_refund
                                             -> email_gate -> send_email -> END
                                 dispatch -> email_gate (a receipt only);  dispatch -> answer (a lookup only)
any gate that denies -> blocked -> END
```

`gated_node` runs the gate before the node; on a denial the node never runs and `route_on_denial`
sends the run to `blocked`. So an action node is reachable only through gates that passed. Keep that
shape, with node names and routes written as literals, so the wiring reads plainly in the code.

## Make it yours

1. **Find the decision points**: the actions with an effect outside the process (money, messages,
   writes, deletes), and reads that could reach data the requester shouldn't see. A read with no
   such scope implication doesn't need a gate.
2. **Pick the primitive**: an Invariant for a rule that must never be broken; a PreNode for a
   measured score against a threshold. Give each a real `AssuranceBasis`, never a default.
3. **Put the side effect in `tools.py`** and a gate node in front of it in `agent.py`, with a
   `route_on_denial` edge after the gate.
4. **Record every decision** with `write_gate_decision`, and handle a Terminal State yourself:
   vsl-core raises; catching it, recording it and stopping is your code.
5. **Add a test** that the gate blocks the case it should and passes the one it shouldn't.

To use a real model, have the `plan` node call it and return the same plan (an action, an order id,
an amount, a recipient), for example as structured output. To use real services, replace the stand-ins
in `providers.py` (for example `smtp = smtplib.SMTP("mail.example.com")`). The gates don't change.

## Notes

- The VSL packages aren't on PyPI yet, so `pyproject.toml` installs them from GitHub, pinned to
  release tags: vsl-core `v0.3.0` and vsl-langgraph `v0.1.1`. vsl-core is named exactly as
  vsl-langgraph names it, tag included, because pip refuses two different URLs for one package.
- vsl-langgraph's compiled gates report their identity as `vsl-langgraph`; this starter records its
  own decisions under `refund-agent` in the ledger.

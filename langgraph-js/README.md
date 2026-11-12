# Refund agent: LangGraph.js (TypeScript) with VSL gates

A small, complete agent to start from: a customer-support refund agent on
[LangGraph.js](https://github.com/langchain-ai/langgraphjs), with a VSL gate in front of every action
that has a real-world effect. It runs offline, with no API keys, and its tests prove each gate.

Built with [vsl-corejs](https://github.com/supersemantics/vsl-corejs) and
[vsl-langgraphjs](https://github.com/supersemantics/vsl-langgraphjs).

## Run it

Needs Node.js 22.18 or later, which runs TypeScript directly: no build step.

```bash
npm install
npm start            # seven requests through the agent, then a supervisor re-enables it
npm test             # the gates, one by one, and the agent end to end
npm run typecheck
```

Every gate decision is written to `refund-ledger.jsonl`, a hash-chained ledger; the run ends by
checking it is intact.

## What it does

Every request comes from a signed-in customer (`agent.handle(customerEmail, request)`; the customer
comes from your authentication, never from the request text).

| Action | Effect | Gate(s) before it |
|---|---|---|
| look up an order | reads an order, including its customer's email | `order-belongs-to-customer` (Invariant) |
| `issueRefund` | moves money | `order-belongs-to-customer`, `refund-within-order` (Invariants), then `refund-budget-headroom` (PreNode) |
| `sendEmail` | writes to a person | `order-belongs-to-customer`, then `email-to-customer-on-file` (Invariants) |

- **order-belongs-to-customer**: an order is only looked up, refunded or emailed about for the
  customer it belongs to. A lookup only reads, but what it reads would leak another customer's
  order, so it is gated too.
- **refund-within-order** is a hard rule: never refund more than is left on the order, or an order
  that isn't refundable. A violation enters the `refund-escalated` Terminal State, and the agent
  stops refunding until a named human re-enables it (`RefundAgent.reEnable`, through vsl-core's
  `requestReEnablement`).
- **refund-budget-headroom** is a measured score: Gamma = budget left today / amount, a ratio where
  1.0 is break-even, and it must be above 1.1.
- **email-to-customer-on-file**: the confirmation goes only to the address on the order, never to
  one the model introduced.

All four run before their action (F1 = true) and only allow or block it, with no model involved at
the guarded call (F2 = NONE), which derives LOW. That describes influence on a model, not how well the
gates enforce: deciding whether an action happens at all is F1.

## How it's laid out

```
src/
  domain.ts       orders, today's refunds, what each gate evaluates
  providers.ts    offline stand-ins for the payment and email services
  tools.ts        the actions: where each side effect happens
  governance.ts   the gates, the Terminal State, the ledger, the way back
  agent.ts        the LangGraph graph: a gate node before every action
  main.ts         the demo
test/             each gate blocks and passes the right cases; denied actions never run
```

```
START -> plan -> ownershipGate -> dispatch -> refundRuleGate -> refundBudgetGate -> issueRefund
                                           -> emailGate -> sendEmail -> END
any gate that denies -> blocked -> END
```

`gatedNode` runs the gate before the node; on a denial the node never runs and `routeOnDenial` sends
the run to `blocked`. So an action node is reachable only through gates that passed. Keep that shape,
with node names and routes written as literals.

## Make it yours

1. **Find the decision points**: actions with an effect outside the process (money, messages,
   writes, deletes), and reads that could reach data the requester shouldn't see.
2. **Pick the primitive**: an Invariant for a rule that must never be broken; a PreNode for a measured
   score against a threshold. Give each a real `AssuranceBasis`, never a default.
3. **Put the side effect in `tools.ts`** and a gate node in front of it in `agent.ts`, with a
   `routeOnDenial` edge after the gate.
4. **Record every decision** with `writeGateDecision`, and handle a Terminal State yourself: vsl-core
   throws; catching it, recording it and stopping is your code.
5. **Add a test** that the gate blocks the case it should and passes the one it shouldn't.

To use a real model, have the `plan` node call it and return the same plan (an action, an order id,
an amount, a recipient), for example as structured output. To use real services, replace the
stand-ins in `providers.ts` (for example a nodemailer transport). The gates don't change.

## Notes

- The VSL packages aren't on npm yet, so `package.json` installs them from GitHub, pinned to
  release tags: vsl-corejs `v0.2.0` and vsl-langgraphjs `v0.1.1`. vsl-core is named exactly as
  vsl-langgraphjs names it, so npm installs one copy: the adapter recognises a denial with
  `instanceof`, which needs the same copy on both sides.
- vsl-langgraphjs's compiled gates report their identity as `vsl-langgraph`; this starter records its
  own decisions under `refund-agent` in the ledger.

# VSL Starters

Small, complete agents to start from, one per framework, each with a VSL gate in front of every
action that has a real-world effect. They all build the same customer-support refund agent, so you
can compare frameworks line by line. Each runs offline, with no API keys, and its tests prove every
gate.

| Starter | Framework | VSL packages |
|---|---|---|
| [`langgraph-python`](langgraph-python) | LangGraph (Python) | [vsl-core](https://github.com/supersemantics/VSL-Core), [vsl-langgraph](https://github.com/supersemantics/VSL-Langgraph) |
| [`langgraph-js`](langgraph-js) | LangGraph.js (TypeScript) | [vsl-corejs](https://github.com/supersemantics/vsl-corejs), [vsl-langgraphjs](https://github.com/supersemantics/vsl-langgraphjs) |
| [`maf-python`](maf-python) | Microsoft Agent Framework (Python) | [vsl-core](https://github.com/supersemantics/VSL-Core), [vsl-maf](https://github.com/supersemantics/VSL-Microsoft-agent-framework) |
| [`maf-dotnet`](maf-dotnet) | Microsoft Agent Framework (.NET) | [VSL-Core.NET](https://github.com/supersemantics/VSL-Core.NET), [VSL-Microsoft-agent-framework.NET](https://github.com/supersemantics/VSL-Microsoft-agent-framework.NET) |

## The agent

A signed-in customer asks about an order, asks for a refund, or asks for a receipt. The agent can
look an order up, refund it, and email the customer. Four gates decide whether it may:

| Gate | Kind | Before | Why |
|---|---|---|---|
| `order-belongs-to-customer` | Invariant | every lookup, refund and email | a customer only sees and acts on their own orders |
| `refund-within-order` | Invariant, escalates to a Terminal State | every refund | never more than is left on the order, never a final-sale order |
| `refund-budget-headroom` | PreNode, Gamma above 1.1 | every refund | budget left today / amount: a measured ratio, 1.0 is break-even |
| `email-to-customer-on-file` | Invariant | every email | only to the address on the order, never one the model introduced |

A violation of `refund-within-order` stops refunds until a named human re-enables the agent, through
vsl-core's human-authorised re-enablement. Every gate decision is written to a hash-chained ledger.

Each demo runs the same seven requests: an allowed refund, another customer's order (refused), a
refund over budget (denied), a receipt sent to a stranger (blocked), a final-sale refund (the agent
stops), a refund while stopped (paused), and the same refund after a supervisor re-enables it.

## The pattern, whatever the framework

1. **Find the decision points**: actions with an effect outside the process (money, messages,
   writes, deletes), and reads that could reach data the requester shouldn't see.
2. **Pick the primitive**: an Invariant for a rule that must never be broken; a PreNode for a measured
   score against a threshold. Give each an honest `AssuranceBasis`: a gate that only allows or blocks,
   with no model at the guarded call, is F1 = true, F2 = NONE (LOW).
3. **Put the gate immediately before the action**, wired literally: a gate node and a
   route-on-denial edge in LangGraph, per-tool middleware named by tool in the Agent Framework.
4. **Record every decision** with `write_gate_decision` / `writeGateDecision` / `WriteGateDecision`,
   and handle a Terminal State in your own code.
5. **Test each gate**: it blocks the case it should and passes the one it shouldn't, and a denied
   action never runs.

More on the method: vsl-core's
[building-with-vsl-core](https://github.com/supersemantics/VSL-Core/blob/main/docs/building-with-vsl-core.md).

## License

MIT.

# Refund agent: Microsoft Agent Framework (.NET) with VSL gates

A small, complete agent to start from: a customer-support refund agent on the
[Microsoft Agent Framework](https://github.com/microsoft/agent-framework) for .NET, with a VSL gate
in front of every tool. It runs offline, with no API keys, through the Agent Framework's own
function-calling loop, and its tests prove each gate.

Built with [VSL-Core.NET](https://github.com/supersemantics/VSL-Core.NET) (`SuperSemantics.Vsl`) and
[VSL-Microsoft-agent-framework.NET](https://github.com/supersemantics/VSL-Microsoft-agent-framework.NET)
(`SuperSemantics.Vsl.AgentFramework`).

## Run it

Needs the .NET 10 SDK.

```bash
dotnet test RefundAgent.slnx                 # the gates, one by one, and the agent end to end
dotnet run --project src/RefundAgent         # seven requests, then a supervisor re-enables the agent
```

Until `SuperSemantics.Vsl` and `SuperSemantics.Vsl.AgentFramework` are on NuGet, build them into a
local folder once and restore from it:

```bash
git clone --recurse-submodules https://github.com/supersemantics/VSL-Microsoft-agent-framework.NET
dotnet pack VSL-Microsoft-agent-framework.NET/src/SuperSemantics.Vsl.AgentFramework -c Release -o ~/vsl-packages
dotnet pack VSL-Microsoft-agent-framework.NET/external/VSL-Core.NET/src/SuperSemantics.Vsl -c Release -o ~/vsl-packages
dotnet restore RefundAgent.slnx -p:RestoreAdditionalProjectSources=$HOME/vsl-packages
```

Every gate decision is written to `refund-ledger.jsonl`, a hash-chained ledger; the run ends by
checking it is intact.

## What it does

Every request comes from a signed-in customer (`runner.HandleAsync(customerEmail, request)`; the
customer comes from your authentication, never from the model or the request text).

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
  stops refunding until a named human re-enables it (`ReEnable`, through
  `Governance.RequestReEnablement`).
- **refund-budget-headroom** is a measured score: Gamma = budget left today / amount, a ratio where
  1.0 is break-even, and it must be above 1.1.
- **email-to-customer-on-file**: the confirmation goes only to the address on the order, never to
  one the model introduced.

All four run before their tool (F1 = true) and only allow or block it, with no model involved at the
guarded call (F2 = None), which derives Low. That describes influence on a model, not how well the
gates enforce: deciding whether an action happens at all is F1.

## How the gates are wired

```csharp
new ChatClientAgent(model, instructions: ..., tools: [lookupOrder, issueRefund, sendEmail])
    .AsBuilder()
    .UseVslGates([OwnershipGate], functionName: "lookup_order")
    .UseVslGates([OwnershipGate, RefundRuleGate, RefundBudgetGate], functionName: "issue_refund")
    .UseVslGates([OwnershipGate, EmailGate], functionName: "send_email")
    .Build();
```

`UseVslGates` runs its gates in the order listed before every call to the named tool. With the
default `VslDenialMode.Abort`, a denial stops the whole run and throws `VslGateDeniedException` to
your code: the tool never runs, and the model is never handed a tool error to argue with. Use one
`UseVslGates` per tool rather than several `UseVslGate` calls for the same tool: the Agent Framework
runs separately registered middleware last-registered first.

```
src/RefundAgent/
  Domain.cs               orders and today's refunds
  Providers.cs            offline stand-ins for the payment and email services
  Tools.cs                the tools: where each side effect happens
  Governance.cs           the gates, the Terminal State, the ledger, the way back
  RefundAgentFactory.cs   the agent and its gates, the runner, and an offline stand-in for the model
  Program.cs              the demo
tests/RefundAgent.Tests/  each gate blocks and passes the right cases; denied tools never run
```

## Make it yours

1. **Find the decision points**: tools with an effect outside the process (money, messages, writes,
   deletes), and reads that could reach data the requester shouldn't see.
2. **Pick the primitive**: an `Invariant` for a rule that must never be broken; a `PreNode` for a
   measured score against a threshold. Give each a real `AssuranceBasis`, never a default.
3. **Gate each tool by name** with `UseVslGates([...], functionName: "your_tool")`.
4. **Record every decision** with `WriteGateDecision`, and handle a Terminal State yourself: VSL
   throws; catching it, recording it and stopping is your code.
5. **Add a test** that the gate blocks the case it should and passes the one it shouldn't.

To use a real model, pass any `IChatClient` to `new RefundAgentRunner(model)` (Azure OpenAI, Foundry,
OpenAI); the tools and gates don't change. A gate decides whether a tool runs; it doesn't govern what
the model says in its own reply. If the reply itself matters, put a gate before the run too (for
example a scope check on the request). To use real services, replace the stand-ins in `Providers.cs`
(Stripe.net's `RefundService`, `System.Net.Mail.SmtpClient`).

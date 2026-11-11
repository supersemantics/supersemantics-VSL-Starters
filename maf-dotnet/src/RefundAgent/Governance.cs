// The gates in front of this agent's tools, and the ledger that records every decision.
//
// One gate per decision point:
//
// - Every tool touches an order, and every request comes from a signed-in customer, so one hard rule
//   runs before each of them:
//   * order-belongs-to-customer (Invariant): an order is only looked up, refunded or emailed about for
//     the customer it belongs to. Looking an order up only reads, but what it reads (another
//     customer's order and email address) would leak, so it is gated too.
// - issue_refund moves money and can't be cleanly undone, so it has two gates:
//   * refund-within-order (Invariant): a hard rule. Never refund more than is left on the order (its
//     total less earlier refunds), or an order that isn't refundable. A violation enters the
//     refund-escalated Terminal State, and only a named human can bring the agent back (ReEnable).
//   * refund-budget-headroom (PreNode): a measured score against a threshold. Gamma is the budget left
//     today divided by the amount, a ratio where 1.0 is break-even; it must be above 1.1.
// - send_email writes to a person outside the system, so it has one hard rule:
//   * email-to-customer-on-file (Invariant): only to the address on the order, never one the model
//     introduced.
//
// Each gate runs before its tool (F1 = true) and only allows or blocks it, with no model involved at
// the guarded call (F2 = None). That derives Low assurance: an honest description of influence on a
// model, not of how well the gates enforce. Deciding whether an action happens at all is F1.
//
// The gates evaluate the tool call's own arguments (VslToolArguments) and look up the rest (the order,
// today's refunds, the signed-in customer) themselves. The customer never comes from the model: it is
// set by RefundAgentRunner.HandleAsync from your authentication.

using SuperSemantics.Vsl;
using SuperSemantics.Vsl.AgentFramework;

namespace RefundAgent;

public static class Governance
{
    public const string AgentId = "refund-agent";
    public const string OwnershipRule = "order-belongs-to-customer";
    public const string RefundRule = "refund-within-order";
    public const string RefundBudget = "refund-budget-headroom";
    public const string EmailRule = "email-to-customer-on-file";

    // Runs before the tool (F1); only allows or blocks it, no model at the guarded call (F2 None).
    private static readonly AssuranceBasis NoModelAtGuardedCall = new(f1PreCommitment: true, F2Modification.None);

    public static readonly TerminalState RefundEscalated = new(
        "refund-escalated",
        "Refunds stop until a refunds supervisor reviews the case and re-enables the agent.",
        ["refund_outside_order"]);

    /// <summary>Every gate decision is written here. Call UseLedger to keep them in a file.</summary>
    public static VerbaLedger Ledger { get; set; } = new();

    /// <summary>The current request's decision id, so all of one request's records can be read together.</summary>
    public static readonly AsyncLocal<string?> DecisionId = new();

    /// <summary>The signed-in customer making the current request (from your authentication, never from the model).</summary>
    public static readonly AsyncLocal<string?> CustomerEmail = new();

    public static VerbaLedger UseLedger(string path) => Ledger = new VerbaLedger(new JsonlLedgerStore(path, fsync: true));

    public static readonly Invariant OrderBelongsToCustomer = new(
        OwnershipRule,
        "Only look up, refund or email about an order for the customer it belongs to.",
        candidate =>
        {
            var args = (IReadOnlyDictionary<string, object?>)candidate!;
            var order = Orders.Find(args.GetValueOrDefault("orderId")?.ToString());
            var holds = order is not null
                        && string.Equals(order.CustomerEmail, CustomerEmail.Value?.Trim(), StringComparison.OrdinalIgnoreCase);
            Ledger.WriteGateDecision(AgentId, OwnershipRule, allowed: holds, decisionId: DecisionId.Value);
            return Task.FromResult(holds);
        },
        NoModelAtGuardedCall);

    public static readonly Invariant RefundWithinOrder = new(
        RefundRule,
        "Never refund more than is left on the order, or an order that is not refundable.",
        candidate =>
        {
            var args = (IReadOnlyDictionary<string, object?>)candidate!;
            var order = Orders.Find(args.GetValueOrDefault("orderId")?.ToString());
            var amount = VslToolArguments.GetDouble(args, "amount");
            var holds = order is not null && order.Refundable && amount > 0
                        && amount <= order.Total - RefundBook.Current.Refunded(order.OrderId);
            Ledger.WriteGateDecision(AgentId, RefundRule, allowed: holds, decisionId: DecisionId.Value);
            return Task.FromResult(holds);
        },
        NoModelAtGuardedCall,
        onViolation: RefundEscalated);

    public static readonly PreNode RefundBudgetHeadroom = new(
        RefundBudget,
        "A refund must leave headroom in today's refund budget: budget left / amount above 1.1.",
        candidate =>
        {
            var amount = VslToolArguments.GetDouble((IReadOnlyDictionary<string, object?>)candidate!, "amount");
            // A measured ratio: budget left / amount. 0.0 for a missing or bad amount, so the gate fails closed.
            var estimate = new GammaEstimate(amount > 0 ? RefundBook.Current.BudgetRemaining / amount : 0.0);
            Ledger.WriteGateDecision(AgentId, RefundBudget, estimate: estimate, gammaThreshold: 1.1, decisionId: DecisionId.Value);
            return Task.FromResult(estimate);
        },
        NoModelAtGuardedCall,
        gammaThreshold: 1.1);

    public static readonly Invariant EmailToCustomer = new(
        EmailRule,
        "Only email the customer on the order, never an address the model introduced.",
        candidate =>
        {
            var args = (IReadOnlyDictionary<string, object?>)candidate!;
            var order = Orders.Find(args.GetValueOrDefault("orderId")?.ToString());
            var to = args.GetValueOrDefault("to")?.ToString()?.Trim() ?? "";
            var holds = order is not null && string.Equals(to, order.CustomerEmail, StringComparison.OrdinalIgnoreCase);
            Ledger.WriteGateDecision(AgentId, EmailRule, allowed: holds, decisionId: DecisionId.Value);
            return Task.FromResult(holds);
        },
        NoModelAtGuardedCall);

    // ---- Terminal State and the way back ---------------------------------------------------------

    private static readonly GovernanceAuthority RefundsSupervisor = new("Refunds Supervisor", "refunds-supervisor@example.com");

    public static Instance NewInstance() => new(Guid.NewGuid().ToString(), new IdentityKey(AgentId));

    /// <summary>VSL never writes this for you: catching the violation and recording it is your code.</summary>
    public static void EnterTerminalState(Instance instance, string reason) =>
        Ledger.Write(LedgerEntryType.Terminal, AgentId, instanceId: instance.InstanceId, decisionId: DecisionId.Value,
            payload: new Dictionary<string, object?> { ["terminal_state"] = RefundEscalated.Name, ["reason"] = reason });

    /// <summary>The only way out of refund-escalated: a named human, with evidence, through VSL-Core.NET.</summary>
    public static Instance ReEnable(Instance instance, string authorisedBy, string rootCause, string fix)
    {
        var (next, transition) = SuperSemantics.Vsl.Governance.RequestReEnablement(
            instance, RefundsSupervisor, authorisedBy, new Evidence(rootCause, fix));
        Ledger.Write(LedgerEntryType.HumanAuthorisedTransition, AgentId, instanceId: instance.InstanceId,
            payload: new Dictionary<string, object?>
            {
                ["authority"] = transition.Authority.Name,
                ["authorised_by"] = transition.AuthorisedBy,
                ["evidence_hash"] = transition.AuthorisationEvidenceHash,
            });
        Ledger.Write(LedgerEntryType.ReEnablement, AgentId, instanceId: next.InstanceId, causedBy: instance.InstanceId,
            payload: new Dictionary<string, object?> { ["predecessor_instance_id"] = instance.InstanceId });
        return next;
    }
}

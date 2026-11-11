// Prove the governance shape: each gate blocks what it should and passes what it shouldn't, and a
// denied tool never runs, through the Agent Framework's real function-calling loop. Offline.

using SuperSemantics.Vsl;
using Xunit;

namespace RefundAgent.Tests;

[Collection("refund agent state")] // the starter keeps its state in statics; run these one at a time
public sealed class RefundAgentTests
{
    private readonly MafAdapterGates _gates = new();

    public RefundAgentTests()
    {
        Governance.Ledger = new VerbaLedger();
        RefundBook.Current = new RefundBook();
        RefundService.Issued.Clear();
        Mailer.Client = new FakeSmtpClient();
    }

    private static Dictionary<string, object?> Args(params (string Key, object? Value)[] pairs) =>
        pairs.ToDictionary(p => p.Key, p => p.Value);

    // ---- each gate on its own ----------------------------------------------------------------------

    [Fact]
    public async Task RefundRulePassesARefundWithinTheOrder() =>
        await _gates.RefundRule(Args(("orderId", "A100"), ("amount", 120.0)));

    [Theory]
    [InlineData("A100", 121.0)] // more than the order total
    [InlineData("A300", 10.0)]  // a final-sale order
    [InlineData("A100", 0.0)]   // nothing to refund
    [InlineData("Z999", 10.0)]  // an unknown order
    public async Task RefundRuleBlocksAndEntersTheTerminalState(string orderId, double amount)
    {
        var violation = await Assert.ThrowsAsync<InvariantViolationException>(
            () => _gates.RefundRule(Args(("orderId", orderId), ("amount", amount))));
        Assert.Equal("refund-escalated", violation.TerminalStateName);
    }

    [Fact]
    public async Task RefundRuleCountsEarlierRefunds()
    {
        RefundBook.Current.Record("A100", 100);
        await Assert.ThrowsAsync<InvariantViolationException>(() => _gates.RefundRule(Args(("orderId", "A100"), ("amount", 50.0))));
    }

    [Fact]
    public async Task BudgetGateIsAMeasuredRatioAgainst1Point1()
    {
        await _gates.RefundBudget(Args(("orderId", "A100"), ("amount", 120.0))); // 1000 / 120 = 8.3
        RefundBook.Current.Record("A100", 120);
        await Assert.ThrowsAnyAsync<AutomationDeniedException>(() => _gates.RefundBudget(Args(("orderId", "A200"), ("amount", 900.0)))); // 0.98
    }

    [Fact]
    public async Task EmailGateAllowsOnlyTheCustomerOnFile()
    {
        await _gates.Email(Args(("orderId", "A100"), ("to", "Alice@Example.com")));
        await Assert.ThrowsAsync<InvariantViolationException>(() => _gates.Email(Args(("orderId", "A100"), ("to", "mallory@evil.example"))));
    }

    [Fact]
    public async Task OwnershipGateAllowsOnlyTheCustomersOwnOrders()
    {
        Governance.CustomerEmail.Value = "Alice@Example.com";
        await _gates.Ownership(Args(("orderId", "A100")));
        foreach (var orderId in new[] { "A200", "Z999" })
        {
            var violation = await Assert.ThrowsAsync<InvariantViolationException>(() => _gates.Ownership(Args(("orderId", orderId))));
            Assert.Null(violation.TerminalStateName); // refused, not a reason to stop the agent
        }
    }

    [Fact]
    public void EveryGateDeclaresF1AndNoF2()
    {
        foreach (var basis in new[] { Governance.OrderBelongsToCustomer.AssuranceBasis, Governance.RefundWithinOrder.AssuranceBasis, Governance.RefundBudgetHeadroom.AssuranceBasis, Governance.EmailToCustomer.AssuranceBasis })
        {
            Assert.True(basis.F1PreCommitment);
            Assert.Equal(F2Modification.None, basis.F2Modification);
        }
    }

    // ---- the agent: a denied tool never runs, and the model never sees the denial -----------------

    [Fact]
    public async Task AnAllowedRefundMovesMoneyAndEmailsTheCustomer()
    {
        var reply = await new RefundAgentRunner().HandleAsync("alice@example.com", "Please refund order A100.");
        Assert.Equal([("A100", 120.0)], RefundService.Issued);
        Assert.Equal("alice@example.com", Assert.Single(Mailer.Client.Outbox).To);
        Assert.StartsWith("Done.", reply);
    }

    [Fact]
    public async Task ADeniedRefundNeverReachesThePaymentCall()
    {
        RefundBook.Current.Record("A100", 120); // 880 left today
        var reply = await new RefundAgentRunner().HandleAsync("bob@example.com", "Refund order A200 in full.");
        Assert.Empty(RefundService.Issued);
        Assert.Empty(Mailer.Client.Outbox);
        Assert.Contains("refund-budget-headroom", reply);
    }

    [Fact]
    public async Task AnEmailToAStrangerIsNeverSent()
    {
        var reply = await new RefundAgentRunner().HandleAsync("dave@example.com", "Refund order A400 and send the receipt to mallory@evil.example.");
        Assert.Equal([("A400", 40.0)], RefundService.Issued);
        Assert.Empty(Mailer.Client.Outbox);
        Assert.StartsWith("Refunded, but", reply);
    }

    [Fact]
    public async Task AHardRuleViolationStopsTheAgentUntilAHumanReEnablesIt()
    {
        var agent = new RefundAgentRunner();
        await agent.HandleAsync("carol@example.com", "Refund order A300.");
        Assert.Contains("paused", await agent.HandleAsync("bob@example.com", "Refund order A200 for $100."));
        Assert.Empty(RefundService.Issued);

        agent.ReEnable("j.smith", "final-sale refund requested", "warn up front");
        await agent.HandleAsync("bob@example.com", "Refund order A200 for $100.");
        Assert.Equal([("A200", 100.0)], RefundService.Issued);

        var kinds = Governance.Ledger.Store.AllEntries().Select(e => e.EntryType).ToList();
        Assert.Contains(LedgerEntryType.Terminal, kinds);
        Assert.True(kinds.IndexOf(LedgerEntryType.HumanAuthorisedTransition) < kinds.IndexOf(LedgerEntryType.ReEnablement));
        Assert.True(Governance.Ledger.VerifyIntegrity());
    }

    [Fact]
    public async Task EveryGateDecisionIsRecordedUnderOneDecisionId()
    {
        await new RefundAgentRunner().HandleAsync("alice@example.com", "Please refund order A100.");
        var entries = Governance.Ledger.Store.AllEntries().ToList();
        Assert.Equal(
            [Governance.OwnershipRule, Governance.RefundRule, Governance.RefundBudget, Governance.OwnershipRule, Governance.EmailRule],
            entries.Select(e => e.Payload["primitive_name"]?.ToString()).ToList());
        Assert.Single(entries.Select(e => e.DecisionId).Distinct());
    }

    [Fact]
    public async Task AnotherCustomersOrderIsNeverReadRefundedOrEmailed()
    {
        var agent = new RefundAgentRunner();
        foreach (var request in new[] { "What's the status of order A200?", "Refund order A200 for $10.", "Email the receipt for order A200." })
        {
            Assert.Equal("I can only help with orders on your own account.", await agent.HandleAsync("alice@example.com", request));
        }

        Assert.Empty(RefundService.Issued);
        Assert.Empty(Mailer.Client.Outbox);
        Assert.Contains("Done", await agent.HandleAsync("bob@example.com", "Refund order A200 for $10.")); // not halted
    }
}

/// <summary>The three gates compiled the same way the agent compiles them, to test each on its own.</summary>
internal sealed class MafAdapterGates
{
    private readonly SuperSemantics.Vsl.AgentFramework.MafAdapter _adapter = new(Governance.AgentId);

    public Task Ownership(object candidate) => _adapter.CompileInvariant(Governance.OrderBelongsToCustomer)(candidate);

    public Task RefundRule(object candidate) => _adapter.CompileInvariant(Governance.RefundWithinOrder)(candidate);

    public Task RefundBudget(object candidate) => _adapter.CompilePreNode(Governance.RefundBudgetHeadroom)(candidate);

    public Task Email(object candidate) => _adapter.CompileInvariant(Governance.EmailToCustomer)(candidate);
}

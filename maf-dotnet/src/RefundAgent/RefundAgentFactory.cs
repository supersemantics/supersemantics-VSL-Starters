// The refund agent on the Microsoft Agent Framework for .NET, with VSL gates on its tools.
//
// UseVslGates runs the listed gates, in order, before every call to the named tool; the tool runs only
// if they all pass. With the default VslDenialMode.Abort, a denial stops the whole run and throws
// VslGateDeniedException to this code. The model is never handed a tool error to argue with.
//
// The "model" here is ScriptedChatClient, so the starter runs offline: it asks for tool calls the way a
// real model does, through the Agent Framework's own function-calling loop. To use a real model, pass
// any IChatClient instead (Azure OpenAI, Foundry, OpenAI); the tools and gates do not change.

using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using SuperSemantics.Vsl;
using SuperSemantics.Vsl.AgentFramework;
using SuperSemantics.Vsl.Conformance;

namespace RefundAgent;

public static partial class RefundAgentFactory
{
    private const string Instructions = "You handle refunds. Look up orders, refund them, and email the customer a confirmation.";

    private static readonly MafAdapter Adapter = new(Governance.AgentId);
    private static readonly CompiledGate OwnershipGate = Adapter.CompileInvariant(Governance.OrderBelongsToCustomer);
    private static readonly CompiledGate RefundRuleGate = Adapter.CompileInvariant(Governance.RefundWithinOrder);
    private static readonly CompiledGate RefundBudgetGate = Adapter.CompilePreNode(Governance.RefundBudgetHeadroom);
    private static readonly CompiledGate EmailGate = Adapter.CompileInvariant(Governance.EmailToCustomer);

    /// <summary>The agent and its gates, each list run in order before its tool: ownership first, then the
    /// refund's hard rule and budget, or the email rule.</summary>
    public static AIAgent Build(IChatClient? model = null)
    {
        var lookupOrder = AIFunctionFactory.Create(Tools.LookupOrder, "lookup_order");
        var issueRefund = AIFunctionFactory.Create(Tools.IssueRefund, "issue_refund");
        var sendEmail = AIFunctionFactory.Create(Tools.SendEmail, "send_email");

        return new ChatClientAgent(model ?? new ScriptedChatClient(), instructions: Instructions, name: "RefundAgent",
                tools: [lookupOrder, issueRefund, sendEmail])
            .AsBuilder()
            .UseVslGates([OwnershipGate], functionName: "lookup_order")
            .UseVslGates([OwnershipGate, RefundRuleGate, RefundBudgetGate], functionName: "issue_refund")
            .UseVslGates([OwnershipGate, EmailGate], functionName: "send_email")
            .Build();
    }
}

/// <summary>Runs requests through the agent, and stops after a hard-rule violation until a human says so.</summary>
public sealed class RefundAgentRunner(IChatClient? model = null)
{
    private readonly AIAgent _agent = RefundAgentFactory.Build(model);
    private Instance _instance = Governance.NewInstance();
    private string? _haltedReason;

    /// <param name="customerEmail">The signed-in customer, from your authentication, never from the request.</param>
    /// <param name="request">What the customer asked.</param>
    public async Task<string> HandleAsync(string customerEmail, string request)
    {
        if (_haltedReason is not null)
        {
            return $"Refunds are paused pending supervisor review ({_haltedReason}).";
        }

        Governance.DecisionId.Value = Guid.NewGuid().ToString();
        Governance.CustomerEmail.Value = customerEmail;
        try
        {
            var response = await _agent.RunAsync(request);
            return response.Text;
        }
        catch (VslGateDeniedException denied)
        {
            var reason = denied.Denial.Reason;
            if (denied.Denial is InvariantViolationException { InvariantName: Governance.OwnershipRule })
            {
                return "I can only help with orders on your own account.";
            }

            if (denied.Denial is InvariantViolationException { TerminalStateName: not null })
            {
                // VSL threw; recording the Terminal State and stopping is this code's job.
                Governance.EnterTerminalState(_instance, reason);
                _haltedReason = reason;
            }

            return denied.FunctionName == "send_email"
                ? $"Refunded, but the confirmation email was not sent: {reason}."
                : $"Not done: {reason} ('{denied.FunctionName}' did not run). A person will review it.";
        }
    }

    public void ReEnable(string authorisedBy, string rootCause, string fix)
    {
        _instance = Governance.ReEnable(_instance, authorisedBy, rootCause, fix);
        _haltedReason = null;
    }
}

/// <summary>
/// Stands in for a model: plans tool calls from the request, one per turn, then answers. Deterministic,
/// so tests can rely on it.
/// </summary>
public sealed partial class ScriptedChatClient : IChatClient
{
    public async Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
    {
        var history = messages.ToList();
        var request = history.FirstOrDefault(m => m.Role == ChatRole.User)?.Text ?? "";
        var results = history.SelectMany(m => m.Contents.OfType<FunctionResultContent>()).ToList();
        var steps = Plan(request);
        if (results.Count < steps.Count)
        {
            var (name, arguments) = steps[results.Count];
            var call = new FunctionCallContent($"call-{results.Count + 1}", name, arguments);
            return new ChatResponse(new ChatMessage(ChatRole.Assistant, [call]));
        }

        await Task.Yield();
        return new ChatResponse(new ChatMessage(ChatRole.Assistant, "Done. " + string.Join(" ", results.Select(r => r.Result))));
    }

    private static List<(string Name, Dictionary<string, object?> Arguments)> Plan(string request)
    {
        var orderId = OrderPattern().Match(request) is { Success: true } m ? m.Value : "";
        var order = Orders.Find(orderId);
        var explicitTo = EmailPattern().Match(request);
        var to = explicitTo.Success ? explicitTo.Value : order?.CustomerEmail ?? "";
        var lowered = request.ToLowerInvariant();
        if (lowered.Contains("refund"))
        {
            var amountMatch = AmountPattern().Match(request);
            var amount = amountMatch.Success ? double.Parse(amountMatch.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture) : order?.Total ?? 0;
            return
            [
                ("issue_refund", new() { ["orderId"] = orderId, ["amount"] = amount }),
                ("send_email", new() { ["orderId"] = orderId, ["to"] = to, ["subject"] = $"Refund for order {orderId}", ["body"] = $"We refunded ${amount:F2} for order {orderId}." }),
            ];
        }

        if (lowered.Contains("receipt") || lowered.Contains("email"))
        {
            return [("send_email", new() { ["orderId"] = orderId, ["to"] = to, ["subject"] = $"Order {orderId}", ["body"] = $"Here is the receipt for order {orderId}." })];
        }

        return [("lookup_order", new() { ["orderId"] = orderId })];
    }

    public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        foreach (var update in (await GetResponseAsync(messages, options, cancellationToken)).ToChatResponseUpdates())
        {
            yield return update;
        }
    }

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose() { }

    [GeneratedRegex(@"\b[A-Z]\d{3}\b")]
    private static partial Regex OrderPattern();

    [GeneratedRegex(@"\$(\d+(?:\.\d+)?)")]
    private static partial Regex AmountPattern();

    [GeneratedRegex(@"[\w.+-]+@[\w-]+\.[\w.]+")]
    private static partial Regex EmailPattern();
}

using System.ComponentModel;

namespace RefundAgent;

/// <summary>
/// The tools the model can call. Two have real-world effects and are gated (Governance.cs, wired in
/// RefundAgentFactory.cs); looking up an order only reads, so it has no gate.
/// </summary>
public static class Tools
{
    private const string Sender = "refunds@example.com";

    [Description("Look up an order. Read-only: no side effect, so no gate.")]
    public static string LookupOrder([Description("Order id, for example A100")] string orderId)
    {
        var order = Orders.Find(orderId);
        return order is null
            ? "No such order."
            : $"Order {order.OrderId}: ${order.Total:F2}, {(order.Refundable ? "refundable" : "final sale")}, {order.CustomerEmail}.";
    }

    [Description("Refund a customer. Runs only after refund-within-order and refund-budget-headroom pass.")]
    public static async Task<string> IssueRefund(
        [Description("Order id")] string orderId,
        [Description("Amount to refund, in USD")] double amount)
    {
        var service = new RefundService();
        var refundId = await service.CreateAsync(new RefundCreateOptions { OrderId = orderId, Amount = amount });
        RefundBook.Current.Record(orderId, amount);
        return $"Refunded ${amount:F2} for order {orderId} (ref {refundId}).";
    }

    [Description("Email a customer. Runs only after email-to-customer-on-file passes.")]
    public static async Task<string> SendEmail(
        [Description("Order the email is about")] string orderId,
        [Description("Recipient address")] string to,
        [Description("Subject line")] string subject,
        [Description("Message body")] string body)
    {
        await Mailer.Client.SendMailAsync(Sender, to, subject, body);
        return $"Email sent to {to}.";
    }
}

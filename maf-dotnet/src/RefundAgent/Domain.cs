namespace RefundAgent;

/// <summary>An order. Plain data, no governance. Replace with your own entities.</summary>
public sealed record Order(string OrderId, string CustomerEmail, double Total, bool Refundable);

public static class Orders
{
    public static readonly IReadOnlyDictionary<string, Order> All = new Dictionary<string, Order>
    {
        ["A100"] = new("A100", "alice@example.com", 120, Refundable: true),
        ["A200"] = new("A200", "bob@example.com", 900, Refundable: true),
        ["A300"] = new("A300", "carol@example.com", 60, Refundable: false), // final sale
        ["A400"] = new("A400", "dave@example.com", 40, Refundable: true),
    };

    public static Order? Find(string? orderId) => orderId is not null && All.TryGetValue(orderId, out var order) ? order : null;
}

/// <summary>Today's refunds, by order. In production this lives in your database, not in memory.</summary>
public sealed class RefundBook(double dailyLimit = 1000)
{
    private readonly Dictionary<string, double> _byOrder = [];

    public static RefundBook Current { get; set; } = new();

    public double BudgetRemaining => dailyLimit - _byOrder.Values.Sum();

    public double Refunded(string orderId) => _byOrder.GetValueOrDefault(orderId);

    public void Record(string orderId, double amount) => _byOrder[orderId] = Refunded(orderId) + amount;
}

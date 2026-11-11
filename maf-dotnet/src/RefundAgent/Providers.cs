// Stand-ins for the payment and email services, so the starter runs offline.
//
// Replace them with your real clients and keep the calls in Tools.cs: that is where the side effect
// happens, and where the gates must run before it. The fakes have the same shape as the real thing:
// RefundService like Stripe.net's (new RefundService().CreateAsync(options)), and Mailer.Client like
// System.Net.Mail.SmtpClient's SendMailAsync(from, to, subject, body).

namespace RefundAgent;

public sealed class RefundCreateOptions
{
    public required string OrderId { get; init; }
    public required double Amount { get; init; }
}

public sealed class RefundService
{
    public static List<(string OrderId, double Amount)> Issued { get; } = [];

    public Task<string> CreateAsync(RefundCreateOptions options)
    {
        Issued.Add((options.OrderId, options.Amount));
        return Task.FromResult($"re_{options.OrderId}_{Issued.Count}");
    }
}

public sealed class FakeSmtpClient
{
    public List<(string From, string To, string Subject, string Body)> Outbox { get; } = [];

    public Task SendMailAsync(string from, string recipients, string subject, string body)
    {
        Outbox.Add((from, recipients, subject, body));
        return Task.CompletedTask;
    }
}

public static class Mailer
{
    public static FakeSmtpClient Client { get; set; } = new();
}

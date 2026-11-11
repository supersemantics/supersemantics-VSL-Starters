// Stand-ins for the payment and email services, so the starter runs offline.
//
// Replace them with your real clients and keep the calls in tools.ts: that is where the side effect
// happens, and where the gates must run before it. The fakes have the same shape as the real thing:
// `mailer` like nodemailer's transporter.sendMail({ from, to, subject, text }), and `payments` like a
// payment provider's refunds.create({ ... }).

export class FakePayments {
  readonly issued: Array<[string, number]> = [];

  async create(input: { orderId: string; amount: number }): Promise<string> {
    this.issued.push([input.orderId, input.amount]);
    return `re_${input.orderId}_${this.issued.length}`;
  }
}

export class FakeTransport {
  readonly outbox: Array<{ from: string; to: string; subject: string; text: string }> = [];

  async sendMail(message: { from: string; to: string; subject: string; text: string }): Promise<void> {
    this.outbox.push(message);
  }
}

export const refunds = new FakePayments();
export const mailer = new FakeTransport();

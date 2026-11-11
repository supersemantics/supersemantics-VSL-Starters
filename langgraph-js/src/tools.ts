// The actions this agent can take. Two of them have real-world effects and are gated (see
// governance.ts and agent.ts); looking up an order only reads, so it has no gate.

import { ORDERS, refundBook, type Order } from "./domain.ts";
import { mailer, refunds } from "./providers.ts";

const SENDER = "refunds@example.com";

/** Read-only: no side effect, so no gate. Over-gating adds noise, not safety. */
export function lookupOrder(orderId: string): Order | undefined {
  return ORDERS[orderId];
}

/** Moves money. Runs only after refund-within-order and refund-budget-headroom pass. */
export async function issueRefund(orderId: string, amount: number): Promise<string> {
  const refundId = await refunds.create({ orderId, amount });
  refundBook.current.record(orderId, amount);
  return refundId;
}

/** Writes to a person. Runs only after email-to-customer-on-file passes. */
export async function sendEmail(to: string, subject: string, text: string): Promise<void> {
  await mailer.sendMail({ from: SENDER, to, subject, text });
}

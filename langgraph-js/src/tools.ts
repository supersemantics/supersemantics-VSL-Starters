// The actions this agent can take, each behind a gate (see governance.ts and agent.ts). Looking an
// order up only reads, but what it reads could leak, so order-belongs-to-customer runs first.

import { ORDERS, refundBook, type Order } from "./domain.ts";
import { mailer, refunds } from "./providers.ts";

const SENDER = "refunds@example.com";

/** Read-only. The gates call it too, to check an order; the agent answers from it only after order-belongs-to-customer passes. */
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

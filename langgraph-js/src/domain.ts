// The refund agent's domain: orders and today's refunds. Plain data, no governance.

export interface Order {
  orderId: string;
  customerEmail: string;
  total: number;
  refundable: boolean; // false for final-sale items
}

export const ORDERS: Record<string, Order> = {
  A100: { orderId: "A100", customerEmail: "alice@example.com", total: 120, refundable: true },
  A200: { orderId: "A200", customerEmail: "bob@example.com", total: 900, refundable: true },
  A300: { orderId: "A300", customerEmail: "carol@example.com", total: 60, refundable: false },
  A400: { orderId: "A400", customerEmail: "dave@example.com", total: 40, refundable: true },
};

/** Today's refunds, by order. In production this lives in your database, not in memory. */
export class RefundBook {
  readonly dailyLimit: number;
  readonly byOrder = new Map<string, number>();

  constructor(dailyLimit = 1000) {
    this.dailyLimit = dailyLimit;
  }

  get budgetRemaining(): number {
    let spent = 0;
    for (const amount of this.byOrder.values()) spent += amount;
    return this.dailyLimit - spent;
  }

  refunded(orderId: string): number {
    return this.byOrder.get(orderId) ?? 0;
  }

  record(orderId: string, amount: number): void {
    this.byOrder.set(orderId, this.refunded(orderId) + amount);
  }
}

export const refundBook = { current: new RefundBook() };

/** What the ownership gate evaluates: the order asked about, and the signed-in customer asking. */
export interface OwnershipCandidate {
  order: Order | undefined;
  customerEmail: string;
  decisionId: string;
}

/** What the refund gates evaluate. */
export interface RefundCandidate {
  order: Order;
  amount: number;
  alreadyRefunded: number;
  budgetRemaining: number;
  decisionId: string;
}

/** What the email gate evaluates. */
export interface EmailCandidate {
  order: Order;
  to: string;
  decisionId: string;
}

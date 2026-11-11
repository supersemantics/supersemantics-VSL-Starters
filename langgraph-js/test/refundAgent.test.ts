// Prove the governance shape: each gate blocks what it should and passes what it shouldn't, and a
// denied action never runs. Offline and deterministic. Run with: npm test

import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";
import { AutomationDeniedException, InvariantViolation, LedgerEntryType, VerbaLedger } from "@supersemantics/vsl-core";

import { RefundAgent } from "../src/agent.ts";
import { ORDERS, RefundBook, refundBook, type RefundCandidate } from "../src/domain.ts";
import {
  emailGate,
  emailToCustomer,
  ledger,
  orderBelongsToCustomer,
  ownershipGate,
  refundBudget,
  refundBudgetGate,
  refundRuleGate,
  refundWithinOrder,
} from "../src/governance.ts";
import { mailer, refunds } from "../src/providers.ts";

beforeEach(() => {
  ledger.current = new VerbaLedger();
  refundBook.current = new RefundBook();
  refunds.issued.length = 0;
  mailer.outbox.length = 0;
});

const refund = (orderId: string, amount: number, alreadyRefunded = 0, budgetRemaining = 1000): RefundCandidate => ({
  order: ORDERS[orderId]!,
  amount,
  alreadyRefunded,
  budgetRemaining,
  decisionId: "d-1",
});

async function entries() {
  const all = [];
  for await (const entry of ledger.current.store.allEntries()) all.push(entry);
  return all;
}

describe("each gate on its own", () => {
  test("refund rule passes a refund within the order", async () => {
    await refundRuleGate(refund("A100", 120));
  });

  for (const [candidate, why] of [
    [refund("A100", 121), "more than the order total"],
    [refund("A100", 50, 100), "more than is left after earlier refunds"],
    [refund("A300", 10), "a final-sale order"],
    [refund("A100", 0), "nothing to refund"],
  ] as const) {
    test(`refund rule blocks ${why} and enters the Terminal State`, async () => {
      await assert.rejects(refundRuleGate(candidate), (error: unknown) => {
        assert.ok(error instanceof InvariantViolation);
        assert.equal(error.terminalStateName, "refund-escalated");
        return true;
      });
    });
  }

  test("budget gate is a measured ratio against 1.1", async () => {
    await refundBudgetGate(refund("A100", 120, 0, 1000)); // 8.3
    await assert.rejects(refundBudgetGate(refund("A200", 900, 0, 880)), AutomationDeniedException); // 0.98
    await assert.rejects(refundBudgetGate(refund("A200", 100, 0, 105)), AutomationDeniedException); // 1.05
  });

  test("email gate allows only the customer on file", async () => {
    await emailGate({ order: ORDERS.A100!, to: "Alice@Example.com", decisionId: "d-1" });
    await assert.rejects(emailGate({ order: ORDERS.A100!, to: "mallory@evil.example", decisionId: "d-1" }), InvariantViolation);
  });

  test("ownership gate allows only the customer's own orders", async () => {
    await ownershipGate({ order: ORDERS.A100, customerEmail: "Alice@Example.com", decisionId: "d-1" });
    for (const order of [ORDERS.A200, undefined]) {
      await assert.rejects(ownershipGate({ order, customerEmail: "alice@example.com", decisionId: "d-1" }), (error: unknown) => {
        assert.ok(error instanceof InvariantViolation);
        assert.equal(error.terminalStateName, null); // refused, not a reason to stop the agent
        return true;
      });
    }
  });

  test("every gate declares F1 and no F2", () => {
    for (const gate of [orderBelongsToCustomer, refundWithinOrder, refundBudget, emailToCustomer]) {
      assert.equal(gate.assuranceBasis.f1PreCommitment, true);
      assert.equal(gate.assuranceBasis.f2Modification, "NONE");
    }
  });
});

describe("the agent: a denied action never runs", () => {
  test("an allowed refund moves money and emails the customer", async () => {
    const reply = await new RefundAgent().handle("alice@example.com", "Please refund order A100.");
    assert.deepEqual(refunds.issued, [["A100", 120]]);
    assert.equal(mailer.outbox[0]?.to, "alice@example.com");
    assert.match(reply, /^Done\./);
  });

  test("a denied refund never reaches the payment call", async () => {
    refundBook.current.record("A100", 120); // 880 left today
    const reply = await new RefundAgent().handle("bob@example.com", "Refund order A200 in full.");
    assert.deepEqual(refunds.issued, []);
    assert.deepEqual(mailer.outbox, []);
    assert.match(reply, /refund-budget-headroom/);
  });

  test("an email to a stranger is never sent", async () => {
    await new RefundAgent().handle("dave@example.com", "Refund order A400 and send the receipt to mallory@evil.example.");
    assert.deepEqual(refunds.issued, [["A400", 40]]);
    assert.deepEqual(mailer.outbox, []);
  });

  test("a hard-rule violation stops the agent until a human re-enables it", async () => {
    const agent = new RefundAgent();
    await agent.handle("carol@example.com", "Refund order A300.");
    const paused = await agent.handle("bob@example.com", "Refund order A200 for $100.");
    assert.match(paused, /paused/);
    assert.deepEqual(refunds.issued, []);

    await agent.reEnable("j.smith", "final-sale refund requested", "warn up front");
    await agent.handle("bob@example.com", "Refund order A200 for $100.");
    assert.deepEqual(refunds.issued, [["A200", 100]]);

    const kinds = (await entries()).map((e) => e.entryType);
    assert.ok(kinds.includes(LedgerEntryType.TERMINAL));
    assert.ok(kinds.indexOf(LedgerEntryType.HUMAN_AUTHORISED_TRANSITION) < kinds.indexOf(LedgerEntryType.RE_ENABLEMENT));
    assert.equal(await ledger.current.verifyIntegrity(), true);
  });

  test("every gate decision is recorded", async () => {
    await new RefundAgent().handle("alice@example.com", "Please refund order A100.");
    const names = (await entries()).map((e) => e.payload["primitive_name"]);
    assert.deepEqual(names, [
      "order-belongs-to-customer",
      "refund-within-order",
      "refund-budget-headroom",
      "email-to-customer-on-file",
    ]);
  });

  test("another customer's order is never read, refunded or emailed", async () => {
    const agent = new RefundAgent();
    for (const request of ["What's the status of order A200?", "Refund order A200 for $10.", "Email the receipt for order A200."]) {
      assert.equal(await agent.handle("alice@example.com", request), "I can only help with orders on your own account.");
    }
    assert.deepEqual(refunds.issued, []);
    assert.deepEqual(mailer.outbox, []);
    assert.equal(agent.haltedReason, null);
  });
});

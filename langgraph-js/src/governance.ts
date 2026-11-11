// The gates in front of this agent's actions, and the ledger that records every decision.
//
// One gate per decision point:
//
// - Every request touches an order and is made by a signed-in customer, so one hard rule comes first:
//   * order-belongs-to-customer (Invariant): an order is only looked up, refunded or emailed about for
//     the customer it belongs to. Looking an order up only reads, but what it reads (another
//     customer's order and email address) would leak, so it is gated too.
// - issueRefund moves money and can't be cleanly undone, so it has two gates:
//   * refund-within-order (Invariant): a hard rule. Never refund more than is left on the order (its
//     total less earlier refunds), or an order that isn't refundable. A violation enters the
//     refund-escalated Terminal State, and only a named human can bring the agent back (reEnable).
//   * refund-budget-headroom (PreNode): a measured score against a threshold. Gamma is the budget
//     left today divided by the amount, a ratio where 1.0 is break-even; it must be above 1.1.
// - sendEmail writes to a person outside the system, so it has one hard rule:
//   * email-to-customer-on-file (Invariant): only to the address on the order, never one the model
//     introduced.
//
// Each gate runs before its action (F1 = true) and only allows or blocks it, with no model involved at
// the guarded call (F2 = NONE). That derives LOW assurance: an honest description of influence on a
// model, not of how well the gates enforce. Deciding whether an action happens at all is F1.

import { randomUUID } from "node:crypto";
import {
  AssuranceBasis,
  Evidence,
  F2Modification,
  GammaEstimate,
  GovernanceAuthority,
  IdentityKey,
  Instance,
  Invariant,
  JsonlLedgerStore,
  LedgerEntryType,
  PreNode,
  TerminalState,
  VerbaLedger,
  requestReEnablement,
} from "@supersemantics/vsl-core";
import { LangGraphAdapter } from "@supersemantics/vsl-langgraph";

import type { EmailCandidate, OwnershipCandidate, RefundCandidate } from "./domain.ts";

export const AGENT_ID = "refund-agent";

// Runs before the action (F1); only allows or blocks it, no model at the guarded call (F2 NONE).
const NO_MODEL_AT_GUARDED_CALL = new AssuranceBasis(true, F2Modification.NONE);

export const REFUND_ESCALATED = new TerminalState({
  name: "refund-escalated",
  description: "Refunds stop until a refunds supervisor reviews the case and re-enables the agent.",
  entryConditions: ["refund_outside_order"],
});

/** Every gate decision is written here. Call useLedger() to keep them in a file. */
export const ledger = { current: new VerbaLedger() };

export function useLedger(path: string): VerbaLedger {
  ledger.current = new VerbaLedger(new JsonlLedgerStore(path, { fsync: true }));
  return ledger.current;
}

async function orderIsTheCustomers(input: unknown): Promise<boolean> {
  const candidate = input as OwnershipCandidate;
  const holds = candidate.order?.customerEmail.toLowerCase() === candidate.customerEmail.trim().toLowerCase();
  await ledger.current.writeGateDecision(AGENT_ID, "order-belongs-to-customer", { allowed: holds, decisionId: candidate.decisionId });
  return holds;
}

async function refundIsWithinOrder(input: unknown): Promise<boolean> {
  const candidate = input as RefundCandidate;
  const leftOnOrder = candidate.order.total - candidate.alreadyRefunded;
  const holds = candidate.order.refundable && candidate.amount > 0 && candidate.amount <= leftOnOrder;
  await ledger.current.writeGateDecision(AGENT_ID, "refund-within-order", { allowed: holds, decisionId: candidate.decisionId });
  return holds;
}

async function refundBudgetHeadroom(input: unknown): Promise<GammaEstimate> {
  const candidate = input as RefundCandidate;
  // A measured ratio: budget left / amount. 0 for a missing or bad amount, so the gate fails closed.
  const estimate = new GammaEstimate({ gammaHat: candidate.amount > 0 ? candidate.budgetRemaining / candidate.amount : 0 });
  await ledger.current.writeGateDecision(AGENT_ID, "refund-budget-headroom", {
    estimate,
    gammaThreshold: 1.1,
    decisionId: candidate.decisionId,
  });
  return estimate;
}

async function emailIsToCustomerOnFile(input: unknown): Promise<boolean> {
  const candidate = input as EmailCandidate;
  const holds = candidate.to.trim().toLowerCase() === candidate.order.customerEmail.toLowerCase();
  await ledger.current.writeGateDecision(AGENT_ID, "email-to-customer-on-file", { allowed: holds, decisionId: candidate.decisionId });
  return holds;
}

export const orderBelongsToCustomer = new Invariant({
  name: "order-belongs-to-customer",
  description: "Only look up, refund or email about an order for the customer it belongs to.",
  rule: orderIsTheCustomers,
  assuranceBasis: NO_MODEL_AT_GUARDED_CALL,
});

export const refundWithinOrder = new Invariant({
  name: "refund-within-order",
  description: "Never refund more than is left on the order, or an order that is not refundable.",
  rule: refundIsWithinOrder,
  assuranceBasis: NO_MODEL_AT_GUARDED_CALL,
  onViolation: REFUND_ESCALATED,
});

export const refundBudget = new PreNode({
  name: "refund-budget-headroom",
  description: "A refund must leave headroom in today's refund budget: budget left / amount above 1.1.",
  monitor: refundBudgetHeadroom,
  assuranceBasis: NO_MODEL_AT_GUARDED_CALL,
  gammaThreshold: 1.1,
});

export const emailToCustomer = new Invariant({
  name: "email-to-customer-on-file",
  description: "Only email the customer on the order, never an address the model introduced.",
  rule: emailIsToCustomerOnFile,
  assuranceBasis: NO_MODEL_AT_GUARDED_CALL,
});

const adapter = new LangGraphAdapter();
export const ownershipGate = adapter.compileInvariant(orderBelongsToCustomer);
export const refundRuleGate = adapter.compileInvariant(refundWithinOrder);
export const refundBudgetGate = adapter.compilePreNode(refundBudget);
export const emailGate = adapter.compileInvariant(emailToCustomer);

// ---- Terminal State and the way back ------------------------------------------------------------

const refundsSupervisor = new GovernanceAuthority({
  name: "Refunds Supervisor",
  escalationContact: "refunds-supervisor@example.com",
});

export function newInstance(): Instance {
  return new Instance({ instanceId: randomUUID(), identityKey: new IdentityKey(AGENT_ID) });
}

/** vsl-core never writes this for you: catching the violation and recording it is your code. */
export async function enterTerminalState(instance: Instance, reason: string, decisionId: string): Promise<void> {
  await ledger.current.write(LedgerEntryType.TERMINAL, AGENT_ID, {
    instanceId: instance.instanceId,
    decisionId,
    payload: { terminal_state: REFUND_ESCALATED.name, reason },
  });
}

/** The only way out of refund-escalated: a named human, with evidence, through vsl-core. */
export async function reEnable(
  instance: Instance,
  authorisedBy: string,
  rootCause: string,
  fix: string
): Promise<Instance> {
  const [next, transition] = requestReEnablement(instance, refundsSupervisor, authorisedBy, new Evidence(rootCause, fix));
  await ledger.current.write(LedgerEntryType.HUMAN_AUTHORISED_TRANSITION, AGENT_ID, {
    instanceId: instance.instanceId,
    payload: {
      authority: transition.authority.name,
      authorised_by: transition.authorisedBy,
      evidence_hash: transition.authorisationEvidenceHash,
    },
  });
  await ledger.current.write(LedgerEntryType.RE_ENABLEMENT, AGENT_ID, {
    instanceId: next.instanceId,
    causedBy: instance.instanceId,
    payload: { predecessor_instance_id: instance.instanceId },
  });
  return next;
}

// The refund agent as a LangGraph graph, with a VSL gate node in front of every action.
//
//   START -> plan -> ownershipGate -> dispatch -> refundRuleGate -> refundBudgetGate -> issueRefund
//                                                -> emailGate -> sendEmail -> END
//                                    dispatch -> emailGate (a receipt only);  dispatch -> answer (a lookup only) -> END
//   any gate that denies -> blocked -> END
//
// Each gate is its own node (gatedNode), and routeOnDenial sends the run to "blocked" when the gate
// denies, so the action node is reachable only through gates that passed. Keep that shape when you
// add actions: one gate node per decision point, a routeOnDenial edge after it, the action after that.
//
// The "model" here is ScriptedModel, so the starter runs offline. To use a real model, have it return
// the same plan (an action, an order id, an amount, a recipient), for example as structured output;
// the gates do not change.

import { randomUUID } from "node:crypto";
import { Annotation, END, START, StateGraph } from "@langchain/langgraph";
import { AutomationDeniedException, InvariantViolation, type Instance } from "@supersemantics/vsl-core";
import { gatedNode, routeOnDenial } from "@supersemantics/vsl-langgraph";

import { refundBook, type EmailCandidate, type OwnershipCandidate, type RefundCandidate } from "./domain.ts";
import {
  AGENT_ID,
  emailGate,
  enterTerminalState,
  newInstance,
  ownershipGate,
  reEnable,
  refundBudgetGate,
  refundRuleGate,
} from "./governance.ts";
import { issueRefund, lookupOrder, sendEmail } from "./tools.ts";

const last = <T>() => Annotation<T>({ reducer: (_old: T, next: T) => next, default: () => undefined as T });

export const RefundState = Annotation.Root({
  customerEmail: last<string>(), // the signed-in customer making the request
  request: last<string>(),
  decisionId: last<string>(),
  action: last<"refund" | "email" | "lookup">(),
  orderId: last<string>(),
  amount: last<number>(),
  emailTo: last<string>(),
  alreadyRefunded: last<number>(),
  budgetRemaining: last<number>(),
  refundId: last<string | undefined>(),
  reply: last<string>(),
  vslDenial: last<AutomationDeniedException | undefined>(),
});

type State = typeof RefundState.State;

/** Stands in for a model: turns a request into a plan. Deterministic, so tests can rely on it. */
export class ScriptedModel {
  plan(request: string): Partial<State> {
    const orderId = /\b([A-Z]\d{3})\b/.exec(request)?.[1] ?? "";
    const amount = /\$(\d+(?:\.\d+)?)/.exec(request);
    const email = /[\w.+-]+@[\w-]+\.[\w.]+/.exec(request)?.[0];
    const order = lookupOrder(orderId);
    const lowered = request.toLowerCase();
    if (lowered.includes("refund")) {
      return {
        action: "refund",
        orderId,
        amount: amount ? Number(amount[1]) : (order?.total ?? 0),
        emailTo: email ?? order?.customerEmail ?? "",
      };
    }
    if (lowered.includes("receipt") || lowered.includes("email")) {
      return { action: "email", orderId, emailTo: email ?? order?.customerEmail ?? "" };
    }
    return { action: "lookup", orderId };
  }
}

const model = new ScriptedModel();

function unknownOrder(): InvariantViolation {
  return new InvariantViolation({ reason: "unknown order", identityKey: AGENT_ID });
}

function ownershipCandidate(state: State): OwnershipCandidate {
  return { order: lookupOrder(state.orderId), customerEmail: state.customerEmail ?? "", decisionId: state.decisionId };
}

function refundCandidate(state: State): RefundCandidate {
  const order = lookupOrder(state.orderId);
  if (!order) throw unknownOrder();
  return {
    order,
    amount: state.amount ?? 0,
    alreadyRefunded: state.alreadyRefunded ?? 0,
    budgetRemaining: state.budgetRemaining,
    decisionId: state.decisionId,
  };
}

function emailCandidate(state: State): EmailCandidate {
  const order = lookupOrder(state.orderId);
  if (!order) throw unknownOrder();
  return { order, to: state.emailTo ?? "", decisionId: state.decisionId };
}

// ---- nodes ----------------------------------------------------------------------------------------

async function plan(state: State): Promise<Partial<State>> {
  const planned = model.plan(state.request);
  return {
    ...planned,
    alreadyRefunded: refundBook.current.refunded(planned.orderId ?? ""),
    budgetRemaining: refundBook.current.budgetRemaining,
  };
}

const passthrough = async () => ({});

async function doRefund(state: State): Promise<Partial<State>> {
  return { refundId: await issueRefund(state.orderId, state.amount) };
}

async function doSendEmail(state: State): Promise<Partial<State>> {
  const text = state.refundId
    ? `We refunded $${state.amount.toFixed(2)} for order ${state.orderId} (ref ${state.refundId}).`
    : `Here is the receipt for order ${state.orderId}.`;
  await sendEmail(state.emailTo, `Order ${state.orderId}`, text);
  return { reply: `Done. ${text} A confirmation went to ${state.emailTo}.` };
}

async function answer(state: State): Promise<Partial<State>> {
  const order = lookupOrder(state.orderId);
  if (!order) return { reply: "I could not find that order." };
  return { reply: `Order ${order.orderId}: $${order.total.toFixed(2)}, ${order.refundable ? "refundable" : "final sale"}.` };
}

async function blocked(state: State): Promise<Partial<State>> {
  const denial = state.vslDenial;
  if (denial instanceof InvariantViolation && denial.invariantName === "order-belongs-to-customer") {
    return { reply: "I can only help with orders on your own account." };
  }
  const reason = denial?.reason ?? "denied";
  if (state.refundId) return { reply: `Refunded, but the confirmation email was not sent: ${reason}.` };
  return { reply: `Not done: ${reason}. A person will review it.` };
}

export function buildGraph() {
  return new StateGraph(RefundState)
    .addNode("plan", plan)
    .addNode("ownershipGate", gatedNode(ownershipGate, passthrough, { candidateInputFn: ownershipCandidate }))
    .addNode("dispatch", passthrough)
    .addNode("refundRuleGate", gatedNode(refundRuleGate, passthrough, { candidateInputFn: refundCandidate }))
    .addNode("refundBudgetGate", gatedNode(refundBudgetGate, passthrough, { candidateInputFn: refundCandidate }))
    .addNode("issueRefund", doRefund)
    .addNode("emailGate", gatedNode(emailGate, passthrough, { candidateInputFn: emailCandidate }))
    .addNode("sendEmail", doSendEmail)
    .addNode("answer", answer)
    .addNode("blocked", blocked)
    .addEdge(START, "plan")
    .addEdge("plan", "ownershipGate")
    .addConditionalEdges("ownershipGate", routeOnDenial("blocked", "dispatch"))
    .addConditionalEdges("dispatch", (state: State) => state.action, {
      refund: "refundRuleGate",
      email: "emailGate",
      lookup: "answer",
    })
    .addConditionalEdges("refundRuleGate", routeOnDenial("blocked", "refundBudgetGate"))
    .addConditionalEdges("refundBudgetGate", routeOnDenial("blocked", "issueRefund"))
    .addEdge("issueRefund", "emailGate")
    .addConditionalEdges("emailGate", routeOnDenial("blocked", "sendEmail"))
    .addEdge("sendEmail", END)
    .addEdge("answer", END)
    .addEdge("blocked", END)
    .compile();
}

/** Runs requests through the graph, and stops after a hard-rule violation until a human says so. */
export class RefundAgent {
  private instance: Instance = newInstance();
  haltedReason: string | null = null;
  private readonly graph = buildGraph();

  /** `customerEmail` is the signed-in customer, from your authentication, never from the request. */
  async handle(customerEmail: string, request: string): Promise<string> {
    if (this.haltedReason) return `Refunds are paused pending supervisor review (${this.haltedReason}).`;
    const decisionId = randomUUID();
    const state = await this.graph.invoke({ customerEmail, request, decisionId });
    const denial = state.vslDenial;
    if (denial instanceof InvariantViolation && denial.terminalStateName) {
      // vsl-core threw; recording the Terminal State and stopping is this code's job.
      await enterTerminalState(this.instance, denial.reason, decisionId);
      this.haltedReason = denial.reason;
    }
    return state.reply;
  }

  async reEnable(authorisedBy: string, rootCause: string, fix: string): Promise<void> {
    this.instance = await reEnable(this.instance, authorisedBy, rootCause, fix);
    this.haltedReason = null;
  }
}

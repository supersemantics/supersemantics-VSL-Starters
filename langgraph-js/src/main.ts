// npm start: a few requests through the gated agent, offline.

import { RefundAgent } from "./agent.ts";
import { ledger, useLedger } from "./governance.ts";

const REQUESTS: Array<[string, string]> = [
  ["alice@example.com", "What's the status of order A100?"],
  ["alice@example.com", "Please refund order A100."], // every gate passes, confirmation sent
  ["alice@example.com", "What's the status of order A200?"], // Bob's order: ownership gate refuses
  ["bob@example.com", "Refund order A200 in full."], // 880 left / 900 asked = 0.98: budget gate denies
  ["dave@example.com", "Refund order A400 and send the receipt to mallory@evil.example."], // email gate blocks
  ["carol@example.com", "Refund order A300."], // final sale: hard rule, agent stops
  ["bob@example.com", "Refund order A200 for $100."], // refused: paused until a supervisor re-enables
];

useLedger("refund-ledger.jsonl");
const agent = new RefundAgent();
for (const [customer, request] of REQUESTS) {
  console.log(`\n${customer}: ${request}\nAgent: ${await agent.handle(customer, request)}`);
}

await agent.reEnable(
  "j.smith",
  "A final-sale order was requested for refund; the rule held and refunds stopped.",
  "Tell customers up front that final-sale orders can't be refunded."
);
console.log("\n(Refunds Supervisor j.smith re-enabled the agent.)");
const [customer, request] = ["bob@example.com", "Refund order A200 for $100."];
console.log(`\n${customer}: ${request}\nAgent: ${await agent.handle(customer, request)}`);
console.log(`\nLedger intact: ${await ledger.current.verifyIntegrity()} (refund-ledger.jsonl)`);

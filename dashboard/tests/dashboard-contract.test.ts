import { describe, expect, it } from "vitest";

import { agents, navItems, orders } from "../app/mock-data";

describe("Command Center v0.1 contract", () => {
  it("exposes every agreed operational view exactly once", () => {
    const routes = navItems.map(([route]) => route);
    expect(new Set(routes).size).toBe(routes.length);
    expect(routes).toEqual([
      "/",
      "/live-market",
      "/ai-agents",
      "/pixel-office",
      "/orders",
      "/positions",
      "/performance",
      "/logs",
      "/settings",
    ]);
  });

  it("keeps execution stopped while LIVE is locked", () => {
    const execution = agents.find((agent) => agent.role === "Execution Agent");
    expect(execution?.status).toBe("หยุดรอ");
    expect(execution?.task).toBe("LIVE blocked");
  });

  it("labels every displayed order as mock-safe PAPER evidence", () => {
    expect(orders).toHaveLength(3);
    expect(orders.every((order) => ["FILLED", "PAPER", "FILTERED"].includes(order.status))).toBe(true);
  });
});

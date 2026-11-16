import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ insights: vi.fn() }));
vi.mock("../api/client", async (original) => ({ ...(await original<typeof import("../api/client")>()), businessApi: api }));

import { Insights } from "./Insights";

const FINDING = { kind: "dead_stock", title: "Stock that has stopped selling", severity: "high", what: "2 products have not sold in 60 days.", why: "They stopped selling after March.", since: "2026-08-01",
  cost: { amount: 12345.6, basis: "cost of the units still on hand" }, evidence: [], fix: ["Stop reordering these products.", "Put them on offer."],
  rows: [{ product: "Stalled item", units: 40, never_sold: false }] };

beforeEach(() => vi.resetAllMocks());

describe("What your records show", () => {
  it("shows what, why, the cost, the evidence rows and what to do", async () => {
    api.insights.mockResolvedValue({ findings: [FINDING] });
    render(<Insights tenantId="t1" currency="KES" />);
    expect(await screen.findByText("Stock that has stopped selling")).toBeInTheDocument();
    expect(screen.getByText("2 products have not sold in 60 days.")).toBeInTheDocument();
    expect(screen.getByText(/They stopped selling after March/)).toBeInTheDocument();
    expect(screen.getByText(/KES 12,346/)).toBeInTheDocument();
    expect(screen.getByText("Stalled item")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Stop reordering these products.", "Put them on offer."]);
  });

  it("takes no space when there is nothing to say or the request fails", async () => {
    api.insights.mockResolvedValue({ findings: [] });
    const { container, rerender } = render(<Insights tenantId="t1" currency="KES" />);
    await vi.waitFor(() => expect(api.insights).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    api.insights.mockRejectedValue(new Error("down"));
    rerender(<Insights tenantId="t2" currency="KES" />);
    await vi.waitFor(() => expect(api.insights).toHaveBeenCalledTimes(2));
    expect(container).toBeEmptyDOMElement();
  });
});

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

  it("shows what is going well after the problems, with its gain and how to keep it going", async () => {
    const good = { ...FINDING, kind: "sales_growth", title: "Sales are growing", severity: "good", what: "Sales are up 33% on the 30 days before.", why: "Most of the rise came from Riser item.", cost: undefined,
      gain: { amount: 6000, basis: "more than the 30 days before" }, fix: ["Keep the rising products in stock."], rows: [] };
    api.insights.mockResolvedValue({ findings: [FINDING], strengths: [good] });
    render(<Insights tenantId="t1" currency="KES" />);
    const titles = (await screen.findAllByRole("heading", { level: 3 })).map((h) => h.textContent);
    expect(titles).toEqual(["Stock that has stopped selling", "Sales are growing"]);
    expect(screen.getByText(/\+KES 6,000/)).toBeInTheDocument();
    expect(screen.getByText("How to keep it going")).toBeInTheDocument();
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

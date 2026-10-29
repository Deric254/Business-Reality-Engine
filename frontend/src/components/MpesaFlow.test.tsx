import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ create: vi.fn(), contributions: vi.fn(), testEvent: vi.fn(), newLink: vi.fn(), me: vi.fn() }));
vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  connectorApi: { create: api.create, contributions: api.contributions, testEvent: api.testEvent, newLink: api.newLink },
  tenantApi: { me: api.me },
}));

import { MpesaFlow } from "./MpesaFlow";

const CONNECTOR = { id: "c1", name: "M-Pesa payments", connector_type: "webhook", webhook_url: "/api/webhooks/c1/secret-token" };
const renderFlow = (props: Partial<React.ComponentProps<typeof MpesaFlow>> = {}) =>
  render(<MemoryRouter><MpesaFlow tenantId="t1" onClose={vi.fn()} onChanged={vi.fn()} {...props} /></MemoryRouter>);

beforeEach(() => {
  vi.resetAllMocks();
  api.me.mockResolvedValue({ base_currency: "KES" });
  api.contributions.mockResolvedValue({});
  api.create.mockResolvedValue(CONNECTOR);
});

describe("M-Pesa flow", () => {
  it("explains itself in plain words, offers a way out, and uses no technical words", () => {
    const { container } = renderFlow();
    expect(screen.getByRole("heading", { name: /every M-Pesa payment as it happens/i })).toBeInTheDocument();
    expect(screen.getByText(/A spreadsheet or file/)).toBeInTheDocument();                // for someone with no Paybill or Till
    for (const jargon of ["webhook", "json", "post a", "daraja confirmation url", "c2b"]) expect((container.textContent ?? "").toLowerCase()).not.toContain(jargon);
  });

  it("makes the link with the business's own currency and shows the three steps", async () => {
    const onChanged = vi.fn();
    renderFlow({ onChanged });
    await userEvent.click(screen.getByRole("button", { name: /Create my M-Pesa link/ }));
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ tenant_id: "t1", connector_type: "webhook", config: { preset: "mpesa_c2b", default_currency: "KES" } }));
    expect(await screen.findByLabelText("link")).toHaveTextContent("/api/webhooks/c1/secret-token");
    expect(screen.getByRole("button", { name: /Copy link/ })).toBeInTheDocument();
    for (const step of ["Your M-Pesa link", "Give the link to Safaricom", "Check that it works"]) expect(screen.getByText(step)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("proves the link works with a sample payment, in words, without adding anything", async () => {
    api.testEvent.mockResolvedValue({ would_create: { entity: "sale", entity_id: "SIM1", amount: 1500, currency: "KES", customer_id: "254700000000", time: "2026-10-20T12:30:45+03:00" } });
    renderFlow({ existing: CONNECTOR as never });
    await userEvent.click(screen.getByRole("button", { name: /Test my link/ }));
    expect(await screen.findByText(/It works/)).toBeInTheDocument();
    expect(screen.getByText(/A payment of KES 1,500 from 254700000000 would appear as a sale on/)).toBeInTheDocument();
    expect(api.testEvent).toHaveBeenCalledWith("t1", "c1");
  });

  it("celebrates the first real payment when it arrives", async () => {
    api.contributions.mockResolvedValue({ c1: { counted: 3, matched: 0, rejected: 0 } });
    renderFlow({ existing: CONNECTOR as never });
    expect(await screen.findByText(/Your first M-Pesa payment arrived/)).toBeInTheDocument();
    expect(screen.getByText(/3 payments so far/)).toBeInTheDocument();
  });

  it("offers a new link, not a lost one, for a source whose link was shown before", async () => {
    api.newLink.mockResolvedValue({ webhook_url: "/api/webhooks/c1/fresh-token" });
    renderFlow({ existing: { ...CONNECTOR, webhook_url: undefined } as never });
    expect(screen.getByText(/shown only once/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Make a new link/ }));
    expect(await screen.findByLabelText("link")).toHaveTextContent("/api/webhooks/c1/fresh-token");
  });

  it("shows the server's reason when something goes wrong", async () => {
    const { ApiError } = await import("../api/client");
    api.create.mockRejectedValue(new ApiError(400, "Not allowed"));
    renderFlow();
    await userEvent.click(screen.getByRole("button", { name: /Create my M-Pesa link/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Not allowed"));
  });
});

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ list: vi.fn(), me: vi.fn() }));
vi.mock("../api/client", async (original) => ({ ...(await original<typeof import("../api/client")>()), systemApi: { list: api.list }, tenantApi: { me: api.me } }));

import { ConnectHub } from "./ConnectHub";

const renderHub = (props = {}) => render(<MemoryRouter><ConnectHub tenantId="t1" firstTime onClose={vi.fn()} onChanged={vi.fn()} onConnected={vi.fn()} onTypeByHand={vi.fn()} {...props} /></MemoryRouter>);
const cards = () => screen.getAllByRole("button").map((b) => b.textContent ?? "");

beforeEach(() => { vi.resetAllMocks(); api.list.mockResolvedValue([]); api.me.mockResolvedValue({ base_currency: "KES" }); });

describe("Connect hub", () => {
  it("opens on five plain choices, not a dozen connectors", () => {
    renderHub();
    expect(screen.getByRole("heading", { name: "Let's connect your business" })).toBeInTheDocument();
    const choices = cards().filter((t) => !t.startsWith("Type your"));
    expect(choices).toHaveLength(5);
    for (const title of ["My business software", "M-Pesa payments", "A spreadsheet or file", "My online shop", "Something else"]) expect(choices.some((c) => c.startsWith(title))).toBe(true);
  });

  it("shows nothing technical until someone asks for 'Something else'", () => {
    const { container } = renderHub();
    const t = (container.textContent ?? "").toLowerCase();
    for (const jargon of ["webhook", "sftp", "agent", "database", "api"]) expect(t).not.toContain(jargon);
  });

  it("opens the matching short path for each choice", async () => {
    renderHub();
    await userEvent.click(screen.getByRole("button", { name: /A spreadsheet or file/ }));
    const files = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    expect(files.some((t) => t.startsWith("Excel or CSV file"))).toBe(true);
    expect(files.some((t) => t.startsWith("Google Sheets"))).toBe(true);
    expect(files.some((t) => t.startsWith("WooCommerce"))).toBe(false);                     // each path shows only its own choices
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    await userEvent.click(screen.getByRole("button", { name: /My online shop/ }));
    expect(screen.getByRole("button", { name: /WooCommerce/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Shopify/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Excel or CSV/ })).toBeNull();
  });

  it("keeps the technical connectors under 'Something else', and says the two kinds of helper app apart", async () => {
    renderHub();
    await userEvent.click(screen.getByRole("button", { name: /Something else/ }));
    expect(screen.getByRole("heading", { name: "Other ways to connect" })).toBeInTheDocument();
    for (const t of ["Live feed from my own system", "Database", "Custom helper app"]) expect(screen.getByRole("button", { name: new RegExp(t) })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Custom helper app/ })).toHaveTextContent(/use "My business software" instead/);
  });

  it("starts the software path directly when asked to (the 'Show me the steps' button)", async () => {
    api.list.mockResolvedValue([{ system: "smepro", label: "smePro", what: "x", connected: true, connector_id: "c1", state: "waiting", detail: null, checking: false, paused: false, download_url: null, status: {}, last_pass: null }]);
    renderHub({ startSystem: "smepro" });
    expect(await screen.findByRole("heading", { name: "Connect smePro" })).toBeInTheDocument();
  });

  it("lets someone with no system type entries by hand, and a returning owner cancel", async () => {
    const onTypeByHand = vi.fn(), onClose = vi.fn();
    const { rerender } = renderHub({ onTypeByHand });
    await userEvent.click(screen.getByRole("button", { name: /Type your sales and expenses by hand/ }));
    expect(onTypeByHand).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();                     // the first time there is nothing to cancel back to
    rerender(<MemoryRouter><ConnectHub tenantId="t1" firstTime={false} onClose={onClose} onChanged={vi.fn()} onConnected={vi.fn()} onTypeByHand={vi.fn()} /></MemoryRouter>);
    await userEvent.click(within(document.body).getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
  });
});

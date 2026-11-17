import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ discovery: vi.fn(), scan: vi.fn(), propose: vi.fn(), confirmReading: vi.fn() }));
vi.mock("../api/client", async (original) => ({ ...(await original<typeof import("../api/client")>()), systemApi: api }));

import { SystemReview } from "./SystemReview";

const PATH = "C:\\Users\\Ann\\duka\\duka.db";
const col = (name: string) => ({ name, type: "TEXT", pk: false });
const ROLES = { sale: { label: "Money received (sales)", required: ["date", "amount"], amount_label: "Amount" }, expense: { label: "Money paid out (expenses)", required: ["date", "amount"], amount_label: "Amount" }, refund: { label: "Money given back (refunds)", required: ["date", "amount"], amount_label: "Amount" }, stock: { label: "Stock on hand", amount_label: "Value of one unit", required: ["product", "quantity"] } };
const FOUND = { path: PATH, size: 100000, modified: 1, error: null, tables: [
  { name: "tbl_sales", rows: 120, columns: [col("sale_no"), col("sale_date"), col("grand_total")] },
  { name: "supplier_bills", rows: 30, columns: [col("bill_id"), col("paid_on"), col("amount")] },
] };
const PROPOSAL = { business: { kind: "Hardware shop", description: "Sells goods." }, currency: "KES", notes: [], used_ai: true,
  tables: [{ table: "tbl_sales", role: "sale", id: "sale_no", date: "sale_date", amount: "grand_total", reason: "One sale per row." }] };
const link = { system: "custom", label: "Your own system", what: "", connected: true, connector_id: "c1", state: "needs_mapping" } as never;
const show = () => render(<SystemReview tenantId="t1" link={link} reload={vi.fn()} setError={vi.fn()} />);

beforeEach(() => {
  vi.resetAllMocks();
  api.discovery.mockResolvedValue({ roles: ROLES, scanned_at: "x", found: [FOUND], proposals: {}, chosen: null, scan_requested: false });
  api.propose.mockResolvedValue(PROPOSAL);
  api.confirmReading.mockResolvedValue({ chosen: {}, notes: [] });
});

describe("Reviewing what was found", () => {
  it("shows the databases by name and counts, and the AI's reading with its reason, only after one of several is chosen", async () => {
    api.discovery.mockResolvedValue({ roles: ROLES, scanned_at: "x", found: [FOUND, { ...FOUND, path: "D:\\old\\old.db" }], proposals: {}, chosen: null, scan_requested: false });
    show();
    expect(await screen.findByText("duka/duka.db")).toBeInTheDocument();
    expect(screen.queryByText("Hardware shop")).not.toBeInTheDocument();
    expect(api.propose).not.toHaveBeenCalled();                       // with a choice to make, nothing is chosen for the owner
    await userEvent.click(screen.getAllByRole("radio")[0]);
    expect(api.propose).toHaveBeenCalledWith("t1", "c1", PATH);
    expect(await screen.findByDisplayValue("Hardware shop")).toBeInTheDocument();
    expect(screen.getByText("One sale per row.")).toBeInTheDocument();
    expect(screen.getByText(/Read with AI/)).toBeInTheDocument();
  });

  it("lets the owner correct a column and a table, then confirms exactly what is on the screen", async () => {
    show();
    await screen.findByDisplayValue("Hardware shop");
    await userEvent.selectOptions(screen.getByLabelText("What tbl_sales holds"), "refund");
    await userEvent.selectOptions(screen.getByLabelText("Add another table"), "supplier_bills");
    expect(screen.getByRole("button", { name: "Each table needs its key columns filled in" })).toBeDisabled();
    const bills = screen.getByText("supplier_bills").closest("div")!.parentElement!;
    await userEvent.selectOptions(within(bills).getByLabelText("Date"), "paid_on");
    await userEvent.selectOptions(within(bills).getByLabelText("Amount"), "amount");
    await userEvent.selectOptions(within(bills).getByLabelText("What supplier_bills holds"), "expense");
    await userEvent.click(screen.getByRole("button", { name: "This is right. Start reading" }));
    await waitFor(() => expect(api.confirmReading).toHaveBeenCalled());
    const body = api.confirmReading.mock.calls[0][2];
    expect(body.path).toBe(PATH);
    expect(body.tables.map((t: { table: string; role: string }) => [t.table, t.role])).toEqual([["tbl_sales", "refund"], ["supplier_bills", "expense"]]);
  });

  it("has the reading ready when exactly one database is found, and asks only for the confirming click", async () => {
    show();
    expect(await screen.findByDisplayValue("Hardware shop")).toBeInTheDocument();
    expect(api.propose).toHaveBeenCalledTimes(1);
    expect(api.propose).toHaveBeenCalledWith("t1", "c1", PATH);
    expect(api.confirmReading).not.toHaveBeenCalled();               // reading starts only when the owner says the reading is right
    await new Promise((r) => setTimeout(r, 50));
    expect(api.propose).toHaveBeenCalledTimes(1);                    // and the refresh every few seconds does not propose again
  });

  it("does not propose again once a database has been confirmed", async () => {
    api.discovery.mockResolvedValue({ roles: ROLES, scanned_at: "x", found: [FOUND], proposals: {}, chosen: { path: PATH }, scan_requested: false });
    show();
    await screen.findByText("duka/duka.db");
    expect(api.propose).not.toHaveBeenCalled();
  });

  it("says plainly when nothing was found and offers to look again", async () => {
    api.discovery.mockResolvedValue({ roles: ROLES, scanned_at: null, found: [], proposals: {}, chosen: null, scan_requested: false });
    show();
    expect(await screen.findByText("No readable database found yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Look again" }));
    expect(api.scan).toHaveBeenCalledWith("t1", "c1");
  });

  it("asks for the columns each kind of table needs: stock needs a product and a quantity, not a date and an amount", async () => {
    show();
    await screen.findByDisplayValue("Hardware shop");
    await userEvent.selectOptions(screen.getByLabelText("What tbl_sales holds"), "stock");
    expect(screen.getByRole("button", { name: "Each table needs its key columns filled in" })).toBeDisabled();
    expect(screen.getByLabelText("Quantity")).toBeInTheDocument();
    expect(screen.getByLabelText("Value of one unit")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Product"), "sale_no");
    await userEvent.selectOptions(screen.getByLabelText("Quantity"), "grand_total");
    expect(screen.getByRole("button", { name: "This is right. Start reading" })).toBeEnabled();
  });
});

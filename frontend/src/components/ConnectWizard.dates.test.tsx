import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ inspectFile: vi.fn(), create: vi.fn(), upload: vi.fn(), disconnect: vi.fn(), me: vi.fn(), dbTypes: vi.fn() }));
vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  connectorApi: { inspectFile: api.inspectFile, create: api.create, upload: api.upload, disconnect: api.disconnect, databaseTypes: api.dbTypes },
  tenantApi: { me: api.me },
}));

import type { Inspection } from "../api/client";
import { ConnectWizard } from "./ConnectWizard";

const QUALITY = { blank_rows: 0, blank_columns: [], unnamed_columns: [], unreadable_dates: 0, unreadable_amounts: 0, rows_without_amount: 0 };
const inspection = (suggested: Record<string, string>, kind: "transactions" | "partial" | "list", extra: Partial<Inspection> = {}): Inspection => ({
  columns: ["Item", "Amount", "Unnamed: 2"], row_count: 2, sample: [{ Item: "Sugar", Amount: 500, "Unnamed: 2": "Wanjiru" }], suggested_mapping: { entity: "sale", event_type: "sale.created", ...suggested },
  source: "rules", reasons: {}, guessed: [], quality: { ...QUALITY, kind }, ...extra,
});

async function chooseFile(container: HTMLElement) {
  await userEvent.click(screen.getByRole("button", { name: /Excel or CSV file/ }));
  await userEvent.upload(container.querySelector('input[type="file"]') as HTMLInputElement, new File(["x"], "sales.csv", { type: "text/csv" }));
  await userEvent.click(screen.getByRole("button", { name: /Read the file and detect columns/ }));
}
const renderWizard = (onDone = vi.fn()) => render(<ConnectWizard tenantId="t1" kinds={["file", "sheets"]} onClose={vi.fn()} onDone={onDone} />);

beforeEach(() => {
  vi.resetAllMocks();
  api.me.mockResolvedValue({ base_currency: "KES" });
  api.create.mockResolvedValue({ id: "c1", name: "Sales" });
  api.upload.mockResolvedValue({ rows_processed: 2, new_rows: 2, duplicates: 0 });
});

describe("a file with no date column", () => {
  it("asks when it happened, in plain words, and holds the Connect button until it knows", async () => {
    api.inspectFile.mockResolvedValue(inspection({ amount_col: "Amount", product_id_col: "Item" }, "partial"));
    const { container } = renderWizard();
    await chooseFile(container);
    expect(await screen.findByText("When did these happen?")).toBeInTheDocument();
    expect(screen.getByText(/couldn't find a date in your file/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect and import/ })).toBeDisabled();
    expect(screen.getByText("Say when these happened, above, to continue.")).toBeInTheDocument();
  });

  it("dates the whole file with one day the owner confirms, and sends exactly that", async () => {
    api.inspectFile.mockResolvedValue(inspection({ amount_col: "Amount", product_id_col: "Item" }, "partial"));
    const onDone = vi.fn();
    const { container } = renderWizard(onDone);
    await chooseFile(container);
    await userEvent.click(await screen.findByRole("button", { name: /It's all from one day/ }));
    const day = screen.getByLabelText(/All of these happened on/);
    expect(day).toHaveValue(new Date().toISOString().slice(0, 10));
    await userEvent.clear(day); await userEvent.type(day, "2026-10-05");
    await userEvent.click(screen.getByRole("button", { name: /Connect and import/ }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const config = api.create.mock.calls[0][0].config;
    expect(config.default_date).toBe("2026-10-05");
    expect(config.timestamp_col).toBeUndefined();
  });

  it("lets the owner pick a date column instead, and then sends the column, never a day", async () => {
    api.inspectFile.mockResolvedValue(inspection({ amount_col: "Amount" }, "partial"));
    const { container } = renderWizard();
    await chooseFile(container);
    await userEvent.click(await screen.findByRole("button", { name: /A column holds the date/ }));
    await userEvent.selectOptions(screen.getByLabelText(/When it happened/), "Item");
    await userEvent.click(screen.getByRole("button", { name: /Connect and import/ }));
    await waitFor(() => expect(api.create).toHaveBeenCalled());
    const config = api.create.mock.calls[0][0].config;
    expect(config.timestamp_col).toBe("Item");
    expect(config.default_date).toBeUndefined();
  });

  it("will not connect a plain list: no date column and no money", async () => {
    api.inspectFile.mockResolvedValue(inspection({}, "list"));
    const { container } = renderWizard();
    await chooseFile(container);
    expect(await screen.findByText(/This looks like a list/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /It's all from one day/ }));
    expect(screen.getByText("Pick the column that holds the money first, or connect something else.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect and import/ })).toBeDisabled();
  });

  it("shows a header left blank as (no name) with what is in it, so it can be picked", async () => {
    api.inspectFile.mockResolvedValue(inspection({ amount_col: "Amount" }, "partial", { quality: { ...QUALITY, kind: "partial", unnamed_columns: ["Unnamed: 2"] } }));
    const { container } = renderWizard();
    await chooseFile(container);
    expect(await screen.findByText(/1 column has data but no name/)).toBeInTheDocument();
    expect(screen.getAllByRole("option", { name: "(no name) for example “Wanjiru”" }).length).toBeGreaterThan(0);
  });

  it("goes straight through when the file already has a date: no question asked", async () => {
    api.inspectFile.mockResolvedValue(inspection({ timestamp_col: "Item", amount_col: "Amount" }, "transactions"));
    const { container } = renderWizard();
    await chooseFile(container);
    await screen.findByText("What we found");
    expect(screen.queryByText("When did these happen?")).toBeNull();
    expect(screen.getByRole("button", { name: /Connect and import/ })).toBeEnabled();
  });
});

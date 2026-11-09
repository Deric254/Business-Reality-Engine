import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ mine: vi.fn() }));
vi.mock("../api/client", async (original) => {
  const real = await original<typeof import("../api/client")>();
  return { ...real, licenceApi: { mine: mocks.mine } };
});

import { LICENCE_READ_ONLY_EVENT, type MyLicence } from "../api/client";
import { noticeText } from "../lib/payWindow";
import { LicenceNotice } from "./Licence";

const BASE: MyLicence = { ok: true, kind: "licensed", read_only: false, grace: false, payable: true, days_left: 30, licence_id: "lic-1", amount_cents: 4500, currency: "USD", interval_months: 1 };
const OVERDUE: MyLicence = { ...BASE, ok: false, kind: "overdue", read_only: true, days_left: 0 };

beforeEach(() => { mocks.mine.mockReset(); });
afterEach(() => vi.restoreAllMocks());

describe("noticeText", () => {
  it("says nothing for a healthy, a free or a not-yet-loaded licence", () => {
    expect(noticeText(BASE)).toBeNull();
    expect(noticeText({ ...BASE, payable: false, kind: "free" })).toBeNull();
  });
  it("warns in the last 14 days, in grace, and when view-only", () => {
    expect(noticeText({ ...BASE, days_left: 14 })).toBe("Your subscription ends in 14 days. Renew to keep full access.");
    expect(noticeText({ ...BASE, days_left: 1 })).toContain("1 day.");
    expect(noticeText({ ...BASE, days_left: 0, grace: true, grace_days_left: 3 })).toBe("Subscription expired. Renew within 3 days to keep full access.");
    expect(noticeText(OVERDUE)).toContain("view-only");
  });
});

describe("LicenceNotice", () => {
  it("shows no notice and no lock for a healthy licence", async () => {
    mocks.mine.mockResolvedValue(BASE);
    const { container } = render(<LicenceNotice />);
    await waitFor(() => expect(mocks.mine).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the view-only notice with a Renew button that opens the pay window with this licence's own amount and reference", async () => {
    mocks.mine.mockResolvedValue(OVERDUE);
    render(<LicenceNotice />);
    expect(await screen.findByText(/this is view-only/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Renew" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("$45.00");
    expect(dialog).toHaveTextContent("dericmarangu@gmail.com");
    expect(dialog).toHaveTextContent("LIC-BRE-lic-1-M");
  });

  it("opens PayPal and the proof email only through the allowlist, then remembers the proof was sent", async () => {
    mocks.mine.mockResolvedValue(OVERDUE);
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<LicenceNotice />);
    await userEvent.click(await screen.findByRole("button", { name: "Renew" }));
    await userEvent.click(screen.getByRole("button", { name: "Open PayPal" }));
    expect(open).toHaveBeenLastCalledWith("https://www.paypal.com/myaccount/transfer/homepage", "_blank", "noopener,noreferrer");
    await userEvent.click(screen.getByRole("button", { name: "Email my proof" }));
    expect(String(open.mock.calls.at(-1)?.[0])).toMatch(/^mailto:dericmarangu@gmail\.com\?/);
    expect(screen.getByText(/Proof already sent on/)).toHaveTextContent("Please do not pay again");
    await userEvent.click(screen.getByRole("button", { name: "I need to pay again" }));
    expect(screen.getByRole("button", { name: "Email my proof" })).toBeInTheDocument();
  });

  it("opens the pay window once when a change is refused for an expired subscription", async () => {
    mocks.mine.mockResolvedValue(OVERDUE);
    render(<LicenceNotice />);
    await screen.findByText(/this is view-only/i);
    window.dispatchEvent(new Event(LICENCE_READ_ONLY_EVENT));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

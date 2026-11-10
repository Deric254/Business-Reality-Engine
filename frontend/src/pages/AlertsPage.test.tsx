import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ listAlerts: vi.fn(), alertValue: vi.fn(), runChecks: vi.fn() }));
vi.mock("../api/client", async (original) => ({ ...(await original<typeof import("../api/client")>()), api }));
vi.mock("../components/Shell", () => ({ Shell: ({ children }: { children: (t: unknown) => unknown }) => <>{children({ id: "t1", name: "Shop" }) as never}</> }));
vi.mock("../lib/liveContext", () => ({ useOnLiveChange: () => undefined }));

import { AlertsPage } from "./AlertsPage";

const ALERT = { id: "a1", severity: "high", state: "detected", title: "Transport spending is up 34%", what_happened: "It rose.", confidence: 0.9, data_coverage_pct: 100, recommended_action: "Check fuel.", at_stake: 46000 };
const V = { days: 30, open_count: 1, open_measured: 1, open_at_stake: 46000, raised: 1, raised_at_stake: 46000, back_to_normal: 2, currency: "KES" };
const show = () => render(<MemoryRouter><AlertsPage /></MemoryRouter>);

beforeEach(() => { vi.resetAllMocks(); api.listAlerts.mockResolvedValue([ALERT]); });

describe("AlertsPage money line", () => {
  it("shows the line once the figures arrive", async () => {
    api.alertValue.mockResolvedValue(V);
    show();
    expect(await screen.findByText(/About KES 46,000 off your usual across 1 open alert/)).toBeTruthy();
    expect(screen.getByText("Transport spending is up 34%")).toBeTruthy();
  });
  it("still shows every alert, with no line and no error, when the figures cannot be fetched", async () => {
    api.alertValue.mockRejectedValue(new Error("server down"));
    show();
    expect(await screen.findByText("Transport spending is up 34%")).toBeTruthy();
    await waitFor(() => expect(api.alertValue).toHaveBeenCalled());
    expect(screen.queryByText(/off your usual/)).toBeNull();
    expect(screen.queryByText(/Couldn't load alerts/)).toBeNull();
  });
  it("shows nothing extra when there is nothing to say", async () => {
    api.alertValue.mockResolvedValue({ ...V, open_at_stake: 0, open_measured: 0, back_to_normal: 0 });
    show();
    expect(await screen.findByText("Transport spending is up 34%")).toBeTruthy();
    expect(screen.queryByText(/off your usual|went back to normal/)).toBeNull();
  });
  it("shows the alerts even if the figures never answer at all", async () => {
    api.alertValue.mockReturnValue(new Promise(() => undefined));
    show();
    expect(await screen.findByText("Transport spending is up 34%")).toBeTruthy();
  });
});

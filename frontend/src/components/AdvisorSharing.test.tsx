import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ me: vi.fn(), update: vi.fn() }));
vi.mock("../api/client", async (original) => {
  const real = await original<typeof import("../api/client")>();
  return { ...real, tenantApi: { me: mocks.me, update: mocks.update } };
});

import { AdvisorSharing } from "./AdvisorSharing";

beforeEach(() => vi.resetAllMocks());

describe("AdvisorSharing", () => {
  it("is off until the owner turns it on, and says exactly what is shared", async () => {
    mocks.me.mockResolvedValue({ share_alerts_with_advisor: false });
    mocks.update.mockResolvedValue({ share_alerts_with_advisor: true });
    render(<AdvisorSharing />);
    const box = await screen.findByRole("checkbox", { name: /share serious alert headlines/i });
    expect(box).not.toBeChecked();
    expect(screen.getByText(/never see your sales, customers or other records/i)).toBeInTheDocument();
    await userEvent.click(box);
    expect(mocks.update).toHaveBeenCalledWith({ share_alerts_with_advisor: true });
    await waitFor(() => expect(box).toBeChecked());
  });

  it("shows the reason when saving fails and leaves the switch where it was", async () => {
    mocks.me.mockResolvedValue({ share_alerts_with_advisor: true });
    mocks.update.mockRejectedValue(new Error("boom"));
    render(<AdvisorSharing />);
    const box = await screen.findByRole("checkbox");
    await userEvent.click(box);
    expect(await screen.findByRole("alert")).toHaveTextContent(/something went wrong/i);
    expect(box).toBeChecked();
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ login: vi.fn(), status: vi.fn() }));
vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  login: api.login,
  setupApi: { status: api.status },
}));

import { LoginPage } from "./LoginPage";

async function fillIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText(/email/i), "owner@example.com");
  await user.type(screen.getByLabelText(/password/i), "correct-horse-1");
}

beforeEach(() => {
  vi.resetAllMocks();
  api.status.mockResolvedValue({ has_business: true, setup_key_configured: true });
  api.login.mockResolvedValue(undefined);
});

describe("sign in", () => {
  it("does not keep the person signed in on the device unless they ask", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    await fillIn(user);
    expect(screen.getByRole("checkbox", { name: /keep me signed in on this device/i })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));
    await waitFor(() => expect(api.login).toHaveBeenCalledWith("owner@example.com", "correct-horse-1", false));
  });

  it("keeps them signed in on the device when they tick the box", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    await fillIn(user);
    await user.click(screen.getByRole("checkbox", { name: /keep me signed in on this device/i }));
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));
    await waitFor(() => expect(api.login).toHaveBeenCalledWith("owner@example.com", "correct-horse-1", true));
  });

  it("says plainly when the password is wrong", async () => {
    const { ApiError } = await import("../api/client");
    api.login.mockRejectedValue(new ApiError(401, "no"));
    const user = userEvent.setup();
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  });
});

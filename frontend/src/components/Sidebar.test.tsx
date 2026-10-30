import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getCoverage: vi.fn(), whoami: vi.fn(), questions: vi.fn() }));
vi.mock("../api/client", async (original) => {
  const real = await original<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, getCoverage: mocks.getCoverage }, masterApi: { ...real.masterApi, whoami: mocks.whoami }, securityQuestionsApi: { ...real.securityQuestionsApi, get: mocks.questions } };
});

import { cache } from "../api/cache";
import { setTokens } from "../api/session";
import type { Tenant } from "../api/client";
import { Sidebar } from "./Sidebar";

const TENANT = { id: "t1", name: "Demo Shop" } as Tenant;
const open = (path = "/", tenants: Tenant[] = [TENANT]) => {
  const view = render(<MemoryRouter initialEntries={[path]}><Sidebar tenants={tenants} activeTenant={tenants[0]} onTenantChange={vi.fn()} /></MemoryRouter>);
  const bar = view.container.querySelector("nav.tabbar") as HTMLElement;       // the phone tab bar (the side rail is the other "Main" navigation)
  return { ...view, bar };
};

beforeEach(() => {
  vi.resetAllMocks();
  cache.recoverySet = true;                                                  // security questions already set, unless a test says otherwise
  cache.coverage.clear();
  mocks.getCoverage.mockResolvedValue([]);
  mocks.whoami.mockResolvedValue(false);
  mocks.questions.mockResolvedValue({ questions: ["q"] });
});

afterEach(() => setTokens(null));

describe("phone tab bar", () => {
  it("puts the four main pages under the thumb, and everything else under More", async () => {
    const { bar } = open();
    const tabs = within(bar).getAllByRole("link").map((a) => a.textContent);
    expect(tabs).toEqual(["Overview", "Analytics", "Alerts", "Data sources"]);
    expect(within(bar).getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(within(bar).getByRole("button", { name: "More" }));
    const menu = document.getElementById("more-menu") as HTMLElement;
    expect(within(menu).getAllByRole("link").map((a) => a.textContent)).toEqual(["Targets", "Business", "Accuracy", "Settings"]);
  });

  it("shows which business this is inside More, as the side rail does on a computer", async () => {
    const { bar } = open();
    await userEvent.click(within(bar).getByRole("button", { name: "More" }));
    expect(within(document.getElementById("more-menu") as HTMLElement).getByText("Demo Shop")).toBeInTheDocument();
  });

  it("closes with Escape and gives focus back to More; tapping the dimmed page closes it too", async () => {
    const { bar, container } = open();
    const more = within(bar).getByRole("button", { name: "More" });
    await userEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(document.getElementById("more-menu")).toBeNull();
    expect(more).toHaveFocus();
    await userEvent.click(more);
    await userEvent.click(container.querySelector(".bg-scrim") as HTMLElement);
    expect(document.getElementById("more-menu")).toBeNull();
  });

  it("closes when a page is chosen, and More is marked while you are on one of its pages", async () => {
    const { bar } = open("/goals");
    const more = within(bar).getByRole("button", { name: "More" });
    expect(more.className).toContain("text-info");                            // on Targets, which lives under More
    await userEvent.click(more);
    await userEvent.click(within(document.getElementById("more-menu") as HTMLElement).getByRole("link", { name: "Settings" }));
    expect(document.getElementById("more-menu")).toBeNull();
  });

  it("is not marked while you are on a main tab", () => {
    const { bar } = open("/alerts");
    expect(within(bar).getByRole("button", { name: "More" }).className).not.toContain("text-info");
    expect(within(bar).getByRole("link", { name: "Alerts" }).className).toContain("text-info");
  });

  it("lets a person with several businesses switch between them from More", async () => {
    const other = { id: "t2", name: "Second Shop" } as Tenant;
    const onTenantChange = vi.fn();
    render(<MemoryRouter><Sidebar tenants={[TENANT, other]} activeTenant={TENANT} onTenantChange={onTenantChange} /></MemoryRouter>);
    const bar = document.querySelector("nav.tabbar") as HTMLElement;
    await userEvent.click(within(bar).getByRole("button", { name: "More" }));
    await userEvent.selectOptions(within(document.getElementById("more-menu") as HTMLElement).getByRole("combobox", { name: "Business" }), "t2");
    expect(onTenantChange).toHaveBeenCalledWith(other);
  });

  it("gives the master account its Licences page under More", async () => {
    mocks.whoami.mockResolvedValue(true);
    setTokens({ access_token: "a", refresh_token: "master-sign-in" });     // a sign-in nobody has asked about yet
    const { bar } = open();
    await userEvent.click(within(bar).getByRole("button", { name: "More" }));
    expect(await within(document.getElementById("more-menu") as HTMLElement).findByRole("link", { name: "Licences" })).toBeInTheDocument();
  });
});

describe("account recovery reminder", () => {
  it("puts a dot, with words for screen readers, on Settings and on More while security questions are missing", async () => {
    cache.recoverySet = null;
    mocks.questions.mockResolvedValue({ questions: [] });
    const { bar } = open();
    await waitFor(() => expect(within(bar).getByRole("button", { name: /More.*action needed/ })).toBeInTheDocument());
    await userEvent.click(within(bar).getByRole("button", { name: /More/ }));
    expect(within(document.getElementById("more-menu") as HTMLElement).getByRole("link", { name: /Settings.*action needed/ })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Settings.*action needed/ }).length).toBe(2);   // the side rail's link too
  });

  it("shows nothing when they are set, and asks the server only once however many places want to know", async () => {
    cache.recoverySet = null;
    open();
    await waitFor(() => expect(mocks.questions).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/action needed/)).toBeNull();
    expect(mocks.questions).toHaveBeenCalledTimes(1);
  });

  it("leaves the dot out when the server cannot be asked, instead of nagging over a bad connection", async () => {
    cache.recoverySet = null;
    mocks.questions.mockRejectedValue(new Error("offline"));
    open();
    await waitFor(() => expect(mocks.questions).toHaveBeenCalled());
    expect(screen.queryByText(/action needed/)).toBeNull();
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ list: vi.fn(), connect: vi.fn(), check: vi.fn(), confirm: vi.fn(), settings: vi.fn() }));
vi.mock("../api/client", async (original) => ({ ...(await original<typeof import("../api/client")>()), systemApi: api }));

import { SoftwareFlow } from "./SoftwareFlow";

const sys = (system: string, label: string, extra: Record<string, unknown> = {}) =>
  ({ system, label, what: `${label} on your computer`, connected: false, connector_id: null, state: "waiting", detail: null, checking: false, paused: false, download_url: null, status: {}, last_pass: null, ...extra });
const NONE = [sys("smepro", "smePro"), sys("imara", "Imara"), sys("pharmacy", "Pharmacy ERP")];
const WAITING = [sys("smepro", "smePro", { connected: true, connector_id: "c1", state: "waiting", download_url: "https://example.com/RealityAgent.exe" }), ...NONE.slice(1)];
const DONE = [sys("smepro", "smePro", { connected: true, connector_id: "c1", state: "connected", last_pass: { rows: { sale: 1204 } } }), ...NONE.slice(1)];
const pick = async (...names: string[]) => {
  for (const n of names) await userEvent.click(await screen.findByRole("checkbox", { name: n }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
};
const renderFlow = (props = {}) => render(<MemoryRouter><SoftwareFlow tenantId="t1" onClose={vi.fn()} onChanged={vi.fn()} {...props} /></MemoryRouter>);

beforeEach(() => { vi.resetAllMocks(); api.connect.mockResolvedValue({ code: "ABCD-EFGH-1234", connector_ids: [] }); });

describe("Software flow", () => {
  it("starts by asking which software, in plain words, and explains the helper app once", async () => {
    api.list.mockResolvedValue(NONE);
    renderFlow();
    expect(await screen.findByText("Which software do you use?")).toBeInTheDocument();
    for (const name of ["smePro", "Imara", "Pharmacy ERP"]) expect(screen.getByRole("checkbox", { name })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    expect(screen.getByText(/It only/)).toHaveTextContent(/reads? your data|only reads/i);
    expect(screen.getByText(/never changes anything in your software/)).toBeInTheDocument();
  });

  it("gets one code and shows three steps with the code to copy", async () => {
    api.list.mockResolvedValueOnce(NONE).mockResolvedValue(WAITING);
    renderFlow();
    await pick("smePro");
    expect(api.connect).toHaveBeenCalledWith("t1", ["smepro"]);
    expect(await screen.findByLabelText("code")).toHaveTextContent("ABCD-EFGH-1234");
    expect(screen.getByRole("link", { name: /Download the helper app/ })).toHaveAttribute("href", "https://example.com/RealityAgent.exe");
    expect(screen.getByText("Waiting for the helper app")).toBeInTheDocument();
    expect(screen.getByText(/open the helper app and paste your code/)).toBeInTheDocument();
  });

  it("says plainly that there is nothing to download yet, instead of sending people away", async () => {
    api.list.mockResolvedValueOnce(NONE).mockResolvedValue([sys("smepro", "smePro", { connected: true, connector_id: "c1", state: "waiting" }), ...NONE.slice(1)]);
    renderFlow();
    await pick("smePro");
    expect(await screen.findByText(/isn't ready to download yet/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Download/ })).toBeNull();
    expect(screen.queryByText(/double-clicking/)).toBeNull();
  });

  it("on a phone, says the helper app belongs on the Windows computer and offers the link to send, not a download", async () => {
    const ua = vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36");
    try {
      api.list.mockResolvedValueOnce(NONE).mockResolvedValue(WAITING);
      renderFlow();
      await pick("smePro");
      expect(await screen.findByText(/runs on the Windows computer that has your software, not on a phone/)).toBeInTheDocument();
      expect(screen.getByLabelText("download link")).toHaveTextContent("https://example.com/RealityAgent.exe");
      expect(screen.queryByRole("link", { name: /Download/ })).toBeNull();
      expect(screen.queryByText(/double-clicking/)).toBeNull();
      expect(screen.getByLabelText("code")).toHaveTextContent("ABCD-EFGH-1234");          // the code is still there to copy
    } finally { ua.mockRestore(); }
  });

  it("celebrates when the helper app connects, and says what it found", async () => {
    api.list.mockResolvedValueOnce(NONE).mockResolvedValue(DONE);
    renderFlow();
    await pick("smePro");
    expect(await screen.findByText(/smePro is connected/)).toBeInTheDocument();
    expect(screen.getByText(/we found 1,204 sales\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See my overview" })).toHaveAttribute("href", "/");
  });

  it("does not offer a system that is already connected for a second code", async () => {
    api.list.mockResolvedValue(DONE);
    renderFlow();
    expect(await screen.findByRole("checkbox", { name: "smePro" })).toBeDisabled();
    expect(api.connect).not.toHaveBeenCalled();
  });

  it("connects several systems with one code and follows each one", async () => {
    const both = [sys("smepro", "smePro", { connected: true, connector_id: "c1", state: "connected", last_pass: { rows: { sale: 3 } } }),
      sys("imara", "Imara", { connected: true, connector_id: "c2", state: "not_found", detail: "No Imara data file found" }), NONE[2]];
    api.list.mockResolvedValueOnce(NONE).mockResolvedValue(both);
    renderFlow();
    await pick("smePro", "Imara");
    expect(api.connect).toHaveBeenCalledTimes(1);
    expect(api.connect).toHaveBeenCalledWith("t1", ["smepro", "imara"]);
    expect(await screen.findByLabelText("code")).toHaveTextContent("ABCD-EFGH-1234");
    expect(screen.getByText(/Imara: Couldn't find Imara on that computer/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Check Imara again/ })).toBeInTheDocument();
  });

  it("a new code for a connected system really gives the code, not the success screen", async () => {
    api.list.mockResolvedValue(DONE);
    renderFlow({ start: "smepro" });
    await userEvent.click(await screen.findByRole("button", { name: "Get a new code" }));
    expect(api.connect).toHaveBeenCalledWith("t1", ["smepro"]);
    expect(await screen.findByLabelText("code")).toHaveTextContent("ABCD-EFGH-1234");
  });

  it("explains a problem in words and offers the next step", async () => {
    const stuck = [sys("smepro", "smePro", { connected: true, connector_id: "c1", state: "not_found", detail: "No smePro data file found" }), ...NONE.slice(1)];
    api.list.mockResolvedValue(stuck);
    api.check.mockResolvedValue({});
    renderFlow({ start: "smepro" });
    expect(await screen.findByText(/Couldn't find smePro on that computer/)).toBeInTheDocument();
    expect(screen.getByText(/Details: No smePro data file found/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Check again/ }));
    await waitFor(() => expect(api.check).toHaveBeenCalledWith("t1", "c1"));
  });
});

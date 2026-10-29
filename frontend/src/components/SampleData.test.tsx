import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const demoApi = vi.hoisted(() => ({ list: vi.fn(), load: vi.fn(), remove: vi.fn() }));
vi.mock("../api/client", async (original) => ({ ...(await original<typeof import("../api/client")>()), demoApi }));

import { SampleData } from "./SampleData";

const SAMPLES = [
  { id: "duka", title: "Neighbourhood shop", blurb: "Three branches." },
  { id: "pharmacy", title: "Pharmacy", blurb: "Stock bill." },
];

function show(hasData: boolean) {
  const props = { tenantId: "t1", hasData, onChanged: vi.fn(), setNotice: vi.fn(), setError: vi.fn() };
  render(<SampleData {...props} />);
  return props;
}

beforeEach(() => { vi.resetAllMocks(); });

describe("SampleData", () => {
  it("shows the real way to connect first, keeps it mounted when the invitation arrives, and puts the invitation under it", async () => {
    let arrive: (v: unknown) => void = () => undefined;
    demoApi.list.mockReturnValue(new Promise((r) => { arrive = r; }));
    const props = { tenantId: "t1", hasData: false, onChanged: vi.fn(), setNotice: vi.fn(), setError: vi.fn() };
    const Main = () => { const [n, setN] = useState(0); return <button onClick={() => setN(n + 1)}>main {n}</button>; };
    render(<SampleData {...props}><Main /></SampleData>);
    await userEvent.click(screen.getByRole("button", { name: "main 0" }));            // the person is already using it
    arrive({ samples: SAMPLES, loaded: false });
    const offer = await screen.findByText("Want to see it working first?");
    const main = screen.getByRole("button", { name: "main 1" });                         // not remounted: its state survived
    expect(main.compareDocumentPosition(offer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // the offer comes after it
  });

  it("offers the samples to a business with no data and loads the one chosen", async () => {
    demoApi.list.mockResolvedValue({ samples: SAMPLES, loaded: false });
    demoApi.load.mockResolvedValue({ kind: "pharmacy", events: 4200, alerts: 3 });
    const props = show(false);
    await userEvent.click(await screen.findByRole("button", { name: /Pharmacy/ }));
    expect(demoApi.load).toHaveBeenCalledWith("t1", "pharmacy");
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
    expect(props.setNotice).toHaveBeenLastCalledWith(expect.stringContaining("4,200 records and 3 alerts"));
  });

  it("says plainly that sample data is showing, and removes it only when asked and confirmed", async () => {
    demoApi.list.mockResolvedValue({ samples: SAMPLES, loaded: true });
    demoApi.remove.mockResolvedValue({ removed: true });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    show(true);
    expect(await screen.findByText(/looking at sample data/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Remove sample data/ }));
    expect(demoApi.remove).not.toHaveBeenCalled();                       // declined
    await userEvent.click(screen.getByRole("button", { name: /Remove sample data/ }));
    await waitFor(() => expect(demoApi.remove).toHaveBeenCalledWith("t1"));
    confirm.mockRestore();
  });

  it("shows nothing once real data exists", async () => {
    demoApi.list.mockResolvedValue({ samples: SAMPLES, loaded: false });
    const { container } = render(<SampleData tenantId="t1" hasData onChanged={vi.fn()} setNotice={vi.fn()} setError={vi.fn()} />);
    await waitFor(() => expect(demoApi.list).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the server's reason when loading fails", async () => {
    const { ApiError } = await import("../api/client");
    demoApi.list.mockResolvedValue({ samples: SAMPLES, loaded: false });
    demoApi.load.mockRejectedValue(new ApiError(409, "This business already has data"));
    const props = show(false);
    await userEvent.click(await screen.findByRole("button", { name: /Neighbourhood shop/ }));
    await waitFor(() => expect(props.setError).toHaveBeenLastCalledWith("This business already has data"));
  });
});

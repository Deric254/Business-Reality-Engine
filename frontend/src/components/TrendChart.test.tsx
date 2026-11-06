import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
import { TrendChart } from "./TrendChart";

const days = (sales: number[]) => sales.map((s, i) => ({ date: `2026-10-${String(i + 1).padStart(2, "0")}`, sales: s, expenses: 0 }));
const dashed = (c: HTMLElement) => c.querySelectorAll('path[stroke-dasharray="3 3"]').length;

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
});

describe("TrendChart: what is unfinished is decided by the calendar, not by the numbers", () => {
  it("draws every finished day, including a real zero, and marks nothing as in progress", () => {
    const { container } = render(<TrendChart current={days([5, 7, 0])} previous={[]} unfinished={false} />);
    expect(dashed(container)).toBe(0);
    expect(screen.queryByText(/still in progress/i)).toBeNull();
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/3 days shown/);
  });

  it("dashes the last step and says so when the period ends today, even if today already has sales", () => {
    const { container } = render(<TrendChart current={days([5, 7, 2])} previous={[]} unfinished />);
    expect(dashed(container)).toBe(2);               // sales and spending tails
    expect(screen.getByText(/still in progress/i)).toBeInTheDocument();
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/3 days shown/);
  });
});

import { act, fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommandBar } from "@/components/shell/command-bar";

import { renderWithIntl } from "./render";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

beforeEach(() => push.mockReset());

describe("CommandBar", () => {
  it("opens with Ctrl+K and lists the FR-7 commands", async () => {
    renderWithIntl(<CommandBar role="owner" />);
    await act(async () => {
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    expect(await screen.findByText("/spend")).toBeInTheDocument();
    expect(screen.getByText("/touch")).toBeInTheDocument();
    expect(screen.getByText("/stripe connect")).toBeInTheDocument();
  });

  it("opens with / and filters by prefix", async () => {
    renderWithIntl(<CommandBar role="analyst" />);
    await act(async () => {
      fireEvent.keyDown(window, { key: "/" });
    });
    const input = await screen.findByRole("combobox");
    await act(async () => {
      fireEvent.change(input, { target: { value: "/sp" } });
    });
    expect(screen.getByText("/spend")).toBeInTheDocument();
    expect(screen.queryByText("/touch")).not.toBeInTheDocument();
  });

  it("runs a typed command by opening its form with the arguments", async () => {
    renderWithIntl(<CommandBar role="owner" />);
    await act(async () => {
      fireEvent.keyDown(window, { key: "/" });
    });
    const input = await screen.findByRole("combobox");
    await act(async () => {
      fireEvent.change(input, { target: { value: "/spend 24.99 GBP shipping vendor:Royal Mail" } });
    });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    expect(push).toHaveBeenCalledWith(
      `/app/ledger?tab=spends&new=1&args=${encodeURIComponent("24.99 GBP shipping vendor:Royal Mail")}`,
    );
  });

  it("does not run a command the role can't use", async () => {
    renderWithIntl(<CommandBar role="analyst" />);
    await act(async () => {
      fireEvent.keyDown(window, { key: "/" });
    });
    const input = await screen.findByRole("combobox");
    await act(async () => {
      fireEvent.change(input, { target: { value: "/spend" } });
    });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    expect(push).not.toHaveBeenCalled();
    expect(screen.getAllByText("Your role can't use this command.").length).toBeGreaterThan(0);
  });
});

import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CommandBar } from "@/components/shell/command-bar";

import { renderWithIntl } from "./render";

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
});

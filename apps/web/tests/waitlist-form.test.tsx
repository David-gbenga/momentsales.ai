import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WaitlistForm } from "@/components/waitlist-form";

import { renderWithIntl } from "./render";

function mockFetch() {
  return vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) => {
    if (url.endsWith("/waitlist/consent")) {
      return new Response(JSON.stringify({ version: "waitlist-v1", text: "Email me about the launch." }));
    }
    return new Response(JSON.stringify({ status: "check_your_email" }), { status: 202 });
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("WaitlistForm", () => {
  it("requires consent and an email, then confirms", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderWithIntl(
      <QueryClientProvider client={new QueryClient()}>
        <WaitlistForm />
      </QueryClientProvider>,
    );
    const consent = await screen.findByLabelText("Email me about the launch.");
    fireEvent.click(screen.getByRole("button", { name: "Join the waitlist" }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(screen.getByText("Tick the box to join the waitlist")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "a@example.com" } });
    fireEvent.click(consent);
    fireEvent.click(screen.getByRole("button", { name: "Join the waitlist" }));
    await waitFor(() => expect(screen.getByText(/Check your inbox/)).toBeInTheDocument());
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/v1/waitlist"));
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      email: "a@example.com",
      consent: true,
      consent_version: "waitlist-v1",
    });
  });
});

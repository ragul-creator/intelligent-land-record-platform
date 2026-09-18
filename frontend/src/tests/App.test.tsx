import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><App /></MemoryRouter></QueryClientProvider>);
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("App", () => {
  it("renders the H.2 sign-in landing page without a session", () => {
    renderApp();
    expect(screen.getByRole("heading", { name: /intelligent land record platform/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.getByText(/AI outputs are preliminary/i)).toBeInTheDocument();
  });

  it("signs in and presents the authorized Tamil Nadu demo project", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) return new Response(JSON.stringify({ access_token: "access", refresh_token: "refresh", token_type: "bearer", access_expires_in_seconds: 900, refresh_expires_in_seconds: 604800 }), { status: 200 });
      if (url.endsWith("/users/me")) return new Response(JSON.stringify({ id: "user-1", login_id: "OFF-TN-000001", email: "demo@example.invalid", full_name: "Demo Officer", roles: ["OFFICER"], permissions: ["project:read"], project_memberships: [{ project_id: "project-1", role: "OFFICER" }] }), { status: 200 });
      if (url.includes("/projects?")) return new Response(JSON.stringify({ items: [{ id: "project-1", name: "Tamil Nadu Integrated Land Records Demo", description: "Synthetic demo", state: "ACTIVE", owner_id: "user-1", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }], page: { limit: 100, offset: 0, total: 1 } }), { status: 200 });
      return new Response("{}", { status: 404 });
    }));

    renderApp();
    fireEvent.change(screen.getByLabelText(/login id or email/i), { target: { value: "OFF-TN-000001" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "demo-password" } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    expect(await screen.findByText("Tamil Nadu Integrated Land Records Demo")).toBeInTheDocument();
    expect(screen.getByText("OFF-TN-000001 · OFFICER")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open operational dashboard/i })).toHaveAttribute("href", "/projects/project-1");
    await waitFor(() => expect(sessionStorage.getItem("access_token")).toBe("access"));
  });
});

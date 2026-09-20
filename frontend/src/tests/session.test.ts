import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionFetch, storeSessionTokens } from "../api/session";

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("H.2B.4 rotating session client", () => {
  it("rotates a refresh token after a 401 and retries the API request once", async () => {
    storeSessionTokens({
      access_token: "old-access",
      refresh_token: "old-refresh",
      token_type: "bearer",
      access_expires_in_seconds: 900,
      refresh_expires_in_seconds: 3600,
    });

    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "AUTHENTICATION_FAILED" } }), { status: 401, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        access_token: "new-access",
        refresh_token: "new-refresh",
        token_type: "bearer",
        access_expires_in_seconds: 900,
        refresh_expires_in_seconds: 3600,
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await sessionFetch("/projects");
    expect(response.status).toBe(200);
    expect(sessionStorage.getItem("access_token")).toBe("new-access");
    expect(sessionStorage.getItem("refresh_token")).toBe("new-refresh");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[1][0])).toContain("/api/v1/auth/refresh");
    expect((fetchMock.mock.calls[2][1]?.headers as Record<string, string>).Authorization).toBe("Bearer new-access");
  });

  it("clears a rejected refresh session and emits the expiry event", async () => {
    storeSessionTokens({
      access_token: "old-access",
      refresh_token: "old-refresh",
      token_type: "bearer",
      access_expires_in_seconds: 900,
      refresh_expires_in_seconds: 3600,
    });

    const expired = vi.fn();
    window.addEventListener("session-expired", expired, { once: true });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response("{}", { status: 401 })));

    const response = await sessionFetch("/users/me");
    expect(response.status).toBe(401);
    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
    expect(expired).toHaveBeenCalledTimes(1);
  });
});

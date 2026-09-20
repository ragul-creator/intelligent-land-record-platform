import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminPage } from "../pages/AdminPage";
import { AuditPage } from "../pages/AuditPage";
import { JobsPage } from "../pages/JobsPage";
import { HomePage } from "../pages/HomePage";
import { ExportsPage } from "../pages/ExportsPage";
import { SearchPage } from "../pages/SearchPage";

function renderRoute(path: string, route: string, element: ReactNode) {
  sessionStorage.setItem("access_token", "access");
  sessionStorage.setItem("refresh_token", "refresh");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path={route} element={element} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("H.2B.4 platform workspaces", () => {
  it("creates a new isolated project from the signed-in landing page", async () => {
    let created = false;
    let createBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/users/me")) {
        return new Response(JSON.stringify({
          id: "officer",
          login_id: "OFF-TN-000001",
          email: "officer@example.invalid",
          full_name: "Officer",
          roles: ["OFFICER"],
          permissions: ["project:read", "project:create"],
          project_memberships: created ? [{ project_id: "project-new", role: "OFFICER" }] : [],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/projects?state=ACTIVE")) {
        return new Response(JSON.stringify({
          items: created ? [{
            id: "project-new",
            name: "New cadastral pilot",
            description: "H.2B.4 fixture",
            state: "ACTIVE",
            owner_id: "officer",
            created_at: "2026-09-20T00:00:00Z",
            updated_at: "2026-09-20T00:00:00Z",
          }] : [],
          page: { limit: 100, offset: 0, total: created ? 1 : 0 },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/projects") && init?.method === "POST") {
        createBody = JSON.parse(String(init.body));
        created = true;
        return new Response(JSON.stringify({
          id: "project-new",
          name: "New cadastral pilot",
          description: "H.2B.4 fixture",
          state: "ACTIVE",
          owner_id: "officer",
          created_at: "2026-09-20T00:00:00Z",
          updated_at: "2026-09-20T00:00:00Z",
        }), { status: 201, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));

    renderRoute("/", "/", <HomePage />);
    expect(await screen.findByRole("heading", { name: "Create a project" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "New cadastral pilot" } });
    fireEvent.change(screen.getByLabelText("Project description"), { target: { value: "H.2B.4 fixture" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({ name: "New cadastral pilot", description: "H.2B.4 fixture" });
    expect(await screen.findByText("New cadastral pilot")).toBeInTheDocument();
  });

  it("loads evidence-preserving export options", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/projects/project-1/exports")) {
        return new Response(JSON.stringify({
          project_id: "project-1",
          disclaimer: "Draft or unverified data remains explicitly labelled and is not statutory boundary certification.",
          items: [
            { code: "RECORDS_CSV", label: "Land-record evidence CSV", path: "/api/v1/projects/project-1/exports/records.csv", media_type: "text/csv", description: "Record evidence." },
            { code: "PARCELS_GEOJSON", label: "Parcel GeoJSON", path: "/api/v1/projects/project-1/exports/parcels.geojson", media_type: "application/geo+json", description: "Parcel evidence." },
          ],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));

    renderRoute("/projects/project-1/exports", "/projects/:projectId/exports", <ExportsPage />);
    expect(await screen.findByRole("heading", { name: "Land-record evidence CSV" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Parcel GeoJSON" })).toBeInTheDocument();
    expect(screen.getByText(/not statutory boundary certification/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(2);
  });

  it("searches evidence and preserves preliminary labels", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/projects/project-1/search?")) {
        return new Response(JSON.stringify({
          query: "123/4",
          total: 1,
          items: [{
            kind: "PARCEL",
            id: "parcel-1",
            evidence_id: null,
            title: "P-123/4",
            subtitle: "Parcel identifier",
            status: "DRAFT",
            matched_value: "P-123/4",
            preliminary: true,
          }],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));

    renderRoute("/projects/project-1/search", "/projects/:projectId/search", <SearchPage />);
    fireEvent.change(screen.getByLabelText("Search project"), { target: { value: "123/4" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByRole("heading", { name: "P-123/4" })).toBeInTheDocument();
    expect(screen.getByText("PRELIMINARY / UNVERIFIED")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Inspect source evidence" })).toHaveAttribute("href", "/projects/project-1/gis");
  });

  it("shows failed job recovery and retries through the safe endpoint", async () => {
    let retried = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/users/me")) {
        return new Response(JSON.stringify({
          id: "officer",
          login_id: "OFF-TN-000001",
          email: "officer@example.invalid",
          full_name: "Officer",
          roles: ["OFFICER"],
          permissions: ["project:read", "document:reprocess"],
          project_memberships: [{ project_id: "project-1", role: "OFFICER" }],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/projects/project-1/jobs?")) {
        return new Response(JSON.stringify({
          items: [{
            id: "job-1",
            project_id: "project-1",
            job_type: "DOCUMENT_AI_PROCESS",
            status: retried ? "QUEUED" : "FAILED",
            progress: retried ? 0 : 25,
            retry_count: retried ? 3 : 2,
            has_error: !retried,
            retryable: !retried,
            recovery_hint: retried ? null : "Retry the document processing workflow from its immutable source.",
            created_at: "2026-09-20T00:00:00Z",
            updated_at: "2026-09-20T00:00:00Z",
          }],
          page: { limit: 100, offset: 0, total: 1 },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/processing-jobs/job-1/retry") && init?.method === "POST") {
        retried = true;
        return new Response(JSON.stringify({
          id: "job-1",
          project_id: "project-1",
          job_type: "DOCUMENT_AI_PROCESS",
          status: "QUEUED",
          progress: 0,
          retry_count: 3,
          has_error: false,
          retryable: false,
          recovery_hint: null,
          created_at: "2026-09-20T00:00:00Z",
          updated_at: "2026-09-20T00:00:00Z",
        }), { status: 202, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute("/projects/project-1/jobs", "/projects/:projectId/jobs", <JobsPage />);
    expect(await screen.findByText(/Retry the document processing workflow/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry failed job" }));

    await waitFor(() => expect(retried).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/processing-jobs/job-1/retry"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("loads filtered audit events for an authorized project member", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/users/me")) {
        return new Response(JSON.stringify({
          id: "officer",
          login_id: "OFF-TN-000001",
          email: "officer@example.invalid",
          full_name: "Officer",
          roles: ["OFFICER"],
          permissions: ["audit:read"],
          project_memberships: [{ project_id: "project-1", role: "OFFICER" }],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/projects/project-1/audit?")) {
        return new Response(JSON.stringify({
          items: [{
            id: "audit-1",
            actor_id: "officer",
            action: "document.validated",
            target_type: "document",
            target_id: "doc-1",
            metadata: { validation_result_id: "validation-1" },
            created_at: "2026-09-20T00:00:00Z",
          }],
          page: { limit: 100, offset: 0, total: 1 },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));

    renderRoute("/projects/project-1/audit", "/projects/:projectId/audit", <AuditPage />);
    expect(await screen.findByText("document.validated")).toBeInTheDocument();
    expect(screen.getByText(/validation_result_id/)).toBeInTheDocument();
  });

  it("loads archived project settings and can explicitly reactivate them", async () => {
    let patchBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/users/me")) {
        return new Response(JSON.stringify({
          id: "admin",
          login_id: "ADM-TN-000001",
          email: "admin@example.invalid",
          full_name: "Admin",
          roles: ["ADMIN"],
          permissions: ["project:update", "project:member_manage", "user:manage"],
          project_memberships: [{ project_id: "project-1", role: "ADMIN" }],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/projects/project-1") && init?.method === "PATCH") {
        patchBody = JSON.parse(String(init.body));
        return new Response(JSON.stringify({
          id: "project-1", name: "Archived project", description: "Fixture", state: "ACTIVE",
          owner_id: "admin", created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/projects/project-1")) {
        return new Response(JSON.stringify({
          id: "project-1", name: "Archived project", description: "Fixture", state: "ARCHIVED",
          owner_id: "admin", created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/projects/project-1/members?")) {
        return new Response(JSON.stringify({
          items: [{ user_id: "admin", login_id: "ADM-TN-000001", full_name: "Admin", is_active: true, role: "ADMIN", created_at: "2026-09-20T00:00:00Z" }],
          page: { limit: 100, offset: 0, total: 1 },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));

    renderRoute("/projects/project-1/admin", "/projects/:projectId/admin", <AdminPage />);
    const state = await screen.findByLabelText("State");
    expect(state).toHaveValue("ARCHIVED");
    fireEvent.change(state, { target: { value: "ACTIVE" } });
    fireEvent.click(screen.getByRole("button", { name: "Save project" }));

    await waitFor(() => expect(patchBody).not.toBeNull());
    expect(patchBody?.state).toBe("ACTIVE");
  });
});

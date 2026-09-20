import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "../pages/DashboardPage";

const dashboard = {
  project: { id: "project", name: "Tamil Nadu Demo", description: "Combined workflow", state: "ACTIVE", owner_id: "owner", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
  project_role: "REVIEWER",
  documents: { total: 4, by_status: [{ status: "VALIDATED", count: 2 }, { status: "REVIEW_REQUIRED", count: 1 }], validated_records: 2, unlinked_validated_records: 1 },
  reviews: { open: 4, document: 1, gis: 1, record_parcel_link: 1, high: 1, medium: 1, assigned_to_me: 2, validation_issues: 1 },
  geo: { imagery_assets: 1, geoai_jobs: 2, parcels: 5, buildings: 8, roads: 2, land_use_features: 3, parcel_statuses: [{ status: "DRAFT", count: 5 }] },
  record_parcel_links: { total: 2, by_status: [{ status: "CONFIRMED", count: 1 }, { status: "REVIEW_REQUIRED", count: 1 }], confirmed_records: 1 },
  jobs: { total: 6, by_status: [{ status: "COMPLETED", count: 5 }, { status: "FAILED", count: 1 }], active: 0, failed: 1, retryable_failed: 1 },
  attention: { failed_jobs: 1, review_required_documents: 1, high_open_reviews: 1, ambiguous_record_parcel_links: 1, parcels_needing_review: 0, open_validation_issues: 1 },
  visibility: { project_role: "REVIEWER", draft_data_visible: true, viewer_read_only: false, notice: "Draft and unverified evidence remains explicitly labelled." },
};

const reviewer = { id: "u", permissions: ["project:read", "dashboard:read", "document:read", "geo:read", "review:read", "export:read"], project_memberships: [{ project_id: "project", role: "REVIEWER" }] };

function renderPage(user = reviewer, dashboardBody: unknown = dashboard, dashboardStatus = 200) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/users/me")) return new Response(JSON.stringify(user), { headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/projects/project/dashboard")) return new Response(JSON.stringify(dashboardBody), { status: dashboardStatus, headers: { "Content-Type": "application/json" } });
    return new Response("{}", { status: 404 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/project"]}><Routes><Route path="/projects/:projectId" element={<DashboardPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("DashboardPage", () => {
  it("renders combined workflow metrics and attention conditions", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Tamil Nadu Demo" })).toBeInTheDocument();
    expect(screen.getByText("Ambiguous record ↔ parcel links")).toBeInTheDocument();
    expect(screen.getByText(/Reviewer focus:/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review workspace" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Jobs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Exports" })).toBeInTheDocument();
    expect(screen.getAllByText("Validation issues").length).toBeGreaterThan(0);
  });

  it("keeps viewer navigation read-only and hides review workspace", async () => {
    renderPage({ ...reviewer, permissions: ["project:read", "dashboard:read", "document:read", "geo:read", "export:read"], project_memberships: [{ project_id: "project", role: "VIEWER" }] }, { ...dashboard, project_role: "VIEWER", visibility: { project_role: "VIEWER", draft_data_visible: true, viewer_read_only: true, notice: "Viewer members may inspect draft and unverified evidence in read-only mode." } });
    expect(await screen.findByText(/Read-only project overview/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Review workspace" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Documents" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Web-GIS" })).toBeInTheDocument();
  });

  it("renders a stable empty-project state", async () => {
    renderPage(reviewer, {
      ...dashboard,
      documents: { total: 0, by_status: [], validated_records: 0, unlinked_validated_records: 0 },
      reviews: { open: 0, document: 0, gis: 0, record_parcel_link: 0, high: 0, medium: 0, assigned_to_me: 0, validation_issues: 0 },
      geo: { imagery_assets: 0, geoai_jobs: 0, parcels: 0, buildings: 0, roads: 0, land_use_features: 0, parcel_statuses: [] },
      record_parcel_links: { total: 0, by_status: [], confirmed_records: 0 },
      jobs: { total: 0, by_status: [], active: 0, failed: 0, retryable_failed: 0 },
      attention: { failed_jobs: 0, review_required_documents: 0, high_open_reviews: 0, ambiguous_record_parcel_links: 0, parcels_needing_review: 0, open_validation_issues: 0 },
    });
    expect(await screen.findByText(/No persisted workflow conditions currently require attention/i)).toBeInTheDocument();
    expect(screen.getAllByText(/No persisted statuses yet/i).length).toBeGreaterThan(0);
  });

  it("renders an API failure state", async () => {
    renderPage(reviewer, { error: { code: "PROJECT_NOT_FOUND", message: "missing" } }, 404);
    expect(await screen.findByRole("heading", { name: "Dashboard unavailable" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

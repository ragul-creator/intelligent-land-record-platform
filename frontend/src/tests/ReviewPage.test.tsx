import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewPage } from "../pages/ReviewPage";

const currentUser = {
  id: "11111111-1111-1111-1111-111111111111",
  login_id: "REV-TN-000001",
  email: "reviewer@example.invalid",
  full_name: "Reviewer",
  roles: ["REVIEWER"],
  permissions: ["project:read", "review:read", "review:act", "geo:read"],
  project_memberships: [{ project_id: "project-1", role: "REVIEWER" }],
};

const documentTask = {
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  project_id: "project-1",
  queue_type: "DOCUMENT",
  target_type: "LAND_RECORD",
  target_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  severity: "MEDIUM",
  status: "OPEN",
  summary: "Survey number requires verification",
  source_refs: ["file:legacy-register-12/page:3"],
  metadata: { field_name: "survey_number", confidence: 0.61 },
  blocking_issue_count: 0,
  assignee_user_id: null,
  created_by_user_id: null,
  escalated: false,
  resolution_action: null,
  resolved_at: null,
  resolved_by_user_id: null,
  created_at: "2026-09-18T01:00:00Z",
  updated_at: "2026-09-18T01:00:00Z",
};

const gisTask = {
  ...documentTask,
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  queue_type: "GIS",
  target_type: "PARCEL",
  target_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  severity: "HIGH",
  summary: "Parcel overlap requires GIS review",
  source_refs: ["parcel-version:2"],
  metadata: { issue_code: "NEIGHBOUR_OVERLAP" },
  blocking_issue_count: 1,
};

const detail = {
  ...documentTask,
  history: [
    {
      action: "review.task_created",
      actor_id: null,
      metadata: { severity: "MEDIUM" },
      created_at: "2026-09-18T01:00:00Z",
    },
  ],
};

const page = (items: unknown[]) => ({ items, page: { limit: 100, offset: 0, total: items.length } });

function installFetch(user = currentUser) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/users/me")) return new Response(JSON.stringify(user), { status: 200 });

    if (init?.method === "PATCH" && url.includes("/review/tasks/")) {
      const body = JSON.parse(String(init.body)) as { action?: string; reason?: string; assignee_user_id?: string };
      return new Response(JSON.stringify({
        ...detail,
        assignee_user_id: body.assignee_user_id ?? detail.assignee_user_id,
        history: [...detail.history, {
          action: "review.action_applied",
          actor_id: currentUser.id,
          metadata: body,
          created_at: "2026-09-18T02:00:00Z",
        }],
      }), { status: 200 });
    }

    if (url.includes("/review/tasks?")) {
      return new Response(JSON.stringify(page(url.includes("queue_type=GIS") ? [gisTask] : [documentTask])), { status: 200 });
    }

    if (url.includes(`/review/tasks/${gisTask.id}`)) {
      return new Response(JSON.stringify({ ...gisTask, history: detail.history }), { status: 200 });
    }

    if (url.includes(`/review/tasks/${documentTask.id}`)) {
      return new Response(JSON.stringify(detail), { status: 200 });
    }

    return new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "Not found" } }), { status: 404 });
  }));
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/projects/project-1/review"]}>
        <Routes><Route path="/projects/:projectId/review" element={<ReviewPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("ReviewPage", () => {
  it("loads the document queue with evidence and audit history", async () => {
    installFetch();
    renderPage();

    expect(await screen.findByRole("heading", { name: /review workspace/i })).toBeInTheDocument();
    expect(await screen.findByText("Survey number requires verification")).toBeInTheDocument();
    expect(await screen.findByText("file:legacy-register-12/page:3")).toBeInTheDocument();
    expect(screen.getByText("survey_number")).toBeInTheDocument();
    expect(screen.getByText("review.task_created")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /assign to me/i })).toBeEnabled();
  });

  it("switches between document and GIS queues and exposes GIS evidence navigation", async () => {
    installFetch();
    renderPage();
    await screen.findByText("Survey number requires verification");

    fireEvent.click(screen.getByRole("tab", { name: /gis review/i }));

    expect(await screen.findByText("Parcel overlap requires GIS review")).toBeInTheDocument();
    expect(await screen.findByText("parcel-version:2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /inspect project gis evidence/i })).toHaveAttribute("href", "/projects/project-1/gis");
    expect(screen.getByText(/approval is blocked/i)).toBeInTheDocument();
  });

  it("applies a reviewer comment and refreshes audit/status data", async () => {
    installFetch();
    renderPage();
    await screen.findByText("Survey number requires verification");

    fireEvent.change(screen.getByLabelText(/reason \/ comment/i), { target: { value: "Checked against the supplied scan." } });
    fireEvent.click(screen.getByRole("button", { name: "Apply COMMENT" }));

    expect(await screen.findByText(/COMMENT recorded successfully/i)).toBeInTheDocument();
    expect(await screen.findByText("review.action_applied")).toBeInTheDocument();

    const fetchMock = vi.mocked(fetch);
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(patch).toBeTruthy();
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      action: "COMMENT",
      reason: "Checked against the supplied scan.",
    });
  });

  it("keeps the workspace read-only without review:act", async () => {
    installFetch({ ...currentUser, permissions: ["project:read", "review:read"] });
    renderPage();

    expect(await screen.findByText("Survey number requires verification")).toBeInTheDocument();
    expect(screen.getByText(/read-only review access/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /apply comment/i })).not.toBeInTheDocument();
  });

  it("denies the workspace when review:read cannot be verified", async () => {
    installFetch({ ...currentUser, permissions: ["project:read"] });
    renderPage();

    expect(await screen.findByRole("heading", { name: /review workspace unavailable/i })).toBeInTheDocument();
    expect(screen.getByText(/review:read/i)).toBeInTheDocument();
  });
});

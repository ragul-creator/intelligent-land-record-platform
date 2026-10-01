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
  permissions: ["project:read", "review:read", "review:act", "validation:run", "geo:read"],
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
  metadata: {
    field_name: "survey_number",
    confidence: 0.61,
    document_id: "983ed020-ebab-452d-b262-ffc4c6efa875",
    issue_codes: ["LOW_CONFIDENCE", "LOW_CONFIDENCE", "LOW_CONFIDENCE"],
    blocking_issue_codes: [],
    validation_version: 2,
    validation_result_id: "6a1db91f-f9f2-48d9-a74a-d786b3367cbb",
    confidence_summary: {
      band: "LOW",
      value: 0.7066666666666667,
      fields: [
        { band: "MEDIUM", field_name: "survey_number", candidate_count: 1, unknown_confidence_count: 0, representative_confidence: 0.83 },
        { band: "LOW", field_name: "seller", candidate_count: 1, unknown_confidence_count: 0, representative_confidence: 0.706857142857143 },
        { band: "LOW", field_name: "buyer", candidate_count: 1, unknown_confidence_count: 0, representative_confidence: 0.7440000000000001 },
        { band: "LOW", field_name: "unique_document_reference", candidate_count: 1, unknown_confidence_count: 0, representative_confidence: 0.7066666666666667 },
        { band: "UNKNOWN", field_name: "khasra_number", candidate_count: 0, unknown_confidence_count: 0, representative_confidence: null },
      ],
      high_threshold: 0.9,
      medium_threshold: 0.75,
      missing_field_count: 1,
      conflict_field_count: 0,
      contributing_field_count: 4,
      unknown_confidence_field_count: 0,
      policy_version: "document-validation-mvp-v1",
    },
  },
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

const validationTask = {
  ...documentTask,
  id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
  target_type: "VALIDATION_ISSUE",
  target_id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
  severity: "HIGH",
  summary: "Potential duplicate record: 2 validated documents share identifier 123/4.",
  source_refs: [
    "document:doc-1:field:field-1:page:1",
    "document:doc-2:field:field-2:page:1",
  ],
  metadata: {
    validation_issue_type: "DUPLICATE_RECORD",
    normalized_identifier: "123/4",
    document_count: 2,
    interpretation: "Exact identifier reuse is a review flag only.",
  },
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

const savedReviewTask = {
  ...documentTask,
  id: "saved-review-task",
  project_id: "saved-project",
  target_id: "saved-demo-document",
  severity: "INFO",
  summary: "Document verification review: validation passed",
  source_refs: [],
  metadata: {
    document_id: "saved-demo-document",
    validation_result_id: "saved-demo-validation",
    validation_version: 1,
    issue_codes: [],
    blocking_issue_codes: [],
    verification_only: true,
    confidence_summary: {
      band: "MEDIUM",
      value: 0.8425,
      fields: [],
      high_threshold: 0.9,
      medium_threshold: 0.75,
      missing_field_count: 0,
      conflict_field_count: 0,
      contributing_field_count: 19,
      unknown_confidence_field_count: 0,
      policy_version: "document-validation-mvp-v1",
    },
  },
  created_at: "2026-10-01T09:42:36Z",
  updated_at: "2026-10-01T09:42:36Z",
  history: [{
    action: "review.task_created",
    actor_id: null,
    metadata: { severity: "INFO", target_type: "LAND_RECORD" },
    created_at: "2026-10-01T09:42:36Z",
  }],
};

const page = (items: unknown[]) => ({ items, page: { limit: 100, offset: 0, total: items.length } });

function installFetch(user = currentUser) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/demo/ocr-land-record-review.json")) return new Response(JSON.stringify(savedReviewTask), { status: 200 });
    if (url.includes("/users/me")) return new Response(JSON.stringify(user), { status: 200 });


    if (init?.method === "POST" && url.includes("/validation/run")) {
      return new Response(JSON.stringify({
        created_count: 1,
        refreshed_count: 0,
        open_issue_count: 1,
        duplicate_record_issue_count: 1,
        area_mismatch_issue_count: 0,
        items: [validationTask],
      }), { status: 201 });
    }

    if (url.includes("/validation/issues?")) {
      return new Response(JSON.stringify(page([validationTask])), { status: 200 });
    }

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


    if (url.includes(`/review/tasks/${validationTask.id}`)) {
      return new Response(JSON.stringify({ ...validationTask, history: detail.history }), { status: 200 });
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
    expect(screen.getByText("Overall confidence")).toBeInTheDocument();
    expect(screen.getAllByText("70.7%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Low Confidence × 3")).toBeInTheDocument();
    expect(screen.getByText("No blocking issues")).toBeInTheDocument();
    expect(screen.getByText("Seller")).toBeInTheDocument();
    expect(screen.getByText("Buyer")).toBeInTheDocument();
    expect(screen.getByText("Unique Document Reference")).toBeInTheDocument();
    expect(screen.getByText("review.task_created")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /assign to me/i })).toBeEnabled();
  });

  it("shows the preserved OCR demo review and links back to its exact saved document", async () => {
    installFetch();
    renderPage();
    await screen.findByText("Survey number requires verification");

    const savedDemoLabel = await screen.findByText("Saved OCR demo · Document verification");
    fireEvent.click(savedDemoLabel.closest("button")!);

    expect(await screen.findByText("Preserved OCR demo review")).toBeInTheDocument();
    expect(screen.getAllByText("84.3%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Verification Only")).toBeInTheDocument();
    expect(screen.getByText("true")).toBeInTheDocument();
    expect(screen.getByText("review.task_created")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /inspect document evidence/i })).toHaveAttribute(
      "href",
      "/projects/project-1/documents?documentId=saved-ocr-land-record-demo",
    );
    expect(screen.queryByRole("button", { name: /assign to me/i })).not.toBeInTheDocument();
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

  it("shows validation issues and can run the H.2B.3 checks", async () => {
    installFetch();
    renderPage();
    await screen.findByText("Survey number requires verification");

    fireEvent.click(screen.getByRole("tab", { name: /validation issues/i }));

    expect(await screen.findByText(/Potential duplicate record/i)).toBeInTheDocument();
    expect((await screen.findAllByText("Duplicate record")).length).toBeGreaterThan(0);
    expect(await screen.findByText("123/4")).toBeInTheDocument();
    expect((await screen.findAllByText(/Exact identifier reuse is a review flag only/i)).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /run validation checks/i }));

    expect(await screen.findByText(/Validation completed: 1 new, 0 refreshed, 1 open/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(vi.mocked(fetch).mock.calls.some(([input, init]) =>
        String(input).includes("/validation/run") && init?.method === "POST",
      )).toBe(true);
    });
  });

  it("applies a reviewer comment and refreshes audit/status data", async () => {
    installFetch();
    renderPage();
    await screen.findByText("Survey number requires verification");

    fireEvent.change(screen.getByLabelText(/reason \/ comment/i), { target: { value: "Checked against the supplied scan." } });
    fireEvent.click(screen.getByRole("button", { name: "Apply comment" }));

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

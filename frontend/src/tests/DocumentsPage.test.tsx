import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentsPage } from "../pages/DocumentsPage";

const user = { id: "user", permissions: ["document:read", "document:upload", "document:process", "field:read", "field:correct", "record:read", "validation:run", "validation:resolve"], project_memberships: [{ project_id: "project", role: "OFFICER" }] };
const link = { id: "link-1", project_id: "project", document_id: "doc", document_validation_result_id: "validation-1", parcel_id: "parcel-1", parcel_display_identifier: "123/4", link_status: "REVIEW_REQUIRED", link_method: "EXACT_SURVEY_IDENTIFIER", confidence: 0.88, rationale: {}, provenance: {}, review_required: true, review_task_id: "review-1", review_reason: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };

function installFetch(readOnly = false, withEvidence = false) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/users/me")) return new Response(JSON.stringify(readOnly ? { ...user, permissions: ["document:read", "field:read", "record:read"] } : user));
    if (url.endsWith("/documents")) return new Response(JSON.stringify({ items: [{ id: "doc", project_id: "project", filename: "record.pdf", content_type: "application/pdf", size_bytes: 1, status: "REVIEW_REQUIRED", latest_processing_job_id: null, uploaded_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }] }));
    if (url.endsWith("/documents/doc")) return new Response(JSON.stringify({
      id: "doc",
      project_id: "project",
      filename: "record.pdf",
      content_type: "application/pdf",
      size_bytes: 1,
      status: "REVIEW_REQUIRED",
      latest_processing_job_id: null,
      uploaded_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      latest_ocr: withEvidence ? {
        id: "ocr-1",
        version: 2,
        confidence: 0.72,
        requested_languages: ["tam", "eng"],
        project_tested_languages: ["tam", "eng"],
        engine: "tesseract",
        engine_version: "5.4",
        model_version: null,
        page_count: 1,
        processed_at: "2026-01-01T00:00:00Z",
        payload: {
          pages: [{
            page_number: 1,
            text: "Survey No. 123/4",
            confidence: 0.72,
            width: 1200,
            height: 1600,
            requested_languages: ["tam", "eng"],
            project_tested_languages: ["tam", "eng"],
            preprocessing: { operations: ["grayscale", "contrast"], deskew_angle_degrees: 0 },
            regions: [
              { text: "Survey", confidence: 0.96, bounding_box: { left: 20, top: 30, width: 80, height: 20 }, kind: "word" },
              { text: "123/4", confidence: 0.51, bounding_box: { left: 120, top: 30, width: 60, height: 20 }, kind: "word" },
            ],
            engine: "tesseract",
            engine_version: "5.4",
            model_version: null,
            processed_at: "2026-01-01T00:00:00Z",
          }],
        },
      } : null,
      latest_validation: { id: "validation-1", version: 1, status: "REVIEW_REQUIRED", report: { issues: [] }, confidence_summary: {}, review_task_id: "review-1" },
    }));
    if (url.endsWith("/documents/doc/source-url")) return new Response(JSON.stringify({ document_id: "doc", source_url: "http://minio.test/source.pdf?sig=demo", expires_in_seconds: 900 }));
    if (url.endsWith("/documents/doc/fields")) return new Response(JSON.stringify({ fields: withEvidence ? [{
      id: "field-1",
      field_name: "survey_number",
      original_value: "123/4",
      normalized_value: "123/4",
      confidence: 0.51,
      page_number: 1,
      bounding_box: { left: 120, top: 30, width: 60, height: 20 },
      source_id: "doc",
      model_version: null,
      extractor_version: "rules-v1",
      processed_at: "2026-01-01T00:00:00Z",
      corrections: [],
    }] : [] }));
    if (url.includes("/documents/doc/record-parcel-links/suggestions")) return new Response(JSON.stringify({ items: [link] }), { status: 201 });
    if (url.includes("/record-parcel-links/link-1/confirm")) return new Response(JSON.stringify({ ...link, link_status: "CONFIRMED", review_required: false }));
    if (url.includes("/documents/doc/record-parcel-links")) return new Response(JSON.stringify({ items: [link], page: { limit: 100, offset: 0, total: 1 } }));
    return new Response("{}", { status: init?.method === "POST" ? 200 : 404 });
  }));
}

function renderPage(initial = "/projects/project/documents") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initial]}><Routes><Route path="/projects/:projectId/documents" element={<DocumentsPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("DocumentsPage", () => {
  it("renders a document and its preliminary review state", async () => {
    installFetch(); renderPage();
    expect(await screen.findByText("record.pdf")).toBeInTheDocument();
    expect(screen.getByText("REVIEW_REQUIRED")).toBeInTheDocument();
    expect(screen.getByText(/preliminary evidence/i)).toBeInTheDocument();
  });

  it("hides upload controls for read-only users", async () => {
    installFetch(true); renderPage();
    await screen.findByText("record.pdf");
    expect(screen.queryByRole("button", { name: /upload document/i })).not.toBeInTheDocument();
  });

  it("renders immutable source, OCR token evidence, and field provenance together", async () => {
    installFetch(false, true); renderPage("/projects/project/documents?documentId=doc");
    expect(await screen.findByRole("region", { name: /document evidence viewer/i })).toBeInTheDocument();
    expect(screen.getByTitle("Source document preview")).toHaveAttribute("src", expect.stringContaining("source.pdf"));
    expect(screen.getByText("Survey No. 123/4")).toBeInTheDocument();
    expect(screen.getByText("LOW CONFIDENCE")).toBeInTheDocument();
    expect(screen.getByText("rules-v1")).toBeInTheDocument();
    expect(screen.getByText("Low-confidence tokens")).toBeInTheDocument();
  });

  it("integrates validated record evidence with parcel candidates and GIS navigation", async () => {
    installFetch(); renderPage("/projects/project/documents?documentId=doc");
    expect(await screen.findByRole("heading", { name: "record.pdf" })).toBeInTheDocument();
    expect(await screen.findByText("123/4")).toBeInTheDocument();
    expect(screen.getByText("REVIEW REQUIRED")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open parcel in web-gis/i })).toHaveAttribute("href", "/projects/project/gis?parcelId=parcel-1");
    fireEvent.click(screen.getByRole("button", { name: /confirm association/i }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([request]) => String(request).includes("/record-parcel-links/link-1/confirm"))).toBe(true));
  });
});

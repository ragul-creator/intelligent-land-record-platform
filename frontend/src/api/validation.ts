import { sessionFetch } from "./session";
import {
  ReviewApiError,
  type ReviewSeverity,
  type ReviewTask,
  type ReviewTaskStatus,
} from "./review";

export type ValidationIssueType = "DUPLICATE_RECORD" | "AREA_MISMATCH";

export interface ValidationIssueFilters {
  status: ReviewTaskStatus | "ALL";
  severity: ReviewSeverity | "ALL";
  issueType: ValidationIssueType | "ALL";
  assigneeUserId?: string;
}

export interface ValidationRunResult {
  created_count: number;
  refreshed_count: number;
  open_issue_count: number;
  duplicate_record_issue_count: number;
  area_mismatch_issue_count: number;
  items: ReviewTask[];
}

interface Page<T> {
  items: T[];
  page: { limit: number; offset: number; total: number };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await sessionFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ReviewApiError(
      response.status,
      body?.error?.code ?? "REQUEST_FAILED",
      body?.error?.message ?? "Unable to load validation issues.",
    );
  }
  return response.json() as Promise<T>;
}

export async function loadValidationIssues(
  projectId: string,
  filters: ValidationIssueFilters,
): Promise<Page<ReviewTask>> {
  const params = new URLSearchParams({ limit: "100", offset: "0" });
  if (filters.status !== "ALL") params.set("status", filters.status);
  if (filters.severity !== "ALL") params.set("severity", filters.severity);
  if (filters.issueType !== "ALL") params.set("issue_type", filters.issueType);
  if (filters.assigneeUserId) params.set("assignee_user_id", filters.assigneeUserId);
  return request<Page<ReviewTask>>(
    `/projects/${projectId}/validation/issues?${params.toString()}`,
  );
}

export function runValidationChecks(projectId: string): Promise<ValidationRunResult> {
  return request<ValidationRunResult>(`/projects/${projectId}/validation/run`, {
    method: "POST",
  });
}

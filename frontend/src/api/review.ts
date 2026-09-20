import { sessionFetch } from "./session";
export type ReviewQueueType = "DOCUMENT" | "GIS";
export type ReviewSeverity = "INFO" | "LOW" | "MEDIUM" | "HIGH";
export type ReviewTaskStatus = "OPEN" | "RESOLVED";
export type ReviewAction = "APPROVE" | "CORRECT" | "REJECT" | "REPROCESS" | "COMMENT" | "ESCALATE";

export interface ReviewHistoryEvent {
  action: string;
  actor_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ReviewTask {
  id: string;
  project_id: string;
  queue_type: ReviewQueueType;
  target_type: string;
  target_id: string;
  severity: ReviewSeverity;
  status: ReviewTaskStatus;
  summary: string;
  source_refs: string[];
  metadata: Record<string, unknown>;
  blocking_issue_count: number;
  assignee_user_id: string | null;
  created_by_user_id: string | null;
  escalated: boolean;
  resolution_action: string | null;
  resolved_at: string | null;
  resolved_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewTaskDetail extends ReviewTask {
  history: ReviewHistoryEvent[];
}

export interface ReviewTaskFilters {
  queueType: ReviewQueueType;
  status: ReviewTaskStatus | "ALL";
  severity: ReviewSeverity | "ALL";
  assigneeUserId?: string;
}

export interface ReviewTaskUpdate {
  action?: ReviewAction;
  assignee_user_id?: string;
  reason?: string;
  correction_reference?: string;
  reprocess_job_id?: string;
}

interface Page<T> {
  items: T[];
  page: { limit: number; offset: number; total: number };
}

export class ReviewApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem("access_token");
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ReviewApiError(
      response.status,
      body?.error?.code ?? "REQUEST_FAILED",
      body?.error?.message ?? "Unable to load review data.",
    );
  }
  return response.json() as Promise<T>;
}

export async function loadReviewTasks(projectId: string, filters: ReviewTaskFilters): Promise<Page<ReviewTask>> {
  const params = new URLSearchParams({
    project_id: projectId,
    queue_type: filters.queueType,
    limit: "100",
    offset: "0",
  });
  if (filters.status !== "ALL") params.set("status", filters.status);
  if (filters.severity !== "ALL") params.set("severity", filters.severity);
  if (filters.assigneeUserId) params.set("assignee_user_id", filters.assigneeUserId);
  return request<Page<ReviewTask>>(`/review/tasks?${params.toString()}`);
}

export function loadReviewTask(taskId: string): Promise<ReviewTaskDetail> {
  return request<ReviewTaskDetail>(`/review/tasks/${taskId}`);
}

export function updateReviewTask(taskId: string, payload: ReviewTaskUpdate): Promise<ReviewTaskDetail> {
  return request<ReviewTaskDetail>(`/review/tasks/${taskId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

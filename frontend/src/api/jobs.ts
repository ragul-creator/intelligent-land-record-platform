import { ApiError } from "./gis";
import { sessionFetch } from "./session";

export type ProcessingJobStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface ProcessingJob {
  id: string;
  project_id: string;
  job_type: string;
  status: ProcessingJobStatus;
  progress: number;
  retry_count: number;
  has_error: boolean;
  retryable: boolean;
  recovery_hint: string | null;
  created_at: string;
  updated_at: string;
}

interface Page<T> { items: T[]; page: { limit: number; offset: number; total: number }; }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await sessionFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "JOB_REQUEST_FAILED", body?.error?.message ?? "Unable to load processing jobs.");
  }
  return response.json() as Promise<T>;
}

export function loadJobs(projectId: string, status?: ProcessingJobStatus): Promise<Page<ProcessingJob>> {
  const params = new URLSearchParams({ limit: "100", offset: "0" });
  if (status) params.set("status", status);
  return request<Page<ProcessingJob>>(`/projects/${projectId}/jobs?${params.toString()}`);
}

export function retryJob(jobId: string): Promise<ProcessingJob> {
  return request<ProcessingJob>(`/processing-jobs/${jobId}/retry`, { method: "POST" });
}

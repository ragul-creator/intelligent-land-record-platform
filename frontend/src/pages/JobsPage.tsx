import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { loadCurrentUser } from "../api/gis";
import { loadJobs, retryJob, type ProcessingJob, type ProcessingJobStatus } from "../api/jobs";

const statuses: Array<ProcessingJobStatus | "ALL"> = ["ALL", "QUEUED", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"];

const jobTypeLabel = (value: string) => value
  .toLowerCase()
  .split("_")
  .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
  .join(" ");

function canRetryJob(job: ProcessingJob, permissions: string[]): boolean {
  if (!job.retryable) return false;
  if (job.job_type === "DOCUMENT_REVALIDATE") return permissions.includes("field:correct");
  if (job.job_type === "DOCUMENT_AI_PROCESS") return permissions.includes("document:reprocess");
  if (job.job_type === "IMAGERY_REGISTER") return permissions.includes("imagery:upload");
  return permissions.includes("geoai:process");
}

export function JobsPage() {
  const { projectId } = useParams();
  const client = useQueryClient();
  const [status, setStatus] = useState<ProcessingJobStatus | "ALL">("ALL");
  const currentUser = useQuery({ queryKey: ["current-user"], queryFn: loadCurrentUser, retry: false });
  const jobs = useQuery({
    queryKey: ["project-jobs", projectId, status],
    queryFn: () => loadJobs(projectId!, status === "ALL" ? undefined : status),
    enabled: Boolean(projectId && currentUser.data),
    retry: false,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((item) => item.status === "QUEUED" || item.status === "PROCESSING") ? 2000 : false;
    },
  });
  const retry = useMutation({
    mutationFn: (jobId: string) => retryJob(jobId),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["project-jobs", projectId] });
      await client.invalidateQueries({ queryKey: ["project-dashboard", projectId] });
    },
  });
  const permissions = currentUser.data?.permissions ?? [];

  if (!projectId) return <main className="tool-state"><h1>Jobs unavailable</h1></main>;
  return <main className="tool-shell">
    <header className="tool-header"><div><p className="eyebrow">H.2B.4 asynchronous work</p><h1>Processing jobs</h1><p>Live progress, failure signals, retry counts, and safe recovery actions.</p></div><nav><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to="/">Platform home</Link></nav></header>
    <section className="tool-toolbar"><label>Status<select value={status} onChange={(event) => setStatus(event.target.value as ProcessingJobStatus | "ALL")}>{statuses.map((item) => <option key={item}>{item}</option>)}</select></label><span className="toolbar-meta">{jobs.data?.items.length ?? 0} job{(jobs.data?.items.length ?? 0) === 1 ? "" : "s"}</span><button type="button" onClick={() => jobs.refetch()}>Refresh</button></section>
    {jobs.isError && <p className="tool-error">Processing jobs could not be loaded.</p>}
    <section className="job-list" aria-label="Processing jobs">{jobs.data?.items.map((job) => {
      const active = job.status === "QUEUED" || job.status === "PROCESSING";
      return <article key={job.id} className={`job-row job-row-${job.status.toLowerCase()}`}>
        <div className="job-heading">
          <div className="job-title-line">
            <strong>{jobTypeLabel(job.job_type)}</strong>
            <span className={`job-status job-status-${job.status.toLowerCase()}`}>{job.status.replaceAll("_", " ")}</span>
            {job.retry_count > 0 && <span className="job-retry">Retry {job.retry_count}</span>}
          </div>
          <time dateTime={job.updated_at}>{new Date(job.updated_at).toLocaleString()}</time>
        </div>
        {active && <div className="job-progress-row"><progress max={100} value={job.progress} aria-label={`Progress for ${job.job_type}`} /><span>{job.progress}%</span></div>}
        {job.recovery_hint && <p className="job-recovery">{job.recovery_hint}</p>}
        {canRetryJob(job, permissions) && <button type="button" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>Retry failed job</button>}
      </article>;
    })}</section>
  </main>;
}

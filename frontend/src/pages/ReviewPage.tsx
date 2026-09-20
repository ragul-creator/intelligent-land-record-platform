import { Fragment, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { loadCurrentUser } from "../api/gis";
import {
  ReviewApiError,
  loadReviewTask,
  loadReviewTasks,
  updateReviewTask,
  type ReviewAction,
  type ReviewQueueType,
  type ReviewSeverity,
  type ReviewTask,
  type ReviewTaskStatus,
} from "../api/review";
import {
  loadValidationIssues,
  runValidationChecks,
  type ValidationIssueType,
} from "../api/validation";

const severityOrder: ReviewSeverity[] = ["INFO", "LOW", "MEDIUM", "HIGH"];
const actionOptions: ReviewAction[] = ["APPROVE", "CORRECT", "REJECT", "REPROCESS", "COMMENT", "ESCALATE"];
type WorkspaceMode = ReviewQueueType | "VALIDATION";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "Not available";
}

function metadataEntries(metadata: Record<string, unknown>) {
  return Object.entries(metadata).filter(([, value]) => value !== null && value !== undefined);
}

function validationIssueLabel(metadata: Record<string, unknown>) {
  if (metadata.validation_issue_type === "DUPLICATE_RECORD") return "Duplicate record";
  if (metadata.validation_issue_type === "AREA_MISMATCH") return "Area mismatch";
  return "Validation issue";
}

function TaskListItem({ task, selected, onSelect }: { task: ReviewTask; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={selected ? "review-task-card selected" : "review-task-card"} onClick={onSelect} aria-pressed={selected}>
      <span className="review-task-card-top">
        <strong>{task.severity}</strong>
        <span>{task.status}</span>
      </span>
      <span className="review-task-summary">{task.summary}</span>
      <span className="review-task-meta">
        {task.target_type === "VALIDATION_ISSUE" ? validationIssueLabel(task.metadata) : task.target_type} · {task.target_id}
      </span>
      <span className="review-task-meta">{task.assignee_user_id ? "Assigned" : "Unassigned"}{task.escalated ? " · Escalated" : ""}</span>
    </button>
  );
}

function History({ history }: { history: Array<{ action: string; actor_id: string | null; metadata: Record<string, unknown>; created_at: string }> }) {
  if (!history.length) return <p className="review-muted">No review history has been recorded yet.</p>;
  return (
    <ol className="review-history">
      {history.map((event, index) => (
        <li key={`${event.created_at}-${index}`}>
          <strong>{event.action}</strong>
          <span>{formatDate(event.created_at)} · {event.actor_id ?? "system"}</span>
          {Object.keys(event.metadata).length > 0 && <code>{JSON.stringify(event.metadata)}</code>}
        </li>
      ))}
    </ol>
  );
}

export function ReviewPage() {
  const { projectId } = useParams();
  const queryClient = useQueryClient();
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("DOCUMENT");
  const [taskStatus, setTaskStatus] = useState<ReviewTaskStatus | "ALL">("OPEN");
  const [severity, setSeverity] = useState<ReviewSeverity | "ALL">("ALL");
  const [issueType, setIssueType] = useState<ValidationIssueType | "ALL">("ALL");
  const [assignedToMe, setAssignedToMe] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [action, setAction] = useState<ReviewAction>("COMMENT");
  const [reason, setReason] = useState("");
  const [correctionReference, setCorrectionReference] = useState("");
  const [reprocessJobId, setReprocessJobId] = useState("");
  const [assigneeUserId, setAssigneeUserId] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [validationMessage, setValidationMessage] = useState("");

  const currentUser = useQuery({
    queryKey: ["current-user"],
    queryFn: loadCurrentUser,
    retry: false,
    enabled: Boolean(projectId),
  });

  const membership = Boolean(
    currentUser.data?.project_memberships?.some((item) => item.project_id === projectId),
  );
  const canRead = Boolean(currentUser.data?.permissions?.includes("review:read") && membership);
  const canAct = Boolean(currentUser.data?.permissions?.includes("review:act") && membership);
  const canRunValidation = Boolean(currentUser.data?.permissions?.includes("validation:run") && membership);

  const assigneeUserIdFilter = assignedToMe ? currentUser.data?.id : undefined;
  const reviewFilters = useMemo(() => ({
    queueType: (workspaceMode === "GIS" ? "GIS" : "DOCUMENT") as ReviewQueueType,
    status: taskStatus,
    severity,
    assigneeUserId: assigneeUserIdFilter,
  }), [workspaceMode, taskStatus, severity, assigneeUserIdFilter]);

  const validationFilters = useMemo(() => ({
    status: taskStatus,
    severity,
    issueType,
    assigneeUserId: assigneeUserIdFilter,
  }), [taskStatus, severity, issueType, assigneeUserIdFilter]);

  const reviewTasks = useQuery({
    queryKey: ["review-tasks", projectId, reviewFilters],
    queryFn: () => loadReviewTasks(projectId!, reviewFilters),
    enabled: Boolean(projectId && currentUser.data && canRead && workspaceMode !== "VALIDATION"),
    retry: false,
  });

  const validationIssues = useQuery({
    queryKey: ["validation-issues", projectId, validationFilters],
    queryFn: () => loadValidationIssues(projectId!, validationFilters),
    enabled: Boolean(projectId && currentUser.data && canRead && workspaceMode === "VALIDATION"),
    retry: false,
  });

  const activeData = workspaceMode === "VALIDATION" ? validationIssues.data : reviewTasks.data;
  const activeLoading = workspaceMode === "VALIDATION" ? validationIssues.isLoading : reviewTasks.isLoading;
  const activeFetching = workspaceMode === "VALIDATION" ? validationIssues.isFetching : reviewTasks.isFetching;
  const activeError = (workspaceMode === "VALIDATION" ? validationIssues.error : reviewTasks.error) as ReviewApiError | null;

  useEffect(() => {
    const items = activeData?.items ?? [];
    if (!items.length) {
      setSelectedTaskId(null);
      return;
    }
    if (!selectedTaskId || !items.some((item) => item.id === selectedTaskId)) {
      setSelectedTaskId(items[0].id);
    }
  }, [activeData, selectedTaskId]);

  const detail = useQuery({
    queryKey: ["review-task", selectedTaskId],
    queryFn: () => loadReviewTask(selectedTaskId!),
    enabled: Boolean(selectedTaskId && canRead),
    retry: false,
  });

  useEffect(() => {
    setReason("");
    setCorrectionReference("");
    setReprocessJobId("");
    setAssigneeUserId("");
    setSuccessMessage("");
  }, [selectedTaskId, action]);

  useEffect(() => {
    setValidationMessage("");
  }, [workspaceMode]);

  const mutation = useMutation({
    mutationFn: (payload: Parameters<typeof updateReviewTask>[1]) => updateReviewTask(selectedTaskId!, payload),
    onSuccess: async (updated, payload) => {
      queryClient.setQueryData(["review-task", updated.id], updated);
      setSuccessMessage(payload.action ? `${payload.action} recorded successfully.` : "Assignment updated successfully.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["review-tasks", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["validation-issues", projectId] }),
      ]);
    },
  });

  const validationRun = useMutation({
    mutationFn: () => runValidationChecks(projectId!),
    onSuccess: async (result) => {
      setValidationMessage(
        `Validation completed: ${result.created_count} new, ${result.refreshed_count} refreshed, ${result.open_issue_count} open.`,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["validation-issues", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["review-tasks", projectId] }),
      ]);
    },
  });

  if (!projectId) return <main className="review-state"><h1>Review workspace unavailable</h1></main>;

  if (currentUser.isLoading) {
    return <main className="review-state"><p className="eyebrow">Human verification · H.2B.3</p><h1>Loading reviewer access…</h1></main>;
  }

  if (currentUser.isError || !canRead) {
    return (
      <main className="review-state">
        <p className="eyebrow">Human verification · H.2B.3</p>
        <h1>Review workspace unavailable</h1>
        <p>You need project membership and the <code>review:read</code> permission to open this workspace.</p>
        <Link to="/">Return to platform</Link>
      </main>
    );
  }

  const selected = detail.data;
  const mutationError = mutation.error as ReviewApiError | null;
  const validationRunError = validationRun.error as ReviewApiError | null;
  const detailError = detail.error as ReviewApiError | null;

  const submitAction = () => {
    if (!selectedTaskId) return;
    const payload: Parameters<typeof updateReviewTask>[1] = { action };
    if (reason.trim()) payload.reason = reason.trim();
    if (action === "CORRECT" && correctionReference.trim()) payload.correction_reference = correctionReference.trim();
    if (action === "REPROCESS" && reprocessJobId.trim()) payload.reprocess_job_id = reprocessJobId.trim();
    if (action === "ESCALATE" && assigneeUserId.trim()) payload.assignee_user_id = assigneeUserId.trim();
    setSuccessMessage("");
    mutation.mutate(payload);
  };

  const assignToMe = () => {
    if (!selectedTaskId || !currentUser.data?.id) return;
    setSuccessMessage("");
    mutation.mutate({ assignee_user_id: currentUser.data.id });
  };

  const refreshActive = () => {
    if (workspaceMode === "VALIDATION") {
      validationIssues.refetch();
    } else {
      reviewTasks.refetch();
    }
  };

  const actionNeedsReason = ["CORRECT", "REJECT", "REPROCESS", "COMMENT", "ESCALATE"].includes(action);
  const actionReady =
    action === "APPROVE"
      ? Boolean(selected && selected.blocking_issue_count === 0)
      : action === "CORRECT"
        ? Boolean(reason.trim() && correctionReference.trim())
        : action === "REPROCESS"
          ? Boolean(reason.trim() && reprocessJobId.trim())
          : action === "ESCALATE"
            ? Boolean(reason.trim() && assigneeUserId.trim())
            : Boolean(reason.trim());

  const panelTitle = workspaceMode === "VALIDATION" ? "Validation issues" : "Cases";
  const panelEyebrow = workspaceMode === "VALIDATION" ? "Cross-record checks" : `${workspaceMode} queue`;

  return (
    <main className="review-shell">
      <header className="review-header">
        <div>
          <p className="eyebrow">Human verification · H.2B.3</p>
          <h1>Review workspace</h1>
          <p>Project: {projectId}</p>
        </div>
        <nav className="review-nav" aria-label="Project review navigation">
          <Link to={`/projects/${projectId}`}>Dashboard</Link>
          <Link to={`/projects/${projectId}/documents`}>Documents</Link>
          <Link to={`/projects/${projectId}/gis`}>Web-GIS</Link>
          <Link to="/">Platform home</Link>
        </nav>
      </header>

      <section className="review-toolbar" aria-label="Review filters">
        <div className="review-tabs" role="tablist" aria-label="Review queue">
          {([
            ["DOCUMENT", "Document review"],
            ["GIS", "GIS review"],
            ["VALIDATION", "Validation issues"],
          ] as Array<[WorkspaceMode, string]>).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={workspaceMode === value}
              className={workspaceMode === value ? "active" : ""}
              onClick={() => setWorkspaceMode(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <label>Status
          <select value={taskStatus} onChange={(event) => setTaskStatus(event.target.value as ReviewTaskStatus | "ALL")}>
            <option value="OPEN">Open</option>
            <option value="RESOLVED">Resolved</option>
            <option value="ALL">All</option>
          </select>
        </label>
        <label>Severity
          <select value={severity} onChange={(event) => setSeverity(event.target.value as ReviewSeverity | "ALL")}>
            <option value="ALL">All</option>
            {severityOrder.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        {workspaceMode === "VALIDATION" && <label>Issue type
          <select value={issueType} onChange={(event) => setIssueType(event.target.value as ValidationIssueType | "ALL")}>
            <option value="ALL">All</option>
            <option value="DUPLICATE_RECORD">Duplicate record</option>
            <option value="AREA_MISMATCH">Area mismatch</option>
          </select>
        </label>}
        <label className="review-checkbox"><input type="checkbox" checked={assignedToMe} onChange={(event) => setAssignedToMe(event.target.checked)} /> Assigned to me</label>
        {workspaceMode === "VALIDATION" && canRunValidation && (
          <button
            type="button"
            className="primary-action"
            onClick={() => validationRun.mutate()}
            disabled={validationRun.isPending}
          >
            {validationRun.isPending ? "Running checks…" : "Run validation checks"}
          </button>
        )}
        <button type="button" onClick={refreshActive} disabled={activeFetching}>{activeFetching ? "Refreshing…" : "Refresh queue"}</button>
      </section>

      {workspaceMode === "VALIDATION" && (
        <section aria-live="polite">
          {validationMessage && <div className="review-success">{validationMessage}</div>}
          {validationRunError && <div className="review-error" role="alert">{validationRunError.message}</div>}
          {!canRunValidation && <p className="review-muted">You can review existing validation issues, but running checks requires <code>validation:run</code>.</p>}
        </section>
      )}

      <section className="review-workspace">
        <aside className="review-list-panel" aria-label="Review tasks">
          <div className="review-panel-heading">
            <div><p className="eyebrow">{panelEyebrow}</p><h2>{panelTitle}</h2></div>
            <span>{activeData?.page.total ?? 0}</span>
          </div>
          {activeLoading && <p className="review-muted">Loading review cases…</p>}
          {activeError && <div className="review-error" role="alert">{activeError.message}</div>}
          {activeData?.items.length === 0 && <p className="review-muted">No cases match the current filters.</p>}
          <div className="review-task-list">
            {activeData?.items.map((task) => <TaskListItem key={task.id} task={task} selected={task.id === selectedTaskId} onSelect={() => setSelectedTaskId(task.id)} />)}
          </div>
        </aside>

        <section className="review-detail-panel" aria-label="Review case details">
          {!selectedTaskId && <div className="review-empty"><h2>No case selected</h2><p>Choose a review case from the queue.</p></div>}
          {selectedTaskId && detail.isLoading && <p className="review-muted">Loading case details…</p>}
          {detailError && <div className="review-error" role="alert">{detailError.message}</div>}
          {selected && <>
            <div className="review-detail-heading">
              <div>
                <p className="eyebrow">{selected.target_type === "VALIDATION_ISSUE" ? "Validation issue" : `${selected.queue_type} review case`}</p>
                <h2>{selected.summary}</h2>
              </div>
              <div className="review-badges">
                <span className={`severity-${selected.severity.toLowerCase()}`}>{selected.severity}</span>
                <span>{selected.status}</span>
                {selected.escalated && <span>ESCALATED</span>}
              </div>
            </div>

            {selected.target_type === "VALIDATION_ISSUE" && (
              <div className="review-warning">
                <strong>{validationIssueLabel(selected.metadata)}</strong>
                <p>
                  {typeof selected.metadata.interpretation === "string"
                    ? selected.metadata.interpretation
                    : "This automated check is a review flag, not a legal determination."}
                </p>
              </div>
            )}

            <div className="review-columns">
              <section className="review-evidence">
                <h3>Evidence & status</h3>
                <dl>
                  <dt>Target</dt><dd>{selected.target_type} · {selected.target_id}</dd>
                  <dt>Assignee</dt><dd>{selected.assignee_user_id ?? "Unassigned"}</dd>
                  <dt>Blocking issues</dt><dd>{selected.blocking_issue_count}</dd>
                  <dt>Created</dt><dd>{formatDate(selected.created_at)}</dd>
                  <dt>Updated</dt><dd>{formatDate(selected.updated_at)}</dd>
                  <dt>Resolution</dt><dd>{selected.resolution_action ?? "Pending"}</dd>
                </dl>

                {selected.queue_type === "GIS" && <p><Link to={`/projects/${projectId}/gis`}>Inspect project GIS evidence</Link></p>}
                {selected.target_type === "VALIDATION_ISSUE" && (
                  <p>
                    <Link to={`/projects/${projectId}/documents`}>Inspect document evidence</Link>
                    {selected.metadata.validation_issue_type === "AREA_MISMATCH" && <> · <Link to={`/projects/${projectId}/gis`}>Inspect parcel geometry</Link></>}
                  </p>
                )}

                <h3>Source references</h3>
                {selected.source_refs.length ? <ul className="review-source-list">{selected.source_refs.map((source) => <li key={source}>{source}</li>)}</ul> : <p className="review-muted">No source reference was attached to this case.</p>}

                <h3>Case metadata</h3>
                {metadataEntries(selected.metadata).length ? <dl>{metadataEntries(selected.metadata).map(([key, value]) => <Fragment key={key}><dt>{key}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></Fragment>)}</dl> : <p className="review-muted">No additional metadata.</p>}
              </section>

              <section className="review-actions">
                <h3>Reviewer actions</h3>
                {!canAct && <p className="review-warning">You have read-only review access. A reviewer with <code>review:act</code> must take action.</p>}
                {canAct && selected.status === "RESOLVED" && <p className="review-muted">This case is resolved and cannot be changed.</p>}
                {canAct && selected.status === "OPEN" && <>
                  <button type="button" className="secondary-action" onClick={assignToMe} disabled={mutation.isPending || selected.assignee_user_id === currentUser.data?.id}>Assign to me</button>
                  <label>Action
                    <select value={action} onChange={(event) => setAction(event.target.value as ReviewAction)}>
                      {actionOptions.map((item) => <option key={item} value={item}>{item}</option>)}
                    </select>
                  </label>

                  {selected.blocking_issue_count > 0 && <p className="review-warning">Approval is blocked until all blocking issues are resolved.</p>}

                  {actionNeedsReason && <label>Reason / comment
                    <textarea value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} placeholder={action === "COMMENT" ? "Add reviewer comment" : "Explain this decision"} />
                  </label>}

                  {action === "CORRECT" && <label>Versioned correction reference
                    <input value={correctionReference} onChange={(event) => setCorrectionReference(event.target.value)} placeholder="e.g. parcel-version:2 or field-version:uuid" />
                  </label>}

                  {action === "REPROCESS" && <label>New processing job ID
                    <input value={reprocessJobId} onChange={(event) => setReprocessJobId(event.target.value)} placeholder="UUID of same-project processing job" />
                  </label>}

                  {action === "ESCALATE" && <label>Escalate to reviewer user ID
                    <input value={assigneeUserId} onChange={(event) => setAssigneeUserId(event.target.value)} placeholder="Reviewer UUID" />
                    <small>Backend validation requires this user to be a project member with review:act.</small>
                  </label>}

                  <button type="button" className="primary-action" onClick={submitAction} disabled={mutation.isPending || !actionReady}>{mutation.isPending ? "Saving…" : `Apply ${action}`}</button>
                </>}
                {mutationError && <div className="review-error" role="alert">{mutationError.message}</div>}
                {successMessage && <div className="review-success" role="status">{successMessage}</div>}
              </section>
            </div>

            <section className="review-audit">
              <h3>Audit & review history</h3>
              <History history={selected.history} />
            </section>
          </>}
        </section>
      </section>
      <p className="review-disclaimer">Validation and verification in this workspace record platform review decisions. They do not by themselves constitute statutory approval, prove duplicate legal records, or determine ownership or parcel boundaries.</p>
    </main>
  );
}

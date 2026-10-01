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
  type ReviewTaskDetail,
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

const SAVED_OCR_DEMO_DOCUMENT_ID = "saved-ocr-land-record-demo";

async function loadSavedOcrReviewDemo(): Promise<ReviewTaskDetail> {
  const response = await fetch("/demo/ocr-land-record-review.json");
  if (!response.ok) throw new Error("Saved OCR review demonstration is unavailable.");
  return response.json() as Promise<ReviewTaskDetail>;
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "Not available";
}

const documentValidationMetadataKeys = new Set([
  "document_id",
  "issue_codes",
  "confidence_summary",
  "validation_version",
  "blocking_issue_codes",
  "validation_result_id",
]);

function metadataEntries(metadata: Record<string, unknown>) {
  const hasDocumentValidationSummary = Boolean(metadata.confidence_summary && typeof metadata.confidence_summary === "object");
  return Object.entries(metadata).filter(([key, value]) =>
    value !== null
    && value !== undefined
    && !(hasDocumentValidationSummary && documentValidationMetadataKeys.has(key)),
  );
}

function validationIssueLabel(metadata: Record<string, unknown>) {
  if (metadata.validation_issue_type === "DUPLICATE_RECORD") return "Duplicate record";
  if (metadata.validation_issue_type === "AREA_MISMATCH") return "Area mismatch";
  return "Validation issue";
}

type ConfidenceField = {
  band?: string;
  field_name?: string;
  candidate_count?: number;
  representative_confidence?: number | null;
};

function humanize(value: string) {
  return value
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function percent(value: unknown) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "Not available";
}

function reviewSummary(task: ReviewTask) {
  if (task.queue_type !== "DOCUMENT" || !task.summary.startsWith("Document extraction review required:")) {
    return task.summary;
  }
  const issueCodes = Array.isArray(task.metadata.issue_codes)
    ? task.metadata.issue_codes.filter((value): value is string => typeof value === "string")
    : [];
  const lowConfidenceCount = issueCodes.filter((code) => code === "LOW_CONFIDENCE").length;
  const otherCodes = [...new Set(issueCodes.filter((code) => code !== "LOW_CONFIDENCE"))];

  if (lowConfidenceCount > 0 && otherCodes.length === 0) {
    return `${lowConfidenceCount} extracted field${lowConfidenceCount === 1 ? "" : "s"} need confidence review`;
  }
  const findings = [
    lowConfidenceCount > 0 ? `${lowConfidenceCount} low-confidence field${lowConfidenceCount === 1 ? "" : "s"}` : null,
    ...otherCodes.map(humanize),
  ].filter(Boolean);
  return findings.length ? `Document extraction needs review · ${findings.join(" · ")}` : "Document extraction needs review";
}

function documentValidationSummary(metadata: Record<string, unknown>) {
  const raw = metadata.confidence_summary;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const summary = raw as Record<string, unknown>;
  const fields = Array.isArray(summary.fields)
    ? summary.fields.filter((field): field is ConfidenceField => Boolean(field && typeof field === "object"))
    : [];
  const lowFields = fields.filter((field) => field.band === "LOW" && (field.candidate_count ?? 0) > 0);
  const missingFields = fields.filter((field) => (field.candidate_count ?? 0) === 0);
  const detectedFields = fields.filter((field) => (field.candidate_count ?? 0) > 0 && field.band !== "LOW");
  const issueCodes = Array.isArray(metadata.issue_codes)
    ? metadata.issue_codes.filter((value): value is string => typeof value === "string")
    : [];
  const issueCounts = issueCodes.reduce<Record<string, number>>((counts, code) => {
    counts[code] = (counts[code] ?? 0) + 1;
    return counts;
  }, {});
  const blockingCodes = Array.isArray(metadata.blocking_issue_codes)
    ? metadata.blocking_issue_codes.filter((value): value is string => typeof value === "string")
    : [];

  return (
    <div className="review-document-validation">
      <div className="review-confidence-overview">
        <article>
          <span>Overall confidence</span>
          <strong>{percent(summary.value)}</strong>
          <small className={`confidence-band confidence-${String(summary.band ?? "UNKNOWN").toLowerCase()}`}>
            {humanize(String(summary.band ?? "UNKNOWN"))}
          </small>
        </article>
        <article><span>Fields needing review</span><strong>{lowFields.length}</strong><small>Below {percent(summary.medium_threshold)}</small></article>
        <article><span>Detected fields</span><strong>{Number(summary.contributing_field_count ?? detectedFields.length + lowFields.length)}</strong><small>With usable confidence</small></article>
        <article><span>Not detected</span><strong>{Number(summary.missing_field_count ?? missingFields.length)}</strong><small>Supported schema fields</small></article>
      </div>

      {Object.keys(issueCounts).length > 0 && (
        <section className="review-validation-section">
          <h4>Review findings</h4>
          <div className="review-finding-chips">
            {Object.entries(issueCounts).map(([code, count]) => (
              <span key={code}>{humanize(code)}{count > 1 ? ` × ${count}` : ""}</span>
            ))}
            <span className={blockingCodes.length ? "review-finding-blocking" : "review-finding-clear"}>
              {blockingCodes.length ? `${blockingCodes.length} blocking` : "No blocking issues"}
            </span>
          </div>
        </section>
      )}

      {lowFields.length > 0 && (
        <section className="review-validation-section">
          <h4>Fields needing attention</h4>
          <div className="review-confidence-fields">
            {lowFields.map((field) => (
              <div className="review-confidence-field needs-review" key={field.field_name}>
                <span>{humanize(field.field_name ?? "Field")}</span>
                <strong>{percent(field.representative_confidence)}</strong>
                <small>Low confidence</small>
              </div>
            ))}
          </div>
        </section>
      )}

      {detectedFields.length > 0 && (
        <details className="review-validation-details">
          <summary>Other extracted fields ({detectedFields.length})</summary>
          <div className="review-confidence-fields">
            {detectedFields.map((field) => (
              <div className="review-confidence-field" key={field.field_name}>
                <span>{humanize(field.field_name ?? "Field")}</span>
                <strong>{percent(field.representative_confidence)}</strong>
                <small>{humanize(field.band ?? "UNKNOWN")}</small>
              </div>
            ))}
          </div>
        </details>
      )}

      {missingFields.length > 0 && (
        <details className="review-validation-details">
          <summary>Fields not detected ({missingFields.length})</summary>
          <div className="review-missing-fields">
            {missingFields.map((field) => <span key={field.field_name}>{humanize(field.field_name ?? "Field")}</span>)}
          </div>
        </details>
      )}

      <details className="review-technical-details">
        <summary>Technical provenance</summary>
        <dl>
          {typeof metadata.document_id === "string" && <><dt>Document ID</dt><dd>{metadata.document_id}</dd></>}
          {typeof metadata.validation_result_id === "string" && <><dt>Validation result ID</dt><dd>{metadata.validation_result_id}</dd></>}
          {typeof metadata.validation_version === "number" && <><dt>Validation version</dt><dd>{metadata.validation_version}</dd></>}
          {typeof summary.policy_version === "string" && <><dt>Policy</dt><dd>{summary.policy_version}</dd></>}
          <dt>High-confidence threshold</dt><dd>{percent(summary.high_threshold)}</dd>
          <dt>Medium-confidence threshold</dt><dd>{percent(summary.medium_threshold)}</dd>
          <dt>Conflicting fields</dt><dd>{Number(summary.conflict_field_count ?? 0)}</dd>
          <dt>Unknown-confidence fields</dt><dd>{Number(summary.unknown_confidence_field_count ?? 0)}</dd>
        </dl>
      </details>
    </div>
  );
}

function TaskListItem({ task, selected, savedDemo = false, onSelect }: { task: ReviewTask; selected: boolean; savedDemo?: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={selected ? "review-task-card selected" : "review-task-card"} onClick={onSelect} aria-pressed={selected}>
      <span className="review-task-card-top">
        <strong>{task.severity}</strong>
        <span>{task.status}</span>
      </span>
      <span className="review-task-summary">{reviewSummary(task)}</span>
      <span className="review-task-meta">
        {savedDemo
          ? "Saved OCR demo · Document verification"
          : task.target_type === "VALIDATION_ISSUE"
            ? validationIssueLabel(task.metadata)
            : task.queue_type === "DOCUMENT"
              ? "Document extraction review"
              : humanize(task.target_type)}
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

  const savedReview = useQuery({
    queryKey: ["saved-ocr-review-demo"],
    queryFn: loadSavedOcrReviewDemo,
    enabled: Boolean(projectId && workspaceMode === "DOCUMENT"),
    retry: false,
    staleTime: Infinity,
  });
  const demoOnlyAccess = Boolean(!canRead && savedReview.data && workspaceMode === "DOCUMENT");
  const liveHasSavedReview = Boolean(
    savedReview.data && reviewTasks.data?.items.some((task) => task.id === savedReview.data?.id),
  );
  const savedReviewMatchesFilters = Boolean(
    savedReview.data
    && !assignedToMe
    && (taskStatus === "ALL" || savedReview.data.status === taskStatus)
    && (severity === "ALL" || savedReview.data.severity === severity),
  );
  const showSavedReview = workspaceMode === "DOCUMENT"
    && savedReviewMatchesFilters
    && (!canRead || reviewTasks.isFetched)
    && !liveHasSavedReview;
  const reviewDataWithSavedDemo = useMemo(() => {
    if (!showSavedReview || !savedReview.data) return reviewTasks.data;
    const livePage = reviewTasks.data?.page ?? { limit: 100, offset: 0, total: 0 };
    return {
      items: [...(reviewTasks.data?.items ?? []), savedReview.data],
      page: { ...livePage, total: livePage.total + 1 },
    };
  }, [reviewTasks.data, savedReview.data, showSavedReview]);

  const activeData = workspaceMode === "VALIDATION" ? validationIssues.data : reviewDataWithSavedDemo;
  const activeLoading = workspaceMode === "VALIDATION"
    ? validationIssues.isLoading
    : demoOnlyAccess
      ? savedReview.isLoading
      : reviewTasks.isLoading || (workspaceMode === "DOCUMENT" && savedReview.isLoading);
  const activeFetching = workspaceMode === "VALIDATION"
    ? validationIssues.isFetching
    : demoOnlyAccess
      ? savedReview.isFetching
      : reviewTasks.isFetching || (workspaceMode === "DOCUMENT" && savedReview.isFetching);
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

  const savedReviewSelected = Boolean(
    showSavedReview && savedReview.data && selectedTaskId === savedReview.data.id,
  );
  const detail = useQuery({
    queryKey: ["review-task", selectedTaskId],
    queryFn: () => loadReviewTask(selectedTaskId!),
    enabled: Boolean(selectedTaskId && canRead && !savedReviewSelected),
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

  if (!canRead && !savedReview.data && (currentUser.isLoading || savedReview.isLoading)) {
    return <main className="review-state"><p className="eyebrow">Human verification · H.2B.3</p><h1>Loading preserved review evidence…</h1></main>;
  }

  if ((currentUser.isError || !canRead) && !savedReview.data) {
    return (
      <main className="review-state">
        <p className="eyebrow">Human verification · H.2B.3</p>
        <h1>Review workspace unavailable</h1>
        <p>The live review service is unavailable and the preserved SIH review evidence could not be loaded.</p>
        <Link to="/">Return to platform</Link>
      </main>
    );
  }

  const selected = savedReviewSelected ? savedReview.data : detail.data;
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
    if (demoOnlyAccess && workspaceMode === "DOCUMENT") {
      savedReview.refetch();
    } else if (workspaceMode === "VALIDATION") {
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
          <p>Review uncertain document, GIS, and validation evidence with source context and auditable decisions.</p>
        </div>
        <nav className="review-nav" aria-label="Project review navigation">
          <Link to={`/projects/${projectId}`}>Dashboard</Link>
          <Link to={`/projects/${projectId}/documents`}>Documents</Link>
          <Link to={`/projects/${projectId}/gis`}>Web-GIS</Link>
          <Link to="/">Platform home</Link>
        </nav>
      </header>

      {demoOnlyAccess && (
        <div className="review-warning" role="status">
          <strong>Saved SIH review demo</strong>
          <p>The live backend reviewer session is unavailable on this deployment, so the preserved tested OCR review is shown here as read-only evidence.</p>
        </div>
      )}

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
              disabled={demoOnlyAccess && value !== "DOCUMENT"}
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
        <label className="review-checkbox"><input type="checkbox" checked={assignedToMe} disabled={demoOnlyAccess} onChange={(event) => setAssignedToMe(event.target.checked)} /> Assigned to me</label>
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
            {activeData?.items.map((task) => (
              <TaskListItem
                key={task.id}
                task={task}
                selected={task.id === selectedTaskId}
                savedDemo={showSavedReview && task.id === savedReview.data?.id}
                onSelect={() => setSelectedTaskId(task.id)}
              />
            ))}
          </div>
        </aside>

        <section className="review-detail-panel" aria-label="Review case details">
          {!selectedTaskId && <div className="review-empty"><h2>No case selected</h2><p>Choose a review case from the queue.</p></div>}
          {selectedTaskId && !savedReviewSelected && detail.isLoading && <p className="review-muted">Loading case details…</p>}
          {!savedReviewSelected && detailError && <div className="review-error" role="alert">{detailError.message}</div>}
          {selected && <>
            <div className="review-detail-heading">
              <div>
                <p className="eyebrow">{selected.target_type === "VALIDATION_ISSUE" ? "Validation issue" : `${selected.queue_type} review case`}</p>
                <h2>{reviewSummary(selected)}</h2>
              </div>
              <div className="review-badges">
                {savedReviewSelected && <span>SAVED DEMO</span>}
                <span className={`severity-${selected.severity.toLowerCase()}`}>{selected.severity}</span>
                <span>{selected.status}</span>
                {selected.escalated && <span>ESCALATED</span>}
              </div>
            </div>

            {savedReviewSelected && (
              <div className="review-warning">
                <strong>Preserved OCR demo review</strong>
                <p>This is the real review task backfilled from the preserved 84.25% validation result and exported with its audit entry. It is read-only here so the SIH walkthrough remains stable.</p>
              </div>
            )}

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
                  <dt>Case type</dt><dd>{selected.queue_type === "DOCUMENT" ? "Document extraction review" : selected.target_type === "VALIDATION_ISSUE" ? validationIssueLabel(selected.metadata) : humanize(selected.target_type)}</dd>
                  <dt>Assignee</dt><dd>{selected.assignee_user_id ? "Assigned reviewer" : "Unassigned"}</dd>
                  <dt>Blocking issues</dt><dd>{selected.blocking_issue_count}</dd>
                  <dt>Created</dt><dd>{formatDate(selected.created_at)}</dd>
                  <dt>Updated</dt><dd>{formatDate(selected.updated_at)}</dd>
                  <dt>Resolution</dt><dd>{selected.resolution_action ?? "Pending"}</dd>
                </dl>

                {selected.queue_type === "DOCUMENT" && <p><Link to={savedReviewSelected ? `/projects/${projectId}/documents?documentId=${SAVED_OCR_DEMO_DOCUMENT_ID}` : `/projects/${projectId}/documents`}>Inspect document evidence</Link></p>}
                {selected.queue_type === "GIS" && <p><Link to={`/projects/${projectId}/gis`}>Inspect project GIS evidence</Link></p>}
                {selected.target_type === "VALIDATION_ISSUE" && (
                  <p>
                    <Link to={`/projects/${projectId}/documents`}>Inspect document evidence</Link>
                    {selected.metadata.validation_issue_type === "AREA_MISMATCH" && <> · <Link to={`/projects/${projectId}/gis`}>Inspect parcel geometry</Link></>}
                  </p>
                )}

                {selected.queue_type === "DOCUMENT" && documentValidationSummary(selected.metadata)}

                <h3>Source references</h3>
                {selected.source_refs.length ? <ul className="review-source-list">{selected.source_refs.map((source) => <li key={source}>{source}</li>)}</ul> : <p className="review-muted">No source reference was attached to this case.</p>}

                {metadataEntries(selected.metadata).length > 0 && <>
                  <h3>Additional case details</h3>
                  <dl>{metadataEntries(selected.metadata).map(([key, value]) => <Fragment key={key}><dt>{humanize(key)}</dt><dd>{typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : JSON.stringify(value)}</dd></Fragment>)}</dl>
                </>}
              </section>

              <section className="review-actions">
                <h3>Reviewer actions</h3>
                {savedReviewSelected && <p className="review-muted">Saved demo review is preserved as read-only evidence. Use a live review case to record a new reviewer decision.</p>}
                {!savedReviewSelected && !canAct && <p className="review-warning">You have read-only review access. A reviewer with <code>review:act</code> must take action.</p>}
                {!savedReviewSelected && canAct && selected.status === "RESOLVED" && <p className="review-muted">This case is resolved and cannot be changed.</p>}
                {!savedReviewSelected && canAct && selected.status === "OPEN" && <>
                  <button type="button" className="secondary-action" onClick={assignToMe} disabled={mutation.isPending || selected.assignee_user_id === currentUser.data?.id}>Assign to me</button>
                  <label>Action
                    <select value={action} onChange={(event) => setAction(event.target.value as ReviewAction)}>
                      {actionOptions.map((item) => <option key={item} value={item}>{humanize(item)}</option>)}
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

                  <button type="button" className="primary-action" onClick={submitAction} disabled={mutation.isPending || !actionReady}>{mutation.isPending ? "Saving…" : `Apply ${humanize(action).toLowerCase()}`}</button>
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

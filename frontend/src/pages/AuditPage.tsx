import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { loadAudit, type AuditEvent } from "../api/audit";
import { loadCurrentUser } from "../api/gis";

function humanize(value: string) {
  return value
    .replace(/[._]+/g, " ")
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

const actionLabels: Record<string, string> = {
  "imagery.preview_requested": "Imagery preview requested",
  "document.source_url_requested": "Document source requested",
  "document.validated": "Document validated",
  "document.review_required": "Document sent for review",
  "project.export_records_csv": "Record CSV exported",
  "project.export_records_xlsx": "Record Excel exported",
  "project.export_parcels_geojson": "Parcel GeoJSON exported",
};

function actionLabel(action: string) {
  return actionLabels[action] ?? humanize(action);
}

function targetLabel(targetType: string) {
  const labels: Record<string, string> = {
    imagery_asset: "Imagery asset",
    document: "Document",
    project: "Project",
    parcel: "Parcel",
    review_task: "Review task",
    processing_job: "Processing job",
  };
  return labels[targetType] ?? humanize(targetType);
}

function metadataEntries(metadata: Record<string, unknown>) {
  return Object.entries(metadata).filter(([, value]) => value !== null && value !== undefined);
}

function formatMetadataValue(value: unknown) {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function AuditEventCard({
  event,
  currentUserId,
  currentUserName,
}: {
  event: AuditEvent;
  currentUserId?: string;
  currentUserName?: string | null;
}) {
  const metadata = metadataEntries(event.metadata);
  const actorLabel = event.actor_id === null
    ? "System"
    : event.actor_id === currentUserId
      ? currentUserName ? `${currentUserName} (you)` : "Current user"
      : "Authorized user";

  return (
    <article className="audit-row">
      <div className="audit-row-heading">
        <div>
          <strong>{actionLabel(event.action)}</strong>
          <small>{targetLabel(event.target_type)}</small>
        </div>
        <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString()}</time>
      </div>

      <dl className="audit-summary">
        <dt>Actor</dt><dd>{actorLabel}</dd>
        <dt>Target</dt><dd>{targetLabel(event.target_type)}</dd>
      </dl>

      <details className="audit-technical-details">
        <summary>Technical details</summary>
        <dl>
          <dt>Action code</dt><dd>{event.action}</dd>
          <dt>Event ID</dt><dd>{event.id}</dd>
          {event.actor_id && <><dt>Actor ID</dt><dd>{event.actor_id}</dd></>}
          {event.target_id && <><dt>Target ID</dt><dd>{event.target_id}</dd></>}
          {metadata.map(([key, value]) => (
            <div className="audit-metadata-entry" key={key}>
              <dt>{humanize(key)}</dt>
              <dd>{formatMetadataValue(value)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </article>
  );
}

export function AuditPage() {
  const { projectId } = useParams();
  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");
  const currentUser = useQuery({ queryKey: ["current-user"], queryFn: loadCurrentUser, retry: false });
  const canRead = currentUser.data?.permissions.includes("audit:read") ?? false;
  const audit = useQuery({
    queryKey: ["project-audit", projectId, action, targetType],
    queryFn: () => loadAudit(projectId!, { action: action || undefined, targetType: targetType || undefined }),
    enabled: Boolean(projectId && currentUser.data && canRead),
    retry: false,
  });

  if (!projectId) return <main className="tool-state"><h1>Audit unavailable</h1></main>;
  if (currentUser.data && !canRead) return <main className="tool-state"><h1>Audit access unavailable</h1><p>Your account does not have audit:read.</p><Link to={`/projects/${projectId}`}>Return to dashboard</Link></main>;

  return <main className="tool-shell audit-page">
    <header className="tool-header audit-header">
      <div>
        <p className="eyebrow">H.2B.4 audit trail</p>
        <h1>Project audit</h1>
        <p>Immutable workflow events with sanitized metadata.</p>
      </div>
      <nav><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to="/">Platform home</Link></nav>
    </header>

    <section className="tool-toolbar audit-toolbar" aria-label="Audit filters">
      <label>Action<input value={action} onChange={(event) => setAction(event.target.value)} placeholder="Exact action, e.g. document.validated" /></label>
      <label>Target type<input value={targetType} onChange={(event) => setTargetType(event.target.value)} placeholder="Exact target type" /></label>
      <button type="button" onClick={() => audit.refetch()}>Refresh</button>
    </section>

    {audit.isLoading && <p className="audit-state">Loading audit events…</p>}
    {audit.isError && <p className="tool-error">Audit events could not be loaded.</p>}

    <section className="audit-list" aria-label="Audit events">
      {audit.data?.items.map((event) => (
        <AuditEventCard
          key={event.id}
          event={event}
          currentUserId={currentUser.data?.id}
          currentUserName={currentUser.data?.full_name}
        />
      ))}
      {audit.data?.items.length === 0 && <p className="audit-empty">No audit events match the current filters.</p>}
    </section>
  </main>;
}

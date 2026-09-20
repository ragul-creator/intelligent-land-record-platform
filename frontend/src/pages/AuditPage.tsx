import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { loadAudit } from "../api/audit";
import { loadCurrentUser } from "../api/gis";

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

  return <main className="tool-shell">
    <header className="tool-header"><div><p className="eyebrow">H.2B.4 audit trail</p><h1>Project audit</h1><p>Immutable workflow events with sanitized metadata.</p></div><nav><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to="/">Platform home</Link></nav></header>
    <section className="tool-toolbar"><label>Action<input value={action} onChange={(event) => setAction(event.target.value)} placeholder="Exact action, e.g. document.validated" /></label><label>Target type<input value={targetType} onChange={(event) => setTargetType(event.target.value)} placeholder="Exact target type" /></label><button type="button" onClick={() => audit.refetch()}>Refresh</button></section>
    {audit.isLoading && <p>Loading audit events…</p>}
    {audit.isError && <p className="tool-error">Audit events could not be loaded.</p>}
    <section className="audit-list" aria-label="Audit events">
      {audit.data?.items.map((event) => <article key={event.id} className="audit-row"><div><strong>{event.action}</strong><span>{new Date(event.created_at).toLocaleString()}</span></div><dl><dt>Actor</dt><dd>{event.actor_id ?? "system"}</dd><dt>Target</dt><dd>{event.target_type}{event.target_id ? ` · ${event.target_id}` : ""}</dd></dl><code>{JSON.stringify(event.metadata)}</code></article>)}
      {audit.data?.items.length === 0 && <p>No audit events match the current filters.</p>}
    </section>
  </main>;
}

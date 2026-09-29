import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { loadCurrentUser } from "../api/gis";
import { loadProjectDashboard, type StatusCount } from "../api/dashboard";

function CountCard({
  label,
  value,
  note,
  tone = "neutral",
}: {
  label: string;
  value: number;
  note?: string;
  tone?: "neutral" | "attention" | "positive";
}) {
  return <article className={`dashboard-card dashboard-card-${tone}`}>
    <span>{label}</span>
    <strong>{value.toLocaleString()}</strong>
    {note && <small>{note}</small>}
  </article>;
}

function StatusList({ items, empty = "No persisted statuses yet." }: { items: StatusCount[]; empty?: string }) {
  if (!items.length) return <p className="dashboard-muted">{empty}</p>;
  return <div className="dashboard-status-list">{items.map((item) => <span key={item.status}>{item.status.replaceAll("_", " ")} <strong>{item.count}</strong></span>)}</div>;
}

function DataRow({ label, value, note }: { label: string; value: number; note?: string }) {
  return <div className="dashboard-data-row">
    <span>{label}</span>
    <strong>{value.toLocaleString()}</strong>
    {note && <small>{note}</small>}
  </div>;
}

function WorkflowIcon({ kind }: { kind: "document" | "map" | "review" }) {
  if (kind === "document") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h8l4 4V20.5H6z" /><path d="M14 3.5v4h4M9 12h6M9 15.5h5" /></svg>;
  }
  if (kind === "map") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 6 5-2 6 2 5-2v14l-5 2-6-2-5 2z" /><path d="M9 4v14M15 6v14" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 19 6v5.5c0 4.2-2.6 7-7 9-4.4-2-7-4.8-7-9V6z" /><path d="m8.5 12 2.2 2.2 4.8-5" /></svg>;
}

function percent(part: number, total: number) {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((part / total) * 100)));
}

export function DashboardPage() {
  const { projectId } = useParams();
  const dashboard = useQuery({ queryKey: ["project-dashboard", projectId], queryFn: () => loadProjectDashboard(projectId!), enabled: Boolean(projectId), retry: false });
  const currentUser = useQuery({ queryKey: ["current-user"], queryFn: loadCurrentUser, enabled: Boolean(projectId), retry: false });

  if (!projectId) return <main className="dashboard-state"><h1>Project dashboard unavailable</h1></main>;
  if (dashboard.isLoading) return <main className="dashboard-state"><p className="eyebrow">Project dashboard</p><h1>Loading operational summary…</h1></main>;
  if (dashboard.isError || !dashboard.data) return <main className="dashboard-state"><p className="eyebrow">Project dashboard</p><h1>Dashboard unavailable</h1><p>The project summary could not be loaded or you do not have dashboard access.</p><button type="button" onClick={() => dashboard.refetch()}>Retry</button></main>;

  const data = dashboard.data;
  const permissions = currentUser.data?.permissions ?? [];
  const canDocuments = permissions.includes("document:read");
  const canGis = permissions.includes("geo:read");
  const canReview = permissions.includes("review:read");
  const canAdmin = permissions.includes("project:update") || permissions.includes("project:member_manage");
  const canAudit = permissions.includes("audit:read");
  const canExport = permissions.includes("export:read");
  const canProjectRead = permissions.includes("project:read");
  const validationIssueCount = data.reviews.validation_issues ?? data.attention.open_validation_issues ?? 0;
  const activeJobs = data.jobs.active ?? data.jobs.by_status.filter((item) => item.status === "QUEUED" || item.status === "PROCESSING").reduce((sum, item) => sum + item.count, 0);
  const failedJobs = data.jobs.failed ?? data.attention.failed_jobs;
  const retryableFailed = data.jobs.retryable_failed ?? 0;
  const validatedShare = percent(data.documents.validated_records, data.documents.total);
  const reviewAssignedShare = percent(data.reviews.assigned_to_me, data.reviews.open);
  const completedJobs = data.jobs.by_status.find((item) => item.status === "COMPLETED")?.count ?? 0;
  const completedShare = percent(completedJobs, data.jobs.total);

  const attention = [
    ["Failed jobs", data.attention.failed_jobs],
    ["Documents requiring review", data.attention.review_required_documents],
    ["High-severity open reviews", data.attention.high_open_reviews],
    ["Validation issues", data.attention.open_validation_issues ?? validationIssueCount],
    ["Ambiguous record ↔ parcel links", data.attention.ambiguous_record_parcel_links],
    ["Parcels requiring review", data.attention.parcels_needing_review],
  ] as Array<[string, number]>;
  const activeAttention = attention.filter(([, count]) => count > 0);
  const visibilityNotice = data.visibility?.notice ?? (data.project_role === "VIEWER"
    ? "Viewer members have read-only access. Draft and unverified evidence remains visibly labelled."
    : "Draft and unverified evidence remains visibly labelled.");

  return <main className="dashboard-shell">
    <header className="dashboard-header dashboard-hero">
      <div className="dashboard-title-block">
        <p className="eyebrow">Land intelligence workspace</p>
        <div className="dashboard-title-line">
          <h1>{data.project.name}</h1>
          <div className="dashboard-badges" aria-label="Project status"><span>{data.project.state}</span><span>{data.project_role}</span></div>
        </div>
        <p>{data.project.description || "Document intelligence and geospatial analysis in one auditable workspace."}</p>
        <div className="dashboard-hero-actions">
          {canDocuments && <Link to={`/projects/${projectId}/documents`}>Add document</Link>}
          {canGis && <Link to={`/projects/${projectId}/gis`}>Open map</Link>}
          {canReview && data.reviews.open > 0 && <Link to={`/projects/${projectId}/review`}>{data.reviews.open} reviews open</Link>}
        </div>
      </div>

      <aside className="dashboard-hero-aside" aria-label="Workspace snapshot">
        <div className="dashboard-cadastral-graphic" aria-hidden="true">
          <svg viewBox="0 0 340 150" role="presentation">
            <path className="cad-boundary" d="M18 24 112 15 168 34 236 20 320 40 308 128 225 136 166 119 94 138 25 116Z" />
            <path className="cad-line" d="M112 15 103 73 94 138M168 34 157 78 166 119M236 20 229 73 225 136M25 75 103 73 157 78 229 73 314 83" />
            <path className="cad-road" d="M12 101c58-17 102-10 151-3 60 9 103 9 162-11" />
            <rect className="cad-building" x="42" y="42" width="28" height="19" rx="2" />
            <rect className="cad-building" x="184" y="49" width="36" height="22" rx="2" />
            <rect className="cad-building" x="254" y="94" width="25" height="18" rx="2" />
            <circle className="cad-node" cx="103" cy="73" r="3" />
            <circle className="cad-node" cx="229" cy="73" r="3" />
          </svg>
          <span className="dashboard-map-label dashboard-map-label-a">PARCELS {data.geo.parcels}</span>
          <span className="dashboard-map-label dashboard-map-label-b">BUILDINGS {data.geo.buildings}</span>
        </div>
        <div className="dashboard-hero-snapshot">
          <div><span>Source evidence</span><strong>{(data.documents.total + data.geo.imagery_assets).toLocaleString()}</strong><small>{data.documents.total} docs · {data.geo.imagery_assets} imagery</small></div>
          <div><span>Spatial features</span><strong>{(data.geo.parcels + data.geo.buildings + data.geo.roads + data.geo.land_use_features).toLocaleString()}</strong><small>Persisted project features</small></div>
        </div>
      </aside>

      <nav className="dashboard-nav" aria-label="Project workspace navigation">
        <Link to="/">Home</Link>
        {canDocuments && <Link to={`/projects/${projectId}/documents`}>Documents</Link>}
        {canGis && <Link to={`/projects/${projectId}/gis`}>Web-GIS</Link>}
        {canReview && <Link to={`/projects/${projectId}/review`}>Review workspace</Link>}
        {canProjectRead && <Link to={`/projects/${projectId}/search`}>Search</Link>}
        {canProjectRead && <Link to={`/projects/${projectId}/jobs`}>Jobs</Link>}
        {canExport && <Link to={`/projects/${projectId}/exports`}>Exports</Link>}
        {canAudit && <Link to={`/projects/${projectId}/audit`}>Audit</Link>}
        {canAdmin && <Link to={`/projects/${projectId}/admin`}>Admin</Link>}
      </nav>
    </header>

    <section className="dashboard-launch-grid" aria-label="Primary workspaces">
      {canDocuments && <Link className="dashboard-launch-card" to={`/projects/${projectId}/documents`}>
        <span className="dashboard-launch-index">01</span>
        <span className="dashboard-workflow-icon"><WorkflowIcon kind="document" /></span>
        <div><p className="eyebrow">Document intelligence</p><h2>OCR & land records</h2><p>Upload source records, inspect OCR evidence, structured fields, confidence, and provenance.</p></div>
        <strong>Open Document AI <span aria-hidden="true">→</span></strong>
      </Link>}
      {canGis && <Link className="dashboard-launch-card" to={`/projects/${projectId}/gis`}>
        <span className="dashboard-launch-index">02</span>
        <span className="dashboard-workflow-icon"><WorkflowIcon kind="map" /></span>
        <div><p className="eyebrow">Spatial intelligence</p><h2>Web-GIS & GeoAI</h2><p>Inspect imagery, buildings, roads, preliminary parcel candidates, and visible-boundary evidence.</p></div>
        <strong>Open Web-GIS <span aria-hidden="true">→</span></strong>
      </Link>}
      {canReview && <Link className="dashboard-launch-card dashboard-launch-card-secondary" to={`/projects/${projectId}/review`}>
        <span className="dashboard-launch-index">03</span>
        <span className="dashboard-workflow-icon"><WorkflowIcon kind="review" /></span>
        <div><p className="eyebrow">Human oversight</p><h2>Review workspace</h2><p>Resolve low-confidence or conflicting evidence while preserving source provenance and audit history.</p></div>
        <strong>Open Review <span aria-hidden="true">→</span></strong>
      </Link>}
    </section>

    {data.project_role === "REVIEWER" && <p className="dashboard-role-note">Reviewer focus: {data.reviews.open} open tasks, {data.reviews.assigned_to_me} assigned to you.</p>}
    {data.project_role === "SURVEYOR" && <p className="dashboard-role-note">Surveyor focus: {data.geo.parcels} parcels and {data.geo.geoai_jobs} GeoAI jobs are visible in the GIS workflow.</p>}
    {data.project_role === "VIEWER" && <p className="dashboard-role-note">Read-only project overview. {visibilityNotice}</p>}

    <section className="dashboard-grid dashboard-metric-strip" aria-label="Project summary">
      <CountCard label="Documents" value={data.documents.total} note={`${data.documents.validated_records} validated`} />
      <CountCard label="Parcels" value={data.geo.parcels} note="Preliminary / draft" />
      <CountCard label="Open reviews" value={data.reviews.open} note={`${data.reviews.assigned_to_me} assigned to you`} tone={data.reviews.open > 0 ? "attention" : "neutral"} />
      <CountCard label="Validation issues" value={validationIssueCount} note="Consistency flags" tone={validationIssueCount > 0 ? "attention" : "positive"} />
      <CountCard label="Active jobs" value={activeJobs} note={failedJobs ? `${failedJobs} failed · ${retryableFailed} retryable` : "No failed jobs"} tone={failedJobs > 0 ? "attention" : "positive"} />
      <CountCard label="Confirmed links" value={data.record_parcel_links.confirmed_records} note="Workflow evidence only" />
    </section>

    <section className={failedJobs > 0 ? "dashboard-attention dashboard-attention-alert" : "dashboard-attention"}>
      <div className="dashboard-attention-heading">
        <span className="dashboard-attention-mark" aria-hidden="true">!</span>
        <div><p className="eyebrow">Needs attention</p><h2>Operational exceptions</h2></div>
      </div>
      {activeAttention.length
        ? <ul>{activeAttention.map(([label, count]) => <li key={label} className={label === "Failed jobs" ? "attention-critical" : ""}><strong>{count}</strong><span>{label}</span></li>)}</ul>
        : <p>No persisted workflow conditions currently require attention.</p>}
    </section>

    <section className="dashboard-operations" aria-label="Operational overview">
      <article className="dashboard-module dashboard-module-document">
        <div className="dashboard-module-head">
          <div><p className="eyebrow">SIH18 · Document AI</p><h2>Record intelligence</h2></div>
          {canDocuments && <Link to={`/projects/${projectId}/documents`} aria-label="Open documents">→</Link>}
        </div>
        <div className="dashboard-progress-block">
          <div className="dashboard-progress-copy"><span>Validated records</span><strong>{data.documents.validated_records} / {data.documents.total}</strong></div>
          <div className="dashboard-progress-track"><i style={{ width: `${validatedShare}%` }} /></div>
        </div>
        <StatusList items={data.documents.by_status} />
        <p className="dashboard-module-note">{data.documents.unlinked_validated_records} validated record{data.documents.unlinked_validated_records === 1 ? "" : "s"} not yet linked to a confirmed parcel.</p>
      </article>

      <article className="dashboard-module dashboard-module-spatial">
        <div className="dashboard-module-head">
          <div><p className="eyebrow">SIH12 · GIS / GeoAI</p><h2>Spatial inventory</h2></div>
          {canGis && <Link to={`/projects/${projectId}/gis`} aria-label="Open Web-GIS">→</Link>}
        </div>
        <div className="dashboard-data-table">
          <DataRow label="Imagery" value={data.geo.imagery_assets} />
          <DataRow label="GeoAI jobs" value={data.geo.geoai_jobs} />
          <DataRow label="Buildings" value={data.geo.buildings} />
          <DataRow label="Roads" value={data.geo.roads} />
          <DataRow label="Land-use features" value={data.geo.land_use_features} />
        </div>
        <StatusList items={data.geo.parcel_statuses} />
      </article>

      <article className="dashboard-module dashboard-module-review">
        <div className="dashboard-module-head">
          <div><p className="eyebrow">Human oversight</p><h2>Review load</h2></div>
          {canReview && <Link to={`/projects/${projectId}/review`} aria-label="Open review workspace">→</Link>}
        </div>
        <div className="dashboard-progress-block">
          <div className="dashboard-progress-copy"><span>Assigned to you</span><strong>{data.reviews.assigned_to_me} / {data.reviews.open}</strong></div>
          <div className="dashboard-progress-track"><i style={{ width: `${reviewAssignedShare}%` }} /></div>
        </div>
        <div className="dashboard-data-table dashboard-data-table-compact">
          <DataRow label="Document" value={data.reviews.document} />
          <DataRow label="GIS" value={data.reviews.gis} />
          <DataRow label="Record ↔ parcel" value={data.reviews.record_parcel_link} />
          <DataRow label="Validation" value={validationIssueCount} />
        </div>
      </article>

      <article className="dashboard-module dashboard-module-ops">
        <div className="dashboard-module-head">
          <div><p className="eyebrow">Operations</p><h2>Processing health</h2></div>
          {canProjectRead && <Link to={`/projects/${projectId}/jobs`} aria-label="Open processing jobs">→</Link>}
        </div>
        <div className="dashboard-progress-block">
          <div className="dashboard-progress-copy"><span>Completed jobs</span><strong>{completedJobs} / {data.jobs.total}</strong></div>
          <div className="dashboard-progress-track"><i style={{ width: `${completedShare}%` }} /></div>
        </div>
        <StatusList items={data.jobs.by_status} />
        <div className="dashboard-ops-foot">
          <span><b>{data.record_parcel_links.confirmed_records}</b> confirmed record links</span>
          <span><b>{retryableFailed}</b> safe retries available</span>
        </div>
      </article>
    </section>

    <section className="dashboard-policy-band" aria-label="Evidence and verification policy">
      <div>
        <span className="dashboard-policy-icon" aria-hidden="true">◎</span>
        <div><p className="eyebrow">Evidence policy</p><h2>Preliminary until verified</h2></div>
      </div>
      <p>{visibilityNotice} Confirmed associations are workflow evidence only; external or statutory verification is not implied.</p>
      <div className="dashboard-policy-links">
        {canAudit && <Link to={`/projects/${projectId}/audit`}>Audit trail</Link>}
        {canExport && <Link to={`/projects/${projectId}/exports`}>Evidence exports</Link>}
      </div>
    </section>

    <section className="demo-flow" aria-label="Demo presentation guide">
      <div><p className="eyebrow">Demo presentation</p><h2>Two focused capabilities</h2><p>Present document intelligence and spatial intelligence as separate, production-oriented workflows. Cross-validation is intentionally outside this demo scope.</p></div>
      <ol>
        {canDocuments && <li><Link to={`/projects/${projectId}/documents`}>1. Document AI / OCR</Link><span>Upload the demo land deed and show OCR evidence, structured extraction, confidence, and provenance.</span></li>}
        {canGis && <li><Link to={`/projects/${projectId}/gis`}>2. Web-GIS / GeoAI</Link><span>Use the prepared imagery to show detected buildings, roads, preliminary parcels, and visible-boundary evidence.</span></li>}
        {canReview && <li><Link to={`/projects/${projectId}/review`}>Optional: human review</Link><span>Use only if needed to demonstrate governance and auditable corrections.</span></li>}
      </ol>
    </section>

    <p className="dashboard-disclaimer">AI cadastral outputs remain preliminary until authorized verification. Dashboard counts are derived from persisted project data; no synthetic progress score is used.</p>
  </main>;
}

import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { loadCurrentUser } from "../api/gis";
import { loadProjectDashboard, type StatusCount } from "../api/dashboard";

function CountCard({ label, value, note }: { label: string; value: number; note?: string }) {
  return <article className="dashboard-card"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</article>;
}

function StatusList({ items, empty = "No persisted statuses yet." }: { items: StatusCount[]; empty?: string }) {
  if (!items.length) return <p className="dashboard-muted">{empty}</p>;
  return <div className="dashboard-status-list">{items.map((item) => <span key={item.status}>{item.status.replaceAll("_", " ")} <strong>{item.count}</strong></span>)}</div>;
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
    <header className="dashboard-header">
      <div className="dashboard-title-block">
        <p className="eyebrow">Land intelligence workspace</p>
        <h1>{data.project.name}</h1>
        <p>{data.project.description || "Document intelligence and geospatial analysis in one auditable workspace."}</p>
        <div className="dashboard-badges"><span>{data.project.state}</span><span>{data.project_role}</span></div>
      </div>
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

    <section className="dashboard-launch-grid" aria-label="Primary demo workspaces">
      {canDocuments && <Link className="dashboard-launch-card" to={`/projects/${projectId}/documents`}>
        <span className="dashboard-launch-index">01</span>
        <div><p className="eyebrow">Document intelligence</p><h2>OCR & land records</h2><p>Upload a land document, inspect OCR evidence, structured fields, confidence, and provenance.</p></div>
        <strong>Open Document AI →</strong>
      </Link>}
      {canGis && <Link className="dashboard-launch-card" to={`/projects/${projectId}/gis`}>
        <span className="dashboard-launch-index">02</span>
        <div><p className="eyebrow">Spatial intelligence</p><h2>Web-GIS & GeoAI</h2><p>Inspect imagery, detected buildings, roads, preliminary parcel candidates, and visible boundary evidence.</p></div>
        <strong>Open Web-GIS →</strong>
      </Link>}
      {canReview && <Link className="dashboard-launch-card dashboard-launch-card-secondary" to={`/projects/${projectId}/review`}>
        <span className="dashboard-launch-index">03</span>
        <div><p className="eyebrow">Human oversight</p><h2>Review workspace</h2><p>Resolve low-confidence or conflicting evidence while preserving source provenance and audit history.</p></div>
        <strong>Open Review →</strong>
      </Link>}
    </section>

    {data.project_role === "REVIEWER" && <p className="dashboard-role-note">Reviewer focus: {data.reviews.open} open tasks, {data.reviews.assigned_to_me} assigned to you.</p>}
    {data.project_role === "SURVEYOR" && <p className="dashboard-role-note">Surveyor focus: {data.geo.parcels} parcels and {data.geo.geoai_jobs} GeoAI jobs are visible in the GIS workflow.</p>}
    {data.project_role === "VIEWER" && <p className="dashboard-role-note">Read-only project overview. {visibilityNotice}</p>}

    <section className="dashboard-grid" aria-label="Project summary">
      <CountCard label="Documents" value={data.documents.total} note={`${data.documents.validated_records} validated`} />
      <CountCard label="Parcels" value={data.geo.parcels} note="Preliminary/draft until authorized verification" />
      <CountCard label="Open reviews" value={data.reviews.open} note={`${data.reviews.assigned_to_me} assigned to you`} />
      <CountCard label="Validation issues" value={validationIssueCount} note="Duplicate and area consistency review flags" />
      <CountCard label="Active jobs" value={activeJobs} note={failedJobs ? `${failedJobs} failed · ${retryableFailed} retryable` : "No failed jobs"} />
      <CountCard label="Confirmed record links" value={data.record_parcel_links.confirmed_records} note="Workflow associations, not statutory ownership proof" />
    </section>

    <section className="dashboard-attention"><div><p className="eyebrow">Needs attention</p><h2>Operational exceptions</h2></div>{activeAttention.length ? <ul>{activeAttention.map(([label, count]) => <li key={label}><strong>{count}</strong> {label}</li>)}</ul> : <p>No persisted workflow conditions currently require attention.</p>}</section>

    <div className="dashboard-sections">
      <section><h2>SIH18 · Document AI</h2><StatusList items={data.documents.by_status} /><p>{data.documents.validated_records} validated records · {data.documents.unlinked_validated_records} not yet linked to a confirmed parcel.</p></section>
      <section><h2>Human review</h2><div className="dashboard-mini-grid"><CountCard label="Document" value={data.reviews.document} /><CountCard label="GIS" value={data.reviews.gis} /><CountCard label="Record ↔ parcel" value={data.reviews.record_parcel_link} /><CountCard label="Validation" value={validationIssueCount} /></div></section>
      <section><h2>SIH12 · GIS / GeoAI</h2><div className="dashboard-mini-grid"><CountCard label="Imagery" value={data.geo.imagery_assets} /><CountCard label="GeoAI jobs" value={data.geo.geoai_jobs} /><CountCard label="Buildings" value={data.geo.buildings} /><CountCard label="Roads" value={data.geo.roads} /><CountCard label="Land-use features" value={data.geo.land_use_features} /></div><StatusList items={data.geo.parcel_statuses} /></section>
      <section><h2>Record ↔ parcel workflow</h2><StatusList items={data.record_parcel_links.by_status} /><p>Confirmed associations are workflow evidence only; external/statutory verification is not implied.</p></section>
      <section><h2>Jobs</h2><StatusList items={data.jobs.by_status} /><p>{activeJobs} active · {failedJobs} failed · {retryableFailed} eligible for safe manual retry.</p>{canProjectRead && <Link to={`/projects/${projectId}/jobs`}>Open processing jobs</Link>}</section>
      <section><h2>Visibility policy</h2><p>{visibilityNotice}</p><p>Draft/preliminary labels and verification state are preserved in UI, search, and exports.</p></section>
    </div>

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

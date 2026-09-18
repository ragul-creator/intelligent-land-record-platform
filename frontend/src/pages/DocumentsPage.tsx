import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { loadCurrentUser } from "../api/gis";
import { correctDocumentField, loadDocument, loadDocuments, loadFields, processDocument, uploadDocument } from "../api/documents";
import { loadDocumentRecordLinks, resolveRecordParcelLink, suggestDocumentRecordLinks, type RecordParcelLink } from "../api/recordLinks";

export function DocumentsPage() {
  const { projectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | undefined>(() => searchParams.get("documentId") ?? undefined);
  const [file, setFile] = useState<File>();
  const [resolutionReason, setResolutionReason] = useState("");

  useEffect(() => {
    const requested = searchParams.get("documentId") ?? undefined;
    if (requested && requested !== selected) setSelected(requested);
  }, [searchParams, selected]);

  const chooseDocument = (id: string) => {
    setSelected(id);
    setResolutionReason("");
    setSearchParams({ documentId: id });
  };

  const user = useQuery({ queryKey: ["current-user"], queryFn: loadCurrentUser, retry: false });
  const documents = useQuery({ queryKey: ["documents", projectId], queryFn: () => loadDocuments(projectId!), enabled: Boolean(projectId), retry: false });
  const detail = useQuery({ queryKey: ["document", projectId, selected], queryFn: () => loadDocument(projectId!, selected!), enabled: Boolean(projectId && selected), retry: false });
  const fields = useQuery({ queryKey: ["document-fields", projectId, selected], queryFn: () => loadFields(projectId!, selected!), enabled: Boolean(projectId && selected), retry: false });
  const links = useQuery({ queryKey: ["record-parcel-links", projectId, selected], queryFn: () => loadDocumentRecordLinks(projectId!, selected!), enabled: Boolean(projectId && selected), retry: false });

  const permissions = user.data?.permissions ?? [];
  const membership = user.data?.project_memberships.some((item) => item.project_id === projectId);
  const can = (permission: string) => Boolean(membership && permissions.includes(permission));
  const refreshDocuments = () => client.invalidateQueries({ queryKey: ["documents", projectId] });
  const refreshLinks = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["record-parcel-links", projectId, selected] }),
      client.invalidateQueries({ queryKey: ["project-dashboard", projectId] }),
    ]);
  };

  const upload = useMutation({ mutationFn: () => uploadDocument(projectId!, file!), onSuccess: async (item) => { chooseDocument(item.id); setFile(undefined); await refreshDocuments(); } });
  const process = useMutation({ mutationFn: (reprocess: boolean) => processDocument(projectId!, selected!, reprocess), onSuccess: refreshDocuments });
  const suggest = useMutation({
    mutationFn: () => suggestDocumentRecordLinks(projectId!, selected!, detail.data!.latest_validation!.id),
    onSuccess: refreshLinks,
  });
  const resolve = useMutation({
    mutationFn: ({ link, action }: { link: RecordParcelLink; action: "confirm" | "reject" }) => resolveRecordParcelLink(projectId!, link.id, action, resolutionReason),
    onSuccess: async () => { setResolutionReason(""); await refreshLinks(); },
  });

  if (!projectId) return <main className="app-shell"><h1>Documents route unavailable</h1></main>;

  return <main className="documents-shell">
    <header className="documents-header">
      <div><p className="eyebrow">SIH18 · Document AI</p><h1>Project documents</h1><p>OCR and extracted fields are preliminary evidence, not verified land records.</p></div>
      <nav className="documents-nav"><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to={`/projects/${projectId}/gis`}>Web-GIS</Link><Link to={`/projects/${projectId}/review`}>Review workspace</Link></nav>
    </header>

    {can("document:upload") && <section className="document-upload"><input aria-label="Choose document" type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={(event) => setFile(event.target.files?.[0])} /><button disabled={!file || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? "Uploading…" : "Upload document"}</button>{upload.error && <p role="alert">Upload failed.</p>}</section>}

    <section className="documents-grid">
      <aside><h2>Documents</h2>{documents.data?.items.map((item) => <button key={item.id} className="document-row" aria-pressed={selected === item.id} onClick={() => chooseDocument(item.id)}><strong>{item.filename}</strong><span>{item.status}</span></button>)}{documents.data?.items.length === 0 && <p>No documents have been uploaded.</p>}</aside>
      <section className="document-detail">
        {!selected && <p>Select a document to inspect its evidence and parcel association.</p>}
        {detail.data && <>
          <h2>{detail.data.filename}</h2>
          <p><strong>Status:</strong> {detail.data.status}</p>
          {can(detail.data.status === "UPLOADED" || detail.data.status === "FAILED" ? "document:process" : "document:reprocess") && <button onClick={() => process.mutate(detail.data.status !== "UPLOADED" && detail.data.status !== "FAILED")}>{process.isPending ? "Queuing…" : detail.data.status === "UPLOADED" || detail.data.status === "FAILED" ? "Process" : "Reprocess"}</button>}

          {detail.data.latest_ocr && <section><h3>Preliminary OCR</h3><p>{detail.data.latest_ocr.engine} · {detail.data.latest_ocr.requested_languages.join("+")} · confidence {detail.data.latest_ocr.confidence ?? "unknown"}</p>{detail.data.latest_ocr.payload.pages?.slice(0, 2).map((page, index) => <pre key={index}>{page.text}</pre>)}</section>}

          <section><h3>Extracted fields</h3>{fields.data?.fields.map((field) => <Field key={field.id} field={field} canCorrect={can("field:correct")} onCorrect={(value, reason) => correctDocumentField(projectId, selected!, field.id, value, reason).then(() => client.invalidateQueries({ queryKey: ["document-fields", projectId, selected] }))} />)}</section>

          {detail.data.latest_validation && <section><h3>Validation: {detail.data.latest_validation.status}</h3><p>Persisted validation version {detail.data.latest_validation.version}</p><pre>{JSON.stringify(detail.data.latest_validation.confidence_summary, null, 2)}</pre>{detail.data.latest_validation.report.issues?.map((issue) => <p key={`${issue.code}-${issue.message}`}>{issue.severity}: {issue.message}</p>)}{detail.data.latest_validation.review_task_id && <Link to={`/projects/${projectId}/review`}>Open linked review case</Link>}</section>}

          {(suggest.error || resolve.error) && <p className="error-copy" role="alert">{resolve.error ? "Unable to resolve the parcel association. Rejection requires a reason and confirmed records cannot be replaced implicitly." : "Unable to generate parcel candidates from the persisted validated evidence."}</p>}
          <RecordParcelLinksPanel
            projectId={projectId}
            links={links.data?.items ?? []}
            loading={links.isLoading}
            canSuggest={can("validation:run")}
            canResolve={can("validation:resolve")}
            validationId={detail.data.latest_validation?.id ?? null}
            suggesting={suggest.isPending}
            resolving={resolve.isPending}
            reason={resolutionReason}
            onReason={setResolutionReason}
            onSuggest={() => suggest.mutate()}
            onResolve={(link, action) => resolve.mutate({ link, action })}
          />
        </>}
      </section>
    </section>
  </main>;
}

function RecordParcelLinksPanel({ projectId, links, loading, canSuggest, canResolve, validationId, suggesting, resolving, reason, onReason, onSuggest, onResolve }: {
  projectId: string;
  links: RecordParcelLink[];
  loading: boolean;
  canSuggest: boolean;
  canResolve: boolean;
  validationId: string | null;
  suggesting: boolean;
  resolving: boolean;
  reason: string;
  onReason: (value: string) => void;
  onSuggest: () => void;
  onResolve: (link: RecordParcelLink, action: "confirm" | "reject") => void;
}) {
  return <section className="record-link-panel" aria-label="Record to parcel association">
    <div className="record-link-heading"><div><p className="eyebrow">G.1 + G.3 integration</p><h3>Record ↔ parcel association</h3></div>{canSuggest && validationId && <button disabled={suggesting} onClick={onSuggest}>{suggesting ? "Matching…" : links.length ? "Refresh candidates" : "Find matching parcels"}</button>}</div>
    <p className="panel-note">Associations are workflow evidence only. Imagery alone never establishes an official parcel identity or statutory ownership.</p>
    {loading && <p>Loading parcel associations…</p>}
    {!loading && links.length === 0 && <p>No persisted parcel association exists for this validated record.</p>}
    {links.map((link) => <article className="record-link-card" key={link.id}>
      <div><strong>{link.parcel_display_identifier ?? link.parcel_id}</strong><span className={`link-status link-${link.link_status.toLowerCase()}`}>{link.link_status.replaceAll("_", " ")}</span></div>
      <p>{link.link_method.replaceAll("_", " ")} · confidence {link.confidence === null ? "not available" : `${Math.round(link.confidence * 100)}%`}</p>
      <p>Parcel UUID: <code>{link.parcel_id}</code></p>
      <div className="record-link-actions"><Link to={`/projects/${projectId}/gis?parcelId=${link.parcel_id}`}>Open parcel in Web-GIS</Link>{link.review_task_id && <Link to={`/projects/${projectId}/review`}>Open review task</Link>}</div>
      {canResolve && (link.link_status === "SUGGESTED" || link.link_status === "REVIEW_REQUIRED") && <div className="record-link-resolution"><input aria-label="Link resolution reason" placeholder="Optional reviewer reason" value={reason} onChange={(event) => onReason(event.target.value)} /><button disabled={resolving} onClick={() => onResolve(link, "confirm")}>Confirm association</button><button disabled={resolving || !reason.trim()} onClick={() => onResolve(link, "reject")}>Reject association</button></div>}
    </article>)}
  </section>;
}

function Field({ field, canCorrect, onCorrect }: { field: { field_name: string; original_value: string; normalized_value: unknown; confidence: number | null; corrections: Array<{ corrected_value: string; reason: string }> }; canCorrect: boolean; onCorrect: (value: string, reason: string) => void }) {
  const [value, setValue] = useState(field.original_value);
  const [reason, setReason] = useState("");
  return <article className="document-field"><strong>{field.field_name}</strong><p>Original evidence: {field.original_value}</p><p>Normalized: {typeof field.normalized_value === "string" ? field.normalized_value : JSON.stringify(field.normalized_value)} · confidence {field.confidence ?? "unknown"}</p>{field.corrections.map((item, index) => <p key={index}>Correction: {item.corrected_value} ({item.reason})</p>)}{canCorrect && <><input aria-label={`${field.field_name} corrected value`} value={value} onChange={(event) => setValue(event.target.value)} /><input aria-label={`${field.field_name} correction reason`} placeholder="Reason required" value={reason} onChange={(event) => setReason(event.target.value)} /><button disabled={!value.trim() || !reason.trim()} onClick={() => onCorrect(value, reason)}>Save correction</button></>}</article>;
}

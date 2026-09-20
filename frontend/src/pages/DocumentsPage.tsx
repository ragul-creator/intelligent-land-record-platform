import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { loadCurrentUser } from "../api/gis";
import { correctDocumentField, loadDocument, loadDocumentSourceUrl, loadDocuments, loadFields, processDocument, uploadDocument, type DocumentDetail, type DocumentField } from "../api/documents";
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
  const detail = useQuery({
    queryKey: ["document", projectId, selected],
    queryFn: () => loadDocument(projectId!, selected!),
    enabled: Boolean(projectId && selected),
    retry: false,
    refetchInterval: (query) => {
      const status = (query.state.data as DocumentDetail | undefined)?.status;
      return status && ["QUEUED", "PROCESSING", "EXTRACTED", "VALIDATING"].includes(status) ? 1200 : false;
    },
  });
  const fields = useQuery({ queryKey: ["document-fields", projectId, selected], queryFn: () => loadFields(projectId!, selected!), enabled: Boolean(projectId && selected), retry: false });
  const source = useQuery({ queryKey: ["document-source", projectId, selected], queryFn: () => loadDocumentSourceUrl(projectId!, selected!), enabled: Boolean(projectId && selected), retry: false, staleTime: 8 * 60_000 });
  const links = useQuery({ queryKey: ["record-parcel-links", projectId, selected], queryFn: () => loadDocumentRecordLinks(projectId!, selected!), enabled: Boolean(projectId && selected), retry: false });

  const permissions = user.data?.permissions ?? [];
  const projectMembership = user.data?.project_memberships.find((item) => item.project_id === projectId);
  const membership = Boolean(projectMembership);
  const viewerReadOnly = projectMembership?.role === "VIEWER";
  const can = (permission: string) => Boolean(membership && permissions.includes(permission));
  const refreshDocuments = () => client.invalidateQueries({ queryKey: ["documents", projectId] });
  const refreshDocumentEvidence = async () => {
    await Promise.all([
      refreshDocuments(),
      client.invalidateQueries({ queryKey: ["document", projectId, selected] }),
      client.invalidateQueries({ queryKey: ["document-fields", projectId, selected] }),
      client.invalidateQueries({ queryKey: ["project-dashboard", projectId] }),
    ]);
  };
  const refreshLinks = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["record-parcel-links", projectId, selected] }),
      client.invalidateQueries({ queryKey: ["project-dashboard", projectId] }),
    ]);
  };

  const upload = useMutation({ mutationFn: () => uploadDocument(projectId!, file!), onSuccess: async (item) => { chooseDocument(item.id); setFile(undefined); await refreshDocuments(); } });
  const process = useMutation({ mutationFn: (reprocess: boolean) => processDocument(projectId!, selected!, reprocess), onSuccess: refreshDocumentEvidence });
  const suggest = useMutation({
    mutationFn: () => suggestDocumentRecordLinks(projectId!, selected!, detail.data!.latest_validation!.id),
    onSuccess: refreshLinks,
  });
  const resolve = useMutation({
    mutationFn: ({ link, action }: { link: RecordParcelLink; action: "confirm" | "reject" }) => resolveRecordParcelLink(projectId!, link.id, action, resolutionReason),
    onSuccess: async () => { setResolutionReason(""); await refreshLinks(); },
  });

  useEffect(() => {
    if (!detail.data?.latest_ocr?.id || !projectId || !selected) return;
    void client.invalidateQueries({ queryKey: ["document-fields", projectId, selected] });
  }, [client, detail.data?.latest_ocr?.id, projectId, selected]);

  if (!projectId) return <main className="app-shell"><h1>Documents route unavailable</h1></main>;

  return <main className="documents-shell">
    <header className="documents-header">
      <div><p className="eyebrow">SIH18 · Document AI</p><h1>Project documents</h1><p>OCR and extracted fields are preliminary evidence, not verified land records.</p></div>
      <nav className="documents-nav"><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to={`/projects/${projectId}/gis`}>Web-GIS</Link><Link to={`/projects/${projectId}/review`}>Review workspace</Link></nav>
    </header>

    {viewerReadOnly && <p className="viewer-visibility-note" role="status">Viewer policy: preliminary document/OCR evidence is visible in read-only mode. Upload, processing, corrections, validation actions, and review changes remain permission-gated.</p>}

    {can("document:upload") && <section className="document-upload"><input aria-label="Choose document" type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={(event) => setFile(event.target.files?.[0])} /><button disabled={!file || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? "Uploading…" : "Upload document"}</button>{upload.error && <p role="alert">Upload failed.</p>}</section>}

    <section className="documents-grid">
      <aside><h2>Documents</h2>{documents.data?.items.map((item) => <button key={item.id} className="document-row" aria-pressed={selected === item.id} onClick={() => chooseDocument(item.id)}><strong>{item.filename}</strong><span>{item.status}</span></button>)}{documents.data?.items.length === 0 && <p>No documents have been uploaded.</p>}</aside>
      <section className="document-detail">
        {!selected && <p>Select a document to inspect its evidence and parcel association.</p>}
        {detail.data && <>
          <h2>{detail.data.filename}</h2>
          <p><strong>Status:</strong> {detail.data.status}</p>
          {detail.data.filename.startsWith("tn_demo_") && <p className="demo-fixture-note">Synthetic H.2 evidence snapshot. Upload a new PDF/image when demonstrating live OCR; seeded evidence is kept stable for the judging walkthrough.</p>}
          {!detail.data.filename.startsWith("tn_demo_") && can(detail.data.status === "UPLOADED" || detail.data.status === "FAILED" ? "document:process" : "document:reprocess") && <button onClick={() => process.mutate(detail.data.status !== "UPLOADED" && detail.data.status !== "FAILED")}>{process.isPending ? "Queuing…" : detail.data.status === "UPLOADED" || detail.data.status === "FAILED" ? "Process" : "Reprocess"}</button>}

          <DocumentEvidenceViewer detail={detail.data} sourceUrl={source.data?.source_url ?? null} sourceLoading={source.isLoading} sourceError={source.isError} />

          <section aria-label="Extracted field evidence">
            <h3>Structured land-record fields</h3>
            <p className="panel-note">The system extracts labelled values from OCR into structured fields while keeping every value linked to the source evidence. These values remain preliminary until human review/validation.</p>
            {fields.isLoading && <p>Extracting structured fields…</p>}
            {!fields.isLoading && (fields.data?.fields.length ?? 0) === 0 && <p className="structured-empty">{["QUEUED", "PROCESSING", "EXTRACTED", "VALIDATING"].includes(detail.data.status) ? "OCR/extraction is still running. This section updates automatically." : "No label-grounded structured fields were extracted from this OCR result. Review the OCR evidence or reprocess the document."}</p>}
            {(fields.data?.fields.length ?? 0) > 0 && <StructuredRecord fields={fields.data!.fields} />}
            {fields.data?.fields.map((field) => <Field key={field.id} field={field} canCorrect={can("field:correct")} onCorrect={(value, reason) => correctDocumentField(projectId, selected!, field.id, value, reason).then(() => client.invalidateQueries({ queryKey: ["document-fields", projectId, selected] }))} />)}
          </section>

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

function formatConfidence(value: number | null) {
  return value === null ? "unknown" : `${Math.round(value * 100)}%`;
}

const FIELD_LABELS: Record<string, string> = {
  survey_number: "Survey number",
  khasra_number: "Khasra number",
  khata_number: "Khata number",
  owner_details: "Owner",
  plot_area: "Plot area",
  village: "Village",
  tehsil: "Tehsil / Taluk",
  district: "District",
  land_classification: "Land classification",
  mutation_records: "Mutation records",
  registration_information: "Registration information",
};

function structuredValue(field: DocumentField) {
  const corrected = field.corrections[field.corrections.length - 1]?.corrected_value;
  if (corrected) return corrected;
  if (typeof field.normalized_value === "string") return field.normalized_value;
  if (field.normalized_value && typeof field.normalized_value === "object") {
    const area = field.normalized_value as { value?: number | string; unit?: string };
    if (area.value !== undefined && area.unit) return `${area.value} ${area.unit.replaceAll("_", " ")}`;
    return JSON.stringify(field.normalized_value);
  }
  return field.original_value;
}

function StructuredRecord({ fields }: { fields: DocumentField[] }) {
  return <div className="structured-record" aria-label="Structured record summary">
    <table>
      <thead><tr><th>Field</th><th>Extracted value</th><th>Confidence</th><th>Evidence</th></tr></thead>
      <tbody>{fields.map((field) => <tr key={field.id} className={field.confidence !== null && field.confidence < 0.75 ? "structured-low-confidence" : undefined}>
        <th scope="row">{FIELD_LABELS[field.field_name] ?? field.field_name.replaceAll("_", " ")}</th>
        <td>{structuredValue(field)}</td>
        <td>{formatConfidence(field.confidence)}</td>
        <td>Page {field.page_number}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function DocumentEvidenceViewer({ detail, sourceUrl, sourceLoading, sourceError }: {
  detail: DocumentDetail;
  sourceUrl: string | null;
  sourceLoading: boolean;
  sourceError: boolean;
}) {
  const pages = detail.latest_ocr?.payload.pages ?? [];
  const [activePage, setActivePage] = useState(1);
  const page = pages.find((item) => item.page_number === activePage) ?? pages[0] ?? null;

  useEffect(() => {
    setActivePage(pages[0]?.page_number ?? 1);
  }, [detail.id, detail.latest_ocr?.id]);

  const lowConfidenceRegions = page?.regions.filter((region) => region.confidence !== null && region.confidence < 0.75).length ?? 0;
  const isPdf = detail.content_type === "application/pdf";

  return <section className="document-evidence" aria-label="Document evidence viewer">
    <div className="document-evidence-heading">
      <div><p className="eyebrow">H.2B.2 evidence</p><h3>Source ↔ OCR evidence</h3></div>
      {detail.latest_ocr && <span>{detail.latest_ocr.engine} · {detail.latest_ocr.requested_languages.join("+")} · {formatConfidence(detail.latest_ocr.confidence)}</span>}
    </div>
    <div className="document-evidence-grid">
      <section className="document-source-pane" aria-label="Immutable source document">
        <div className="evidence-pane-heading"><strong>Immutable source</strong>{sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer">Open source</a>}</div>
        {sourceLoading && <p>Loading signed source preview…</p>}
        {sourceError && <p className="error-copy">The private source preview could not be loaded.</p>}
        {sourceUrl && isPdf && <iframe title="Source document preview" src={sourceUrl} />}
        {sourceUrl && !isPdf && <img src={sourceUrl} alt={`Source document ${detail.filename}`} />}
        {!sourceLoading && !sourceError && !sourceUrl && <p>No source preview is available.</p>}
        <p className="panel-note">The signed URL is temporary. The original object is not modified by OCR, extraction, or correction.</p>
      </section>
      <section className="document-ocr-pane" aria-label="OCR evidence">
        {!detail.latest_ocr && <p>No OCR result is available yet.</p>}
        {detail.latest_ocr && <>
          <div className="ocr-page-tabs" aria-label="OCR pages">{pages.map((item) => <button type="button" key={item.page_number} aria-pressed={page?.page_number === item.page_number} onClick={() => setActivePage(item.page_number)}>Page {item.page_number}</button>)}</div>
          {page && <>
            <dl className="evidence-metadata">
              <dt>Page confidence</dt><dd>{formatConfidence(page.confidence)}</dd>
              <dt>Dimensions</dt><dd>{page.width} × {page.height} px</dd>
              <dt>Low-confidence tokens</dt><dd>{lowConfidenceRegions}</dd>
              <dt>Preprocessing</dt><dd>{page.preprocessing.operations.length ? page.preprocessing.operations.join(", ") : "none recorded"}</dd>
              <dt>Model</dt><dd>{page.model_version ?? detail.latest_ocr.model_version ?? "engine default"}</dd>
              <dt>Processed</dt><dd>{new Date(page.processed_at).toLocaleString()}</dd>
            </dl>
            <pre className="ocr-evidence-text">{page.text || "No text recognized on this page."}</pre>
            {page.regions.length > 0 && <details><summary>Token evidence ({page.regions.length})</summary><div className="ocr-token-list">{page.regions.slice(0, 60).map((region, index) => <span key={index} className={region.confidence !== null && region.confidence < 0.75 ? "ocr-token low-confidence" : "ocr-token"} title={region.bounding_box ? `bbox ${region.bounding_box.left},${region.bounding_box.top},${region.bounding_box.width},${region.bounding_box.height}` : "No bounding box"}>{region.text} <small>{formatConfidence(region.confidence)}</small></span>)}</div></details>}
          </>}
        </>}
      </section>
    </div>
  </section>;
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

function Field({ field, canCorrect, onCorrect }: { field: DocumentField; canCorrect: boolean; onCorrect: (value: string, reason: string) => void }) {
  const [value, setValue] = useState(field.original_value);
  const [reason, setReason] = useState("");
  const lowConfidence = field.confidence !== null && field.confidence < 0.75;
  return <article className={lowConfidence ? "document-field low-confidence-field" : "document-field"}>
    <div className="document-field-heading"><strong>{field.field_name}</strong>{lowConfidence && <span>LOW CONFIDENCE</span>}</div>
    <p>Original evidence: {field.original_value}</p>
    <p>Normalized: {typeof field.normalized_value === "string" ? field.normalized_value : JSON.stringify(field.normalized_value)} · confidence {formatConfidence(field.confidence)}</p>
    <dl className="field-provenance">
      <dt>Page</dt><dd>{field.page_number}</dd>
      <dt>Source</dt><dd>{field.source_id}</dd>
      <dt>Bounding box</dt><dd>{field.bounding_box ? `${field.bounding_box.left}, ${field.bounding_box.top}, ${field.bounding_box.width}, ${field.bounding_box.height}` : "not available"}</dd>
      <dt>OCR model</dt><dd>{field.model_version ?? "not supplied"}</dd>
      <dt>Extractor</dt><dd>{field.extractor_version}</dd>
      <dt>Processed</dt><dd>{new Date(field.processed_at).toLocaleString()}</dd>
    </dl>
    {field.corrections.map((item, index) => <p key={index}>Correction: {item.corrected_value} ({item.reason})</p>)}
    {canCorrect && <><input aria-label={`${field.field_name} corrected value`} value={value} onChange={(event) => setValue(event.target.value)} /><input aria-label={`${field.field_name} correction reason`} placeholder="Reason required" value={reason} onChange={(event) => setReason(event.target.value)} /><button disabled={!value.trim() || !reason.trim()} onClick={() => onCorrect(value, reason)}>Save correction</button></>}
  </article>;
}

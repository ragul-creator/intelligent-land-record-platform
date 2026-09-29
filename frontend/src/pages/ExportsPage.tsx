import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { downloadProjectExport, loadExportManifest, type ExportDescriptor } from "../api/platform";

function exportFormat(code: string, mediaType: string) {
  if (code.includes("XLSX")) return "XLSX workbook";
  if (code.includes("CSV")) return "CSV";
  if (code.includes("GEOPACKAGE")) return "GeoPackage";
  if (code.includes("GEOJSON")) return "GeoJSON";
  return mediaType;
}

export function ExportsPage() {
  const { projectId } = useParams();
  const manifest = useQuery({ queryKey: ["project-exports", projectId], queryFn: () => loadExportManifest(projectId!), enabled: Boolean(projectId), retry: false });
  const download = useMutation({ mutationFn: (descriptor: ExportDescriptor) => downloadProjectExport(descriptor) });

  if (!projectId) return <main className="tool-state"><h1>Exports unavailable</h1></main>;
  return <main className="tool-shell">
    <header className="tool-header"><div><p className="eyebrow">H.2B.4 evidence exports</p><h1>Project exports</h1><p>Download portable project evidence while preserving draft and verification labels.</p></div><nav><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to="/">Platform home</Link></nav></header>
    {manifest.isError && <p className="tool-error">Export options could not be loaded or your account lacks export access.</p>}
    {manifest.data && <>
      <p className="tool-warning">{manifest.data.disclaimer}</p>
      <section className="export-list" aria-label="Available project exports">
        {manifest.data.items.map((item) => <article className="export-row" key={item.code}>
          <div className="export-row-main">
            <span className="export-row-kicker">Evidence export</span>
            <h2>{item.label}</h2>
            <p>{item.description}</p>
          </div>
          <div className="export-row-action">
            <small title={item.media_type}>{exportFormat(item.code, item.media_type)}</small>
            <button type="button" disabled={download.isPending} onClick={() => download.mutate(item)}>Download</button>
          </div>
        </article>)}
      </section>
    </>}
    {download.isError && <p className="tool-error">The export could not be generated.</p>}
  </main>;
}

import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { searchProject } from "../api/platform";

export function SearchPage() {
  const { projectId } = useParams();
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const results = useQuery({
    queryKey: ["project-search", projectId, query],
    queryFn: () => searchProject(projectId!, query),
    enabled: Boolean(projectId && query.length >= 2),
    retry: false,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const normalized = input.trim();
    if (normalized.length >= 2) setQuery(normalized);
  };

  if (!projectId) return <main className="tool-state"><h1>Search unavailable</h1></main>;
  return <main className="tool-shell">
    <header className="tool-header"><div><p className="eyebrow">H.2B.4 project search</p><h1>Search project evidence</h1><p>Find documents, parcel identifiers, and extracted field evidence without converting matches into legal conclusions.</p></div><nav><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to="/">Platform home</Link></nav></header>
    <form className="search-form" onSubmit={submit}><label>Search<input aria-label="Search project" value={input} onChange={(event) => setInput(event.target.value)} placeholder="Survey number, parcel ID, filename, or extracted value" /></label><button type="submit" disabled={input.trim().length < 2}>Search</button></form>
    {!query && <section className="search-guidance" aria-label="Search guidance"><div><strong>Documents</strong><span>Filename or extracted land-record value</span></div><div><strong>Parcels</strong><span>Parcel identifier or survey reference</span></div><div><strong>Evidence</strong><span>Searches stay linked to their source record</span></div></section>}
    {results.isLoading && <p>Searching…</p>}
    {results.isError && <p className="tool-error">Search could not be completed.</p>}
    {results.data && <section className="search-results" aria-label="Search results">
      <div className="search-results-summary"><span>{results.data.total} result{results.data.total === 1 ? "" : "s"}</span><strong>{results.data.query}</strong></div>
      <div className="search-result-list">
        {results.data.items.map((item) => <article className="search-result-row" key={`${item.kind}-${item.id}-${item.evidence_id ?? ""}`}>
          <div className="search-result-main">
            <div className="search-result-meta"><span className="result-kind">{item.kind}</span>{item.preliminary && <span className="result-preliminary">PRELIMINARY / UNVERIFIED</span>}</div>
            <h2>{item.title}</h2>
            {item.subtitle && <p>{item.subtitle}</p>}
          </div>
          <div className="search-result-side">
            <small>{item.status ?? "No workflow status"}</small>
            <Link to={item.kind === "PARCEL" ? `/projects/${projectId}/gis` : `/projects/${projectId}/documents`} aria-label="Inspect source evidence">→</Link>
          </div>
        </article>)}
        {results.data.items.length === 0 && <div className="search-empty"><h2>No matching evidence</h2><p>Try a filename, survey number, parcel identifier, or extracted value.</p></div>}
      </div>
    </section>}
  </main>;
}

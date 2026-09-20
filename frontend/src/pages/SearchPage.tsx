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
    {results.isLoading && <p>Searching…</p>}
    {results.isError && <p className="tool-error">Search could not be completed.</p>}
    {results.data && <section className="search-results" aria-label="Search results"><p>{results.data.total} result{results.data.total === 1 ? "" : "s"} for <strong>{results.data.query}</strong></p>{results.data.items.map((item) => <article key={`${item.kind}-${item.id}-${item.evidence_id ?? ""}`}><div><span className="result-kind">{item.kind}</span>{item.preliminary && <span className="result-preliminary">PRELIMINARY / UNVERIFIED</span>}</div><h2>{item.title}</h2>{item.subtitle && <p>{item.subtitle}</p>}<small>{item.status ?? "No workflow status"}</small><Link to={item.kind === "PARCEL" ? `/projects/${projectId}/gis` : `/projects/${projectId}/documents`}>Inspect source evidence</Link></article>)}{results.data.items.length === 0 && <p>No matching project evidence was found.</p>}</section>}
  </main>;
}

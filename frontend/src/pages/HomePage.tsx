import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { login, logout, loadProjects, hasSession } from "../api/auth";
import { loadCurrentUser } from "../api/gis";

export function HomePage() {
  const client = useQueryClient();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [sessionVersion, setSessionVersion] = useState(0);
  const session = hasSession();

  const currentUser = useQuery({
    queryKey: ["current-user", sessionVersion],
    queryFn: loadCurrentUser,
    enabled: session,
    retry: false,
  });
  const projects = useQuery({
    queryKey: ["projects", sessionVersion],
    queryFn: loadProjects,
    enabled: Boolean(session && currentUser.data),
    retry: false,
  });
  const signIn = useMutation({
    mutationFn: () => login(identifier.trim(), password),
    onSuccess: async () => {
      setPassword("");
      setSessionVersion((value) => value + 1);
      await client.invalidateQueries();
    },
  });
  const signOut = useMutation({
    mutationFn: logout,
    onSettled: async () => {
      client.clear();
      setSessionVersion((value) => value + 1);
    },
  });

  const staleSession = session && currentUser.isError;
  const loggedIn = Boolean(session && currentUser.data);

  return <main className="home-shell">
    <header className="home-hero">
      <div>
        <p className="eyebrow">Tamil Nadu demo · SIH12 + SIH18</p>
        <h1>Intelligent Land Record Platform</h1>
        <p>One auditable workflow for digitized land records, human verification, cadastral GIS, and evidence-backed record ↔ parcel association.</p>
      </div>
      <aside className="home-safety-note"><strong>Demo boundary</strong><span>AI outputs are preliminary. Parcel associations are workflow evidence, not statutory ownership proof.</span></aside>
    </header>

    {!loggedIn && <section className="login-card" aria-label="Sign in">
      <div><p className="eyebrow">Secure project access</p><h2>Sign in</h2><p>Use an authorized application login ID such as the generated TN demo account.</p></div>
      <form onSubmit={(event) => { event.preventDefault(); if (identifier.trim() && password) signIn.mutate(); }}>
        <label>Login ID or email<input aria-label="Login ID or email" autoComplete="username" value={identifier} onChange={(event) => setIdentifier(event.target.value)} /></label>
        <label>Password<input aria-label="Password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <button type="submit" disabled={!identifier.trim() || !password || signIn.isPending}>{signIn.isPending ? "Signing in…" : "Sign in"}</button>
        {(signIn.isError || staleSession) && <p className="error-copy" role="alert">{staleSession ? "Your saved session is no longer valid. Sign in again." : "Sign in failed. Check the login ID and password."}</p>}
      </form>
      {staleSession && <button className="text-action" type="button" onClick={() => signOut.mutate()}>Clear expired session</button>}
    </section>}

    {loggedIn && <>
      <section className="signed-in-bar">
        <div><span>Signed in as</span><strong>{currentUser.data!.full_name}</strong><small>{currentUser.data!.login_id} · {currentUser.data!.roles.join(", ")}</small></div>
        <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>{signOut.isPending ? "Signing out…" : "Sign out"}</button>
      </section>

      <section className="project-launcher">
        <div className="project-launcher-heading"><div><p className="eyebrow">Available projects</p><h2>Choose a project workspace</h2></div><span>{projects.data?.items.length ?? 0} active</span></div>
        {projects.isLoading && <p>Loading your projects…</p>}
        {projects.isError && <p className="error-copy">Projects could not be loaded for this account.</p>}
        {projects.data?.items.length === 0 && <p>No active project membership is available for this account.</p>}
        <div className="project-card-grid">{projects.data?.items.map((project) => {
          const membership = currentUser.data!.project_memberships.find((item) => item.project_id === project.id);
          return <article className="project-launch-card" key={project.id}>
            <div><span className="project-state">{project.state}</span><span>{membership?.role ?? "MEMBER"}</span></div>
            <h3>{project.name}</h3>
            <p>{project.description || "Integrated land-record project"}</p>
            <Link to={`/projects/${project.id}`}>Open operational dashboard</Link>
          </article>;
        })}</div>
      </section>

      <section className="home-demo-path">
        <div><p className="eyebrow">Suggested judging path</p><h2>Show the vertical slice in four moves</h2></div>
        <ol>
          <li><strong>Document AI</strong><span>Open OCR, structured fields, confidence, and validation evidence.</span></li>
          <li><strong>Human review</strong><span>Show low-confidence or conflicting evidence entering the review queue.</span></li>
          <li><strong>Web-GIS</strong><span>Inspect draft parcels separately from buildings, roads, and land-use layers.</span></li>
          <li><strong>Record ↔ Parcel</strong><span>Show the auditable association and its provenance without claiming statutory ownership.</span></li>
        </ol>
      </section>
    </>}
  </main>;
}

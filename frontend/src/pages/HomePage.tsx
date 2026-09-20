import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { login, logout, loadProjects, hasSession } from "../api/auth";
import { createProject } from "../api/admin";
import { loadCurrentUser } from "../api/gis";

export function HomePage() {
  const client = useQueryClient();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [sessionVersion, setSessionVersion] = useState(0);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const session = hasSession();

  useEffect(() => {
    const onSessionExpired = () => {
      client.clear();
      setSessionVersion((value) => value + 1);
    };
    window.addEventListener("session-expired", onSessionExpired);
    return () => window.removeEventListener("session-expired", onSessionExpired);
  }, [client]);

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
  const create = useMutation({
    mutationFn: () => createProject({ name: projectName.trim(), description: projectDescription.trim() || null }),
    onSuccess: async () => {
      setProjectName("");
      setProjectDescription("");
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["current-user"] });
    },
  });

  const staleSession = session && currentUser.isError;
  const loggedIn = Boolean(session && currentUser.data);
  const canCreateProject = currentUser.data?.permissions.includes("project:create") ?? false;

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

      {canCreateProject && <section className="project-create-card" aria-label="Create project">
        <div><p className="eyebrow">H.2B.4 project setup</p><h2>Create a project</h2><p>Creates an isolated ACTIVE project and adds you as its owner/member using your current application role.</p></div>
        <form onSubmit={(event) => { event.preventDefault(); if (projectName.trim()) create.mutate(); }}>
          <label>Project name<input aria-label="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={255} /></label>
          <label>Description<textarea aria-label="Project description" value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} maxLength={10000} /></label>
          <button type="submit" disabled={!projectName.trim() || create.isPending}>{create.isPending ? "Creating…" : "Create project"}</button>
          {create.isError && <p className="error-copy" role="alert">The project could not be created.</p>}
        </form>
      </section>}

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

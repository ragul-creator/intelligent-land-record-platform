import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { login, logout, loadProjects, hasSession } from "../api/auth";
import { createProject } from "../api/admin";
import { loadCurrentUser } from "../api/gis";

const demoIdentifier = "viewer.demo.tn@example.invalid";
const demoPassword = "BhumiDemo2026Pass";

export function HomePage() {
  const client = useQueryClient();
  const [identifier, setIdentifier] = useState(demoIdentifier);
  const [password, setPassword] = useState(demoPassword);
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
        <h1>Bhumi-AI</h1>
        <p>Professional land-record intelligence with two focused capabilities: document OCR and evidence extraction, plus Web-GIS and GeoAI analysis.</p>
      </div>

    </header>

    {!loggedIn && <section className="login-card login-experience" aria-label="Sign in">
      <aside className="login-visual" aria-label="Bhumi-AI capabilities">
        <div className="login-visual-copy">
          <p className="eyebrow">Unified land intelligence</p>
          <h2>Evidence first.<br />Decisions with context.</h2>
          <p>One workspace for digitized land records, geospatial evidence, and human review.</p>
        </div>

        <div className="login-map-motif" aria-hidden="true">
          <svg viewBox="0 0 520 250" role="presentation">
            <path className="login-boundary" d="M26 48 132 28 220 55 304 30 486 66 470 210 348 224 250 194 138 226 42 190Z" />
            <path className="login-parcel" d="M132 28 122 114 138 226M220 55 210 122 250 194M304 30 298 116 348 224M42 132 122 114 210 122 298 116 480 138" />
            <path className="login-road" d="M14 176c84-34 155-20 222-5 91 20 166 18 273-19" />
            <rect className="login-building" x="69" y="74" width="39" height="27" rx="3" />
            <rect className="login-building" x="254" y="72" width="52" height="33" rx="3" />
            <rect className="login-building" x="384" y="152" width="42" height="29" rx="3" />
            <circle className="login-node" cx="122" cy="114" r="4" />
            <circle className="login-node" cx="298" cy="116" r="4" />
          </svg>
          <span className="login-map-tag login-map-tag-a">Document evidence</span>
          <span className="login-map-tag login-map-tag-b">GeoAI layers</span>
          <span className="login-map-tag login-map-tag-c">Human review</span>
        </div>

        <div className="login-capabilities">
          <span>OCR & extraction</span>
          <span>WebGIS & GeoAI</span>
          <span>Audit-ready review</span>
        </div>
      </aside>

      <div className="login-form-panel">
        <div className="login-form-heading">
          <p className="eyebrow">Secure project access</p>
          <h2>Sign in</h2>
          <p>Continue to your authorized Bhumi-AI workspace.</p>
        </div>
        <form onSubmit={(event) => { event.preventDefault(); if (identifier.trim() && password) signIn.mutate(); }}>
          <label>Login ID or email<input aria-label="Login ID or email" autoComplete="username" value={identifier} onChange={(event) => setIdentifier(event.target.value)} /></label>
          <label>Password<input aria-label="Password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <button type="submit" disabled={!identifier.trim() || !password || signIn.isPending}>{signIn.isPending ? "Signing in…" : "Sign in"}</button>
          {(signIn.isError || staleSession) && <p className="error-copy" role="alert">{staleSession ? "Your saved session is no longer valid. Sign in again." : "Sign in failed. Check the login ID and password."}</p>}
        </form>
        <div className="login-trust-row" aria-label="Security information">
          <span>Role-based access</span>
          <span>Project isolation</span>
          <span>Audit trail</span>
        </div>
        {staleSession && <button className="text-action" type="button" onClick={() => signOut.mutate()}>Clear expired session</button>}
      </div>
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
        <div><p className="eyebrow">Suggested judging path</p><h2>Two clear demo workflows</h2><p>Keep the live walkthrough focused and easy to follow.</p></div>
        <ol>
          <li><strong>Document AI / OCR</strong><span>Upload the prepared land deed and show OCR evidence, extracted fields, confidence, and provenance.</span></li>
          <li><strong>Web-GIS / GeoAI</strong><span>Open the prepared imagery and show detected buildings, roads, parcel candidates, and visible-boundary evidence.</span></li>
        </ol>
      </section>
    </>}
  </main>;
}

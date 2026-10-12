import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

type NavItem = { label: string; path: string; note: string };

function projectIdFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/projects\/([^/]+)/);
  return match?.[1] ?? null;
}

export function WorkspaceNav() {
  const location = useLocation();
  const projectId = projectIdFromPath(location.pathname);
  const [toolsOpen, setToolsOpen] = useState(false);

  useEffect(() => setToolsOpen(false), [location.pathname]);

  useEffect(() => {
    if (!toolsOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setToolsOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toolsOpen]);

  const base = projectId ? `/projects/${projectId}` : "";
  const primary: NavItem[] = projectId
    ? [
        { label: "City dashboard", path: base, note: "Urban intelligence overview" },
        { label: "Planning documents", path: `${base}/documents`, note: "Document intelligence" },
        { label: "Urban GIS Explorer", path: `${base}/gis`, note: "City maps & GeoAI" },
        { label: "Review & verification", path: `${base}/review`, note: "Data quality & verification" },
      ]
    : [];

  const tools: NavItem[] = projectId
    ? [
        { label: "Spatial search", path: `${base}/search`, note: "Find documents, places and evidence" },
        { label: "Analysis centre", path: `${base}/jobs`, note: "Analysis history, failures and reruns" },
        { label: "Planning reports", path: `${base}/exports`, note: "Exports and evidence summaries" },
        { label: "Activity history", path: `${base}/audit`, note: "Trace important workspace activity" },
        { label: "Workspace settings", path: `${base}/admin`, note: "Metadata and authorized membership" },
      ]
    : [];

  return (
    <>
      <header className={projectId ? "workspace-nav workspace-nav-sidebar" : "workspace-nav workspace-nav-public"} aria-label="UrbanPlan AI navigation">
        <Link className="workspace-wordmark" to="/" aria-label="UrbanPlan AI home">
          <span className="workspace-earth" aria-hidden="true" />
          <span>UrbanPlan AI</span>
        </Link>

        <div className="workspace-dock">
          <nav className={toolsOpen ? "workspace-pill workspace-pill-open" : "workspace-pill"} aria-label="Primary navigation">
            <div className="workspace-links" aria-hidden={toolsOpen}>
              {!projectId && <NavLink to="/" end>Study areas</NavLink>}
              {primary.map((item) => (
                <NavLink key={item.path} to={item.path} end={item.path === base}>
                  {item.label}
                </NavLink>
              ))}
              {projectId && (
                <button
                  type="button"
                  className="workspace-more"
                  aria-expanded={toolsOpen}
                  aria-controls="workspace-tools-panel"
                  onClick={() => setToolsOpen(true)}
                >
                  More <span aria-hidden="true">＋</span>
                </button>
              )}
            </div>

            {projectId && (
              <section
                id="workspace-tools-panel"
                className="workspace-expand-panel"
                aria-hidden={!toolsOpen}
              >
                <div className="workspace-panel-head">
                  <span>Workspace tools</span>
                  <button type="button" onClick={() => setToolsOpen(false)} aria-label="Close project tools">×</button>
                </div>
                <p className="workspace-panel-kicker">Move between urban analysis tools without leaving this workspace.</p>
                <div className="workspace-panel-rows">
                  {tools.map((item) => (
                    <NavLink key={item.path} to={item.path}>
                      <span className="workspace-panel-copy">
                        <strong>{item.label}</strong>
                        <small>{item.note}</small>
                      </span>
                      <span className="workspace-arrow" aria-hidden="true">→</span>
                    </NavLink>
                  ))}
                </div>
                <div className="workspace-panel-chips" aria-label="Project shortcuts">
                  <NavLink to={base} end>City dashboard</NavLink>
                  <Link to="/">All workspaces</Link>
                </div>
              </section>
            )}
          </nav>
        </div>

        <div className="workspace-nav-end">
          {projectId ? <Link to="/">Workspaces</Link> : <span className="workspace-mode">Urban intelligence</span>}
        </div>
      </header>
      {toolsOpen && <button className="workspace-scrim" type="button" aria-label="Close navigation menu" onClick={() => setToolsOpen(false)} />}
    </>
  );
}

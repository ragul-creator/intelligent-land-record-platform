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
        { label: "Dashboard", path: base, note: "Project overview" },
        { label: "Documents", path: `${base}/documents`, note: "OCR & records" },
        { label: "WebGIS", path: `${base}/gis`, note: "Maps & GeoAI" },
        { label: "Review", path: `${base}/review`, note: "Human verification" },
      ]
    : [];

  const tools: NavItem[] = projectId
    ? [
        { label: "Search", path: `${base}/search`, note: "Find documents, parcels and evidence" },
        { label: "Processing jobs", path: `${base}/jobs`, note: "Live work, failures and safe retries" },
        { label: "Exports", path: `${base}/exports`, note: "Portable evidence with verification labels" },
        { label: "Audit trail", path: `${base}/audit`, note: "Trace important workflow activity" },
        { label: "Project admin", path: `${base}/admin`, note: "Metadata and authorized membership" },
      ]
    : [];

  return (
    <>
      <header className="workspace-nav" aria-label="Bhumi-AI navigation">
        <Link className="workspace-wordmark" to="/" aria-label="Bhumi-AI home">
          <span className="workspace-earth" aria-hidden="true" />
          <span>Bhumi-AI</span>
        </Link>

        <div className="workspace-dock">
          <nav className={toolsOpen ? "workspace-pill workspace-pill-open" : "workspace-pill"} aria-label="Primary navigation">
            <div className="workspace-links" aria-hidden={toolsOpen}>
              {!projectId && <NavLink to="/" end>Projects</NavLink>}
              {primary.map((item) => (
                <NavLink key={item.path} to={item.path} end={item.label === "Dashboard"}>
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
                  <span>Project tools</span>
                  <button type="button" onClick={() => setToolsOpen(false)} aria-label="Close project tools">×</button>
                </div>
                <p className="workspace-panel-kicker">Move between operational views without leaving this project.</p>
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
                  <NavLink to={base} end>Dashboard</NavLink>
                  <Link to="/">All projects</Link>
                </div>
              </section>
            )}
          </nav>
        </div>

        <div className="workspace-nav-end">
          {projectId ? <Link to="/">Projects</Link> : <span className="workspace-mode">Land intelligence</span>}
        </div>
      </header>
      {toolsOpen && <button className="workspace-scrim" type="button" aria-label="Close navigation menu" onClick={() => setToolsOpen(false)} />}
    </>
  );
}

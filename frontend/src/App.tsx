import { Route, Routes } from "react-router-dom";
import { GisPage } from "./pages/GisPage";

export function App() {
  return <Routes><Route path="/projects/:projectId/gis" element={<GisPage />} /><Route path="*" element={<main className="app-shell"><p className="eyebrow">Platform</p><h1>Intelligent Land Record Platform</h1><p>Open a project GIS route to view authorized preliminary cadastral layers.</p></main>} /></Routes>;
}

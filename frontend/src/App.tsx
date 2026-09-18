import { Route, Routes } from "react-router-dom";
import { GisPage } from "./pages/GisPage";
import { ReviewPage } from "./pages/ReviewPage";
import { DocumentsPage } from "./pages/DocumentsPage";

export function App() {
  return (
    <Routes>
      <Route path="/projects/:projectId/gis" element={<GisPage />} />
      <Route path="/projects/:projectId/review" element={<ReviewPage />} />
      <Route path="/projects/:projectId/documents" element={<DocumentsPage />} />
      <Route path="*" element={<main className="app-shell"><p className="eyebrow">Platform</p><h1>Intelligent Land Record Platform</h1><p>Open a project GIS or review route to work with authorized preliminary land-record data.</p></main>} />
    </Routes>
  );
}

import { Route, Routes } from "react-router-dom";
import { GisPage } from "./pages/GisPage";
import { ReviewPage } from "./pages/ReviewPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { HomePage } from "./pages/HomePage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/projects/:projectId" element={<DashboardPage />} />
      <Route path="/projects/:projectId/gis" element={<GisPage />} />
      <Route path="/projects/:projectId/review" element={<ReviewPage />} />
      <Route path="/projects/:projectId/documents" element={<DocumentsPage />} />
      <Route path="*" element={<HomePage />} />
    </Routes>
  );
}

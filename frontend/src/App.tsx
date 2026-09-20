import { Route, Routes } from "react-router-dom";
import { GisPage } from "./pages/GisPage";
import { ReviewPage } from "./pages/ReviewPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { HomePage } from "./pages/HomePage";
import { AdminPage } from "./pages/AdminPage";
import { AuditPage } from "./pages/AuditPage";
import { JobsPage } from "./pages/JobsPage";
import { SearchPage } from "./pages/SearchPage";
import { ExportsPage } from "./pages/ExportsPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/projects/:projectId" element={<DashboardPage />} />
      <Route path="/projects/:projectId/gis" element={<GisPage />} />
      <Route path="/projects/:projectId/review" element={<ReviewPage />} />
      <Route path="/projects/:projectId/documents" element={<DocumentsPage />} />
      <Route path="/projects/:projectId/admin" element={<AdminPage />} />
      <Route path="/projects/:projectId/audit" element={<AuditPage />} />
      <Route path="/projects/:projectId/jobs" element={<JobsPage />} />
      <Route path="/projects/:projectId/search" element={<SearchPage />} />
      <Route path="/projects/:projectId/exports" element={<ExportsPage />} />
      <Route path="*" element={<HomePage />} />
    </Routes>
  );
}

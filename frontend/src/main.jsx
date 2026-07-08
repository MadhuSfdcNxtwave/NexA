import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import ProjectsPage from "./pages/ProjectsPage.jsx";
import ProjectsListPage from "./pages/ProjectsListPage.jsx";
import ThreadsPage from "./pages/ThreadsPage.jsx";
import ThreadPage from "./pages/ThreadPage.jsx";
import CollectionsPage from "./pages/CollectionsPage.jsx";
import ProjectPage from "./pages/ProjectPage.jsx";
import DataBrowserPage from "./pages/DataBrowserPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import SharedDashboardPage from "./pages/SharedDashboardPage.jsx";
import NotebookPage from "./pages/NotebookPage.jsx";
import DataRedirect from "./pages/DataRedirect.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import AdminPage from "./pages/AdminPage.jsx";
import OrgSchemaPage from "./pages/OrgSchemaPage.jsx";
import ProtectedRoute from "./components/ProtectedRoute.jsx";
import "./styles.css";
import "./theme.css";

function Protected({ children }) {
  return <ProtectedRoute>{children}</ProtectedRoute>;
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/shared/:token" element={<SharedDashboardPage />} />
        <Route path="/" element={<Protected><ProjectsPage /></Protected>} />
        <Route path="/projects" element={<Protected><ProjectsListPage /></Protected>} />
        <Route path="/threads" element={<Protected><ThreadsPage /></Protected>} />
        <Route path="/threads/:threadId" element={<Protected><ThreadPage /></Protected>} />
        <Route path="/collections" element={<Protected><CollectionsPage /></Protected>} />
        <Route path="/admin" element={<Protected><AdminPage /></Protected>} />
        <Route path="/org-schema" element={<Protected><OrgSchemaPage /></Protected>} />
        <Route path="/data" element={<Protected><DataBrowserPage /></Protected>} />
        <Route path="/projects/:id/notebook" element={<Protected><NotebookPage /></Protected>} />
        <Route path="/projects/:id" element={<Protected><ProjectPage /></Protected>} />
        <Route path="/projects/:id/data" element={<Protected><DataBrowserPage /></Protected>} />
        <Route path="/projects/:id/tables" element={<Protected><DataBrowserPage /></Protected>} />
        <Route path="/projects/:id/dashboard" element={<Protected><DashboardPage /></Protected>} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);

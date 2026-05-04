import { NavLink, Outlet, Route, Routes } from "react-router-dom";

import { DashboardPage } from "./pages/DashboardPage";
import { LiveOptimizationPage } from "./pages/LiveOptimizationPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { UploadDcpPage } from "./pages/UploadDcpPage";

const navItems = [
  { to: "/", label: "Dashboard" },
  { to: "/upload", label: "Upload DCP" },
  { to: "/runs", label: "Runs" },
  { to: "/settings", label: "Settings" }
];

function WorkspaceLayout() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <span className="eyebrow">FPGA Timing Optimization</span>
          <h1>DCP Forge</h1>
        </div>
        <nav className="nav-row">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) => `nav-link ${isActive ? "nav-active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/optimize" element={<LiveOptimizationPage />} />
      <Route element={<WorkspaceLayout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/upload" element={<UploadDcpPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}

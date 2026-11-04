import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppHeader } from "./components/AppHeader";
import { ManagerProvider } from "./context/ManagerContext";
import { AssistantPage } from "./pages/AssistantPage";
import { CasePage } from "./pages/CasePage";
import { MetricsPage } from "./pages/MetricsPage";
import { QueuePage } from "./pages/QueuePage";

export function App() {
  return (
    <ManagerProvider>
      <BrowserRouter>
        <div className="app-frame">
          <AppHeader />
          <Routes>
            <Route path="/" element={<QueuePage />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/exceptions/:id" element={<CasePage />} />
            <Route path="/metrics" element={<MetricsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <footer className="app-footer"><span>ShiftGuard · Evidence-led timecard review</span><span>Suggestions always require a manager decision</span></footer>
        </div>
      </BrowserRouter>
    </ManagerProvider>
  );
}

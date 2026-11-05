import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { useAuth, AuthProvider } from "./auth/AuthContext";
import { AppHeader } from "./components/AppHeader";
import { ManagerProvider } from "./context/ManagerContext";
import { useCurrentUser } from "./api/hooks";

import { BRAND } from "./brand";
// Route-level lazy loading — each page is a separate chunk.
// This keeps the initial bundle small: only the shell, header and auth
// logic load on first paint; page code is fetched on demand.
const LoginPage = lazy(() =>
  import("./pages/LoginPage").then((m) => ({ default: m.LoginPage }))
);
const QueuePage = lazy(() =>
  import("./pages/QueuePage").then((m) => ({ default: m.QueuePage }))
);
const AssistantPage = lazy(() =>
  import("./pages/AssistantPage").then((m) => ({ default: m.AssistantPage }))
);
const CasePage = lazy(() =>
  import("./pages/CasePage").then((m) => ({ default: m.CasePage }))
);
const MetricsPage = lazy(() =>
  import("./pages/MetricsPage").then((m) => ({ default: m.MetricsPage }))
);
const PoliciesPage = lazy(() =>
  import("./pages/PoliciesPage").then((m) => ({ default: m.PoliciesPage }))
);

/** Minimal inline spinner shown while a lazy chunk is loading. */
function PageLoader() {
  return (
    <main className="auth-shell">
      <p role="status">Loading…</p>
    </main>
  );
}

export function App() {
  return (
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  );
}

function AppGate() {
  const auth = useAuth();
  if (!auth.enabled) return <Workspace />;
  if (!auth.configured)
    return (
      <Suspense fallback={<PageLoader />}>
        <LoginPage />
      </Suspense>
    );
  if (auth.loading)
    return (
      <main className="auth-shell">
        <p role="status">Checking sign-in…</p>
      </main>
    );
  if (!auth.session)
    return (
      <Suspense fallback={<PageLoader />}>
        <LoginPage />
      </Suspense>
    );
  return <ProfileGate />;
}

function ProfileGate() {
  const auth = useAuth();
  const currentUser = useCurrentUser(true);
  if (currentUser.isLoading)
    return (
      <main className="auth-shell">
        <p role="status">Loading your access profile…</p>
      </main>
    );
  if (currentUser.isError || !currentUser.data) {
    return (
      <main className="auth-shell">
        <section className="auth-card" role="alert">
          <p className="eyebrow">Access profile</p>
          <h1>We couldn't load your {BRAND.name} access</h1>
          <p className="auth-description">
            Ask an administrator to assign your organization and role, then sign
            in again.
          </p>
          <button
            className="button button-outline"
            type="button"
            onClick={() => void auth.signOut()}
          >
            Sign out
          </button>
        </section>
      </main>
    );
  }
  if (currentUser.data.role !== "HR") {
    return (
      <main className="auth-shell">
        <section className="auth-card" role="alert">
          <p className="eyebrow">HR review access</p>
          <h1>An HR account is required</h1>
          <p className="auth-description">
            This workspace is configured for HR case review. Ask an
            administrator to assign your HR role if you should have access.
          </p>
          <button
            className="button button-outline"
            type="button"
            onClick={() => void auth.signOut()}
          >
            Sign out
          </button>
        </section>
      </main>
    );
  }
  return <Workspace />;
}

function Workspace() {
  const auth = useAuth();
  return (
    <ManagerProvider>
      <BrowserRouter>
        <div className="app-frame">
          <AppHeader />
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/" element={<QueuePage />} />
              <Route path="/assistant" element={<AssistantPage />} />
              {auth.enabled ? (
                <Route path="/policies" element={<PoliciesPage />} />
              ) : null}
              <Route path="/exceptions/:id" element={<CasePage />} />
              {!auth.enabled ? (
                <Route path="/metrics" element={<MetricsPage />} />
              ) : null}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
          <footer className="app-footer">
            <span>{BRAND.name} · Evidence-led attendance review</span>
            <span>
              Suggestions always require an{" "}
              {auth.enabled ? "HR" : "manager"} decision
            </span>
          </footer>
        </div>
      </BrowserRouter>
    </ManagerProvider>
  );
}

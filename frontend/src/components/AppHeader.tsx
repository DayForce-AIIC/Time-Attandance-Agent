import { NavLink } from "react-router-dom";
import { useCurrentUser, useDetect, useHealth, useLlmMode, useResetDemo, useSetLlmMode } from "../api/hooks";
import type { FaultMode } from "../api/schemas";
import { useAuth } from "../auth/AuthContext";
import { ManagerSwitcher } from "./ManagerSwitcher";
import { ErrorBanner } from "./common";

const FAULT_MODES: { value: FaultMode; label: string }[] = [
  { value: "none", label: "No fault" },
  { value: "bad_then_good", label: "Bad, then recover" },
  { value: "always_bad", label: "Always invalid" },
  { value: "garbage", label: "Malformed output" },
  { value: "timeout", label: "Timeout" },
  { value: "bad_citation", label: "Bad citation" },
  { value: "naive_latest", label: "Naive latest time" },
];

export function AppHeader() {
  const auth = useAuth();
  const currentUser = useCurrentUser(auth.enabled);
  const health = useHealth();
  const llmMode = useLlmMode(!auth.enabled);
  return (
    <header className="app-header">
      <div className="header-main">
        <NavLink to="/" className="brand" aria-label="ShiftGuard home">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span><strong>ShiftGuard</strong><small>Manager workspace</small></span>
        </NavLink>
        <nav aria-label="Primary navigation" className="primary-nav">
          <NavLink to="/" end>Queue</NavLink>
          <NavLink to="/assistant">Ask ShiftGuard</NavLink>
          {!auth.enabled || currentUser.data?.role === "HR" ? <NavLink to="/metrics">Metrics</NavLink> : null}
        </nav>
        <div className="header-tools">
          <div className={`health-pill ${health.data?.llm_reachable ? "is-reachable" : "is-unreachable"}`} title={health.error ? "API health could not be loaded" : undefined}>
            <span className="health-dot" aria-hidden="true" />
            <span>{health.data ? `${health.data.provider} / ${health.data.model}` : "API status loading"}</span>
            <span className="health-state">{health.data?.llm_reachable ? "reachable" : health.data ? "unreachable" : "checking"}</span>
          </div>
          {auth.enabled ? (
            <div className="authenticated-user">
              <span>{currentUser.data?.email ?? "Signed in"}<small>{currentUser.data?.role ?? ""}</small></span>
              <button type="button" className="button button-quiet" onClick={() => void auth.signOut()}>Sign out</button>
            </div>
          ) : <ManagerSwitcher />}
        </div>
      </div>
      {!auth.enabled && llmMode.data ? <DemoControls initialMode={llmMode.data.mode} /> : null}
    </header>
  );
}

function DemoControls({ initialMode }: { initialMode: FaultMode }) {
  const mode = useLlmMode();
  const setMode = useSetLlmMode();
  const detect = useDetect();
  const reset = useResetDemo();
  const error = setMode.error ?? detect.error ?? reset.error;
  const busy = setMode.isPending || detect.isPending || reset.isPending;

  const resetDemo = () => {
    if (window.confirm("Reset all demo decisions and restore the seeded timecards?")) reset.mutate();
  };

  return (
    <div className="demo-controls">
      <div className="demo-controls-label"><span className="demo-kicker">LIVE DEMO</span><span>Fault simulation</span></div>
      <label className="visually-hidden" htmlFor="fault-mode">LLM fault mode</label>
      <select
        id="fault-mode"
        aria-label="LLM fault mode"
        value={mode.data?.mode ?? initialMode}
        onChange={(event) => setMode.mutate(event.target.value as FaultMode)}
        disabled={busy}
      >
        {FAULT_MODES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
      </select>
      <button type="button" className="button button-quiet" onClick={() => detect.mutate()} disabled={busy}>
        {detect.isPending ? "Checking…" : "↻ Re-run detection"}
      </button>
      <button type="button" className="button button-quiet" onClick={resetDemo} disabled={busy}>
        {reset.isPending ? "Resetting…" : "Reset demo data"}
      </button>
      {error ? <ErrorBanner error={error} onRetry={() => { setMode.reset(); detect.reset(); reset.reset(); }} /> : null}
      {detect.isSuccess ? <span className="sr-only" role="status">Detection complete: {detect.data.created} new exceptions.</span> : null}
    </div>
  );
}

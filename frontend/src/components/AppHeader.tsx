import { NavLink } from "react-router-dom";
import { useCurrentUser, useDetect, useHealth, useLlmMode, useResetDemo, useSetLlmMode } from "../api/hooks";
import type { FaultMode } from "../api/schemas";
import { useAuth } from "../auth/AuthContext";
import { BRAND } from "../brand";
import { BrandMark } from "./BrandMark";
import { HealthStatus, type HealthState } from "./HealthStatus";
import { ManagerSwitcher } from "./ManagerSwitcher";
import { UserMenu } from "./UserMenu";
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
  const email = currentUser.data?.email ?? "Signed in";
  const healthState: HealthState = health.data
    ? (health.data.llm_reachable ? "reachable" : "unreachable")
    : health.error ? "unreachable" : "checking";
  return (
    <header className="app-header">
      <div className="header-main">
        <NavLink to="/" className="brand" aria-label={`${BRAND.name} home`}>
          <BrandMark />
          <span><strong>{BRAND.name}</strong><small>{auth.enabled ? "HR review workspace" : "Manager workspace"}</small></span>
        </NavLink>
        <nav aria-label="Primary navigation" className="primary-nav">
          <NavLink to="/" end>Queue</NavLink>
          <NavLink to="/assistant">Ask {BRAND.name}</NavLink>
          {auth.enabled ? <NavLink to="/policies">Policies</NavLink> : null}
          {!auth.enabled ? <NavLink to="/metrics">Metrics</NavLink> : null}
        </nav>
        <div className="header-tools">
          <HealthStatus
            provider={health.data?.provider}
            model={health.data?.model}
            state={healthState}
            title={health.error ? "API health could not be loaded" : undefined}
          />
          {auth.enabled ? (
            <UserMenu email={email} role={currentUser.data?.role ?? ""} onSignOut={() => void auth.signOut()} />
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
    <div className="demo-bar">
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
    </div>
  );
}

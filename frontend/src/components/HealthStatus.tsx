export type HealthState = "reachable" | "unreachable" | "checking";

/** Compact status pill in the header: is the configured LLM provider and model reachable? */
export function HealthStatus({
  provider,
  model,
  state,
  title,
}: {
  provider?: string;
  model?: string;
  state: HealthState;
  title?: string;
}) {
  const label = provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : "LLM";
  const showModel = state === "reachable" && Boolean(model) && model?.toLowerCase() !== provider?.toLowerCase();
  const tooltip = title ?? (model ? `${label} model ${model} is ${state}` : undefined);
  return (
    <div className={`health-pill is-${state}`} role="status" title={tooltip}>
      <span className="health-dot" aria-hidden="true" />
      <span className="health-provider">{state === "checking" ? "Checking LLM…" : label}</span>
      {showModel ? <span className="health-model">{model}</span> : null}
      {state === "reachable" ? <span className="visually-hidden">reachable</span> : null}
      {state === "unreachable" ? <span className="health-flag">unreachable</span> : null}
    </div>
  );
}

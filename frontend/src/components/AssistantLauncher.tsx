import { Link } from "react-router-dom";
import { useHealth } from "../api/hooks";
import { BRAND } from "../brand";
import { IconBot } from "./icons";

/** Floating shortcut to the assistant, shown on the queue so questions are one click away. */
export function AssistantLauncher() {
  const health = useHealth();
  const ready = Boolean(health.data && health.data.provider === "ollama" && health.data.llm_reachable);
  return (
    <Link
      to="/assistant"
      className={`assistant-launcher${ready ? " is-ready" : ""}`}
      aria-label={`Ask ${BRAND.name}`}
      title={ready ? `Ask ${BRAND.name}` : `Ask ${BRAND.name} (assistant is not available right now)`}
    >
      <span className="assistant-launcher-icon" aria-hidden="true">
        <IconBot />
        <i className="assistant-launcher-dot" />
      </span>
      <span className="assistant-launcher-text" aria-hidden="true">Ask {BRAND.name}</span>
    </Link>
  );
}

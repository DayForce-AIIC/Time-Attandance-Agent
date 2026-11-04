import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAssistantQuery, useHealth } from "../api/hooks";
import type { z } from "zod";
import { assistantAnswerSchema } from "../api/schemas";
import { ErrorBanner, PageHeading } from "../components/common";

type AssistantAnswer = z.infer<typeof assistantAnswerSchema>;
type Turn = { question: string; result: AssistantAnswer };

const QUICK_QUESTIONS = [
  "Which employees have open overtime risks?",
  "What policy applies to missed punches?",
  "Show me the open cases for Priya Raman.",
];

export function AssistantPage() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const assistant = useAssistantQuery();
  const health = useHealth();
  const assistantReady = Boolean(
    health.data && health.data.provider === "ollama" && health.data.llm_reachable,
  );
  const availabilityMessage = !health.data
    ? health.error
      ? "Could not confirm Ollama availability. Ask ShiftGuard is disabled until the backend health check succeeds."
      : "Checking Ollama availability…"
    : health.data.provider !== "ollama"
      ? "Ask ShiftGuard requires Ollama and is disabled while the backend is in mock mode. Set LLM_PROVIDER=ollama and restart the backend."
      : !health.data.llm_reachable
        ? `Ollama or the configured model (${health.data.model}) is unavailable. Start Ollama and make sure the model is installed.`
        : null;

  const ask = (value: string) => {
    const cleaned = value.trim();
    if (!cleaned || assistant.isPending || !assistantReady) return;
    assistant.mutate(cleaned, {
      onSuccess: (result) => {
        setTurns((previous) => [...previous, { question: cleaned, result }]);
        setQuestion("");
      },
    });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    ask(question);
  };

  return (
    <main className="page-shell assistant-page">
      <PageHeading eyebrow="Read-only attendance assistant" title="Ask ShiftGuard" />
      <p className="assistant-intro">
        Ask about your team’s attendance cases or company policy. Answers include the records and policy passages used.
        The assistant cannot change timecards or approve decisions.
      </p>

      <section className="panel assistant-panel" aria-label="Attendance questions">
        {availabilityMessage ? (
          <div className="assistant-availability" role="status" aria-live="polite">
            {availabilityMessage}
          </div>
        ) : null}
        <div className="assistant-quick-questions">
          <span>Try a question</span>
          {QUICK_QUESTIONS.map((prompt) => (
            <button
              className="assistant-chip"
              disabled={assistant.isPending || !assistantReady}
              key={prompt}
              onClick={() => ask(prompt)}
              type="button"
            >
              {prompt}
            </button>
          ))}
        </div>

        <form className="assistant-form" onSubmit={submit}>
          <label htmlFor="assistant-question">Your question</label>
          <div className="assistant-input-row">
            <input
              autoComplete="off"
              disabled={!assistantReady}
              id="assistant-question"
              maxLength={500}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="For example: What policy applies to meal breaks?"
              value={question}
            />
            <button className="button button-primary" disabled={!question.trim() || assistant.isPending || !assistantReady} type="submit">
              {assistant.isPending ? "Asking Ollama…" : "Ask"}
            </button>
          </div>
          <small>Questions are limited to the selected manager’s team and retrieved attendance policies.</small>
        </form>

        {assistant.error ? (
          <ErrorBanner error={assistant.error} onRetry={() => ask(question)} />
        ) : null}

        {turns.length === 0 ? (
          <div className="assistant-empty">
            <strong>Answers stay tied to evidence.</strong>
            <span>ShiftGuard does not infer why an employee was absent or make attendance decisions.</span>
          </div>
        ) : (
          <div aria-live="polite" className="assistant-conversation">
            {turns.map((turn, index) => (
              <article className="assistant-turn" key={`${turn.question}-${index}`}>
                <div className="assistant-question-bubble">
                  <span>You asked</span>
                  <p>{turn.question}</p>
                </div>
                <div className="assistant-answer">
                  <div className="assistant-answer-heading">
                    <strong>ShiftGuard</strong>
                    <span>
                      {turn.result.used_fallback ? "Evidence-only answer" : `Source: ${turn.result.model}`}
                    </span>
                  </div>
                  <p>{turn.result.answer}</p>
                  {turn.result.sources.length ? (
                    <div className="assistant-sources">
                      <h2>Sources</h2>
                      {turn.result.sources.map((source) => (
                        <section className="assistant-source" key={source.source_id}>
                          <div className="assistant-source-heading">
                            <span className={`source-badge source-${source.kind}`}>{source.kind}</span>
                            {source.href ? <Link to={source.href}>{source.label} ↗</Link> : <strong>{source.label}</strong>}
                          </div>
                          <p>{source.excerpt}</p>
                        </section>
                      ))}
                    </div>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAssistantConversation, useAssistantConversations, useAssistantQuery, useDeleteAssistantConversation, useHealth } from "../api/hooks";
import type { z } from "zod";
import { assistantAnswerSchema, conversationMessageSchema } from "../api/schemas";
import { useAuth } from "../auth/AuthContext";
import { ErrorBanner, PageHeading } from "../components/common";

type AssistantAnswer = z.infer<typeof assistantAnswerSchema>;
type ConversationMessage = z.infer<typeof conversationMessageSchema>;
type DisplayMessage = ConversationMessage & { localId?: string };

const QUICK_QUESTIONS = [
  "Which employees have open overtime risks?",
  "What policy applies to missed punches?",
  "Show all open attendance cases.",
];

export function AssistantPage() {
  const auth = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("conversation");
  const [question, setQuestion] = useState("");
  const [demoMessages, setDemoMessages] = useState<DisplayMessage[]>([]);
  const assistant = useAssistantQuery();
  const conversations = useAssistantConversations(auth.enabled);
  const conversation = useAssistantConversation(selectedId, auth.enabled);
  const deleteConversation = useDeleteAssistantConversation();
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

  const startNewConversation = () => {
    setSearchParams({}, { replace: false });
    setDemoMessages([]);
    assistant.reset();
    setQuestion("");
  };

  const ask = (value: string) => {
    const cleaned = value.trim();
    if (!cleaned || assistant.isPending || !assistantReady) return;
    assistant.mutate({ question: cleaned, conversation_id: selectedId }, {
      onSuccess: (result) => {
        setQuestion("");
        if (result.conversation_id) {
          setSearchParams({ conversation: result.conversation_id });
        } else {
          setDemoMessages((previous) => [...previous, ...demoTurn(cleaned, result)]);
        }
      },
    });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    ask(question);
  };

  const messages: DisplayMessage[] = auth.enabled
    ? conversation.data?.messages ?? []
    : demoMessages;

  const deleteSavedConversation = (id: string, title: string | null) => {
    if (!window.confirm(`Delete “${title || "Untitled conversation"}” and its saved messages?`)) return;
    deleteConversation.mutate(id, {
      onSuccess: () => {
        if (selectedId === id) startNewConversation();
      },
    });
  };

  return (
    <main className="page-shell assistant-page">
      <PageHeading eyebrow="Read-only attendance assistant" title="Ask ShiftGuard" />
      <p className="assistant-intro">
        Ask about your organization’s attendance cases or company policy. Answers include the records and policy passages used.
        The assistant cannot change timecards or approve decisions.
      </p>

      <div className={`assistant-layout ${auth.enabled ? "has-history" : ""}`}>
        {auth.enabled ? (
          <aside className="panel assistant-history" aria-label="Saved conversations">
            <div className="assistant-history-heading">
              <div><span>HR workspace</span><h2>Your conversations</h2></div>
              <button className="button button-primary" disabled={assistant.isPending} onClick={startNewConversation} type="button">New chat</button>
            </div>
            {conversations.isLoading ? <p className="assistant-history-empty">Loading saved chats…</p> : null}
            {conversations.error ? <ErrorBanner error={conversations.error} /> : null}
            {conversations.data?.length ? (
              <div className="assistant-history-list">
                {conversations.data.map((item) => (
                  <div className={`assistant-history-item ${item.id === selectedId ? "is-selected" : ""}`} key={item.id}>
                    <button
                      className="assistant-history-open"
                      aria-current={item.id === selectedId ? "page" : undefined}
                      disabled={assistant.isPending}
                      onClick={() => { setSearchParams({ conversation: item.id }); assistant.reset(); }}
                      type="button"
                    >
                      <strong>{item.title || "Untitled conversation"}</strong>
                      <span>{item.last_message_preview || "No messages"}</span>
                      <small>{new Date(item.updated_at).toLocaleString()}</small>
                    </button>
                    <button
                      aria-label={`Delete ${item.title || "conversation"}`}
                      className="assistant-history-delete"
                      disabled={deleteConversation.isPending || assistant.isPending}
                      onClick={() => deleteSavedConversation(item.id, item.title)}
                      type="button"
                    >×</button>
                  </div>
                ))}
              </div>
            ) : !conversations.isLoading && !conversations.error ? (
              <p className="assistant-history-empty">Saved chats will appear here.</p>
            ) : null}
            <p className="assistant-retention-note">Chats are private to your HR account and organization. They remain saved until you delete them.</p>
          </aside>
        ) : null}

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
                disabled={!assistantReady || assistant.isPending}
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
            <small>{auth.enabled ? "Questions use this organization’s saved attendance evidence and retrieved policies." : "Questions are limited to the selected manager’s team and retrieved attendance policies."}</small>
          </form>

          {assistant.error ? (
            <ErrorBanner error={assistant.error} onRetry={() => ask(question)} />
          ) : null}
          {auth.enabled && selectedId && conversation.isLoading ? (
            <div className="assistant-empty" role="status">Loading saved conversation…</div>
          ) : null}
          {auth.enabled && conversation.error ? <ErrorBanner error={conversation.error} /> : null}

          {messages.length === 0 && !(auth.enabled && selectedId && conversation.isLoading) ? (
            <div className="assistant-empty">
              <strong>{selectedId && auth.enabled ? "This conversation has no messages." : "Answers stay tied to evidence."}</strong>
              <span>ShiftGuard does not infer why an employee was absent or make attendance decisions.</span>
            </div>
          ) : (
            <div aria-live="polite" className="assistant-conversation">
              {messages.map((message) => message.role === "user" ? (
                <div className="assistant-question-bubble" key={message.id || message.localId}>
                  <span>You asked</span>
                  <p>{message.content}</p>
                </div>
              ) : (
                <article className="assistant-answer" key={message.id || message.localId}>
                  <div className="assistant-answer-heading">
                    <strong>ShiftGuard</strong>
                    <span>{message.model ? `Source: ${message.model}` : "Evidence-only answer"}</span>
                  </div>
                  <p>{message.content}</p>
                  {message.source_references.length ? (
                    <div className="assistant-sources">
                      <h2>Sources</h2>
                      {message.source_references.map((source) => (
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
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function demoTurn(question: string, result: AssistantAnswer): DisplayMessage[] {
  const now = new Date().toISOString();
  return [
    {
      id: `local-user-${now}`,
      role: "user",
      content: question,
      model: null,
      source_references: [],
      created_at: now,
    },
    {
      id: `local-assistant-${now}`,
      role: "assistant",
      content: result.answer,
      model: result.model,
      source_references: result.sources,
      created_at: now,
    },
  ];
}

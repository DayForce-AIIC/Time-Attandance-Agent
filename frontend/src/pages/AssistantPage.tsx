import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useAssistantConversation, useAssistantConversations, useAssistantQuery, useDeleteAssistantConversation, useHealth } from "../api/hooks";
import type { z } from "zod";
import { assistantAnswerSchema, conversationMessageSchema } from "../api/schemas";
import { useAuth } from "../auth/AuthContext";
import { AnswerCard, Composer, ConversationSidebar, UserBubble, Welcome } from "../components/AssistantParts";
import { ErrorBanner, PageHeading } from "../components/common";
import { IconPlus } from "../components/icons";

import { BRAND } from "../brand";
type AssistantAnswer = z.infer<typeof assistantAnswerSchema>;
type ConversationMessage = z.infer<typeof conversationMessageSchema>;
type DisplayMessage = ConversationMessage & { localId?: string };
type StreamingTurn = {
  question: string;
  answer: string;
  status: string;
  sources: AssistantAnswer["sources"];
  model: string | null;
  complete: boolean;
  conversationId: string | null;
  warning: string | null;
};

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
  const [streamingTurn, setStreamingTurn] = useState<StreamingTurn | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const shouldAutoScroll = useRef(true);
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
      ? `Could not confirm Ollama availability. Ask ${BRAND.name} is disabled until the backend health check succeeds.`
      : "Checking Ollama availability…"
    : health.data.provider !== "ollama"
      ? `Ask ${BRAND.name} requires Ollama and is disabled while the backend is in mock mode. Set LLM_PROVIDER=ollama and restart the backend.`
      : !health.data.llm_reachable
        ? `Ollama or the configured model (${health.data.model}) is unavailable. Start Ollama and make sure the model is installed.`
        : null;

  const startNewConversation = () => {
    setSearchParams({}, { replace: false });
    setDemoMessages([]);
    setStreamingTurn(null);
    shouldAutoScroll.current = true;
    assistant.reset();
    setQuestion("");
  };

  const ask = (value: string) => {
    const cleaned = value.trim();
    if (!cleaned || assistant.isPending || !assistantReady) return;
    shouldAutoScroll.current = true;
    setStreamingTurn({
      question: cleaned,
      answer: "",
      status: "Reviewing records and policies…",
      sources: [],
      model: null,
      complete: false,
      conversationId: selectedId,
      warning: null,
    });
    assistant.mutate({
      question: cleaned,
      conversation_id: selectedId,
      onEvent: (event) => {
        if (event.event === "status" && typeof event.data === "object" && event.data !== null) {
          const message = "message" in event.data && typeof event.data.message === "string"
            ? event.data.message
            : "Reviewing records and policies…";
          setStreamingTurn((current) => current ? { ...current, status: message } : current);
        } else if (event.event === "token" && typeof event.data === "object" && event.data !== null) {
          const text = "text" in event.data && typeof event.data.text === "string" ? event.data.text : "";
          setStreamingTurn((current) => current ? { ...current, answer: current.answer + text } : current);
        } else if (event.event === "reset") {
          setStreamingTurn((current) => current ? { ...current, answer: "", sources: [], complete: false } : current);
        } else if (event.event === "conversation" && typeof event.data === "object" && event.data !== null) {
          const id = "conversation_id" in event.data && typeof event.data.conversation_id === "string"
            ? event.data.conversation_id
            : null;
          if (id) setStreamingTurn((current) => current ? { ...current, conversationId: id } : current);
        } else if ((event.event === "answer" || event.event === "complete") && typeof event.data === "object") {
          const parsed = assistantAnswerSchema.safeParse(event.data);
          if (parsed.success) {
            setStreamingTurn((current) => current ? {
              ...current,
              answer: parsed.data.answer,
              status: "",
              sources: parsed.data.sources,
              model: parsed.data.model,
              complete: true,
              conversationId: parsed.data.conversation_id ?? current.conversationId,
            } : current);
          }
        } else if (event.event === "save_error" && typeof event.data === "object" && event.data !== null) {
          const detail = "detail" in event.data && typeof event.data.detail === "string"
            ? event.data.detail
            : "The answer couldn't be saved to chat history.";
          setStreamingTurn((current) => current ? { ...current, warning: detail } : current);
        }
      },
    }, {
      onSuccess: (result) => {
        setQuestion("");
        if (result.conversation_id) {
          setStreamingTurn((current) => ({
            question: current?.question ?? cleaned,
            answer: result.answer,
            status: "",
            sources: result.sources,
            model: result.model,
            complete: true,
            conversationId: result.conversation_id,
            warning: current?.warning ?? null,
          }));
          setSearchParams({ conversation: result.conversation_id });
        } else if (auth.enabled) {
          setStreamingTurn((current) => current ? {
            ...current,
            answer: result.answer,
            status: "",
            sources: result.sources,
            model: result.model,
            complete: true,
            warning: current.warning ?? "This answer is visible but wasn't saved to chat history.",
          } : current);
        } else {
          setStreamingTurn(null);
          setDemoMessages((previous) => [...previous, ...demoTurn(cleaned, result)]);
        }
      },
      onError: () => setStreamingTurn(null),
    });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    ask(question);
  };

  const messages = useMemo<DisplayMessage[]>(
    () => auth.enabled ? conversation.data?.messages ?? [] : demoMessages,
    [auth.enabled, conversation.data?.messages, demoMessages],
  );
  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) return;
    // Follow new answers, but keep the welcome screen scrolled to its top.
    if (messages.length === 0 && !streamingTurn) thread.scrollTop = 0;
    else if (shouldAutoScroll.current) thread.scrollTop = thread.scrollHeight;
  }, [messages, selectedId, streamingTurn]);

  const streamingTurnIsSaved = Boolean(
    streamingTurn?.complete
    && streamingTurn.conversationId
    && conversation.data?.messages.some((message, index, allMessages) =>
      message.role === "assistant"
      && message.content === streamingTurn.answer
      && allMessages[index - 1]?.role === "user"
      && allMessages[index - 1]?.content === streamingTurn.question
    ),
  );
  const visibleStreamingTurn = streamingTurnIsSaved
    || Boolean(selectedId && streamingTurn?.conversationId && selectedId !== streamingTurn.conversationId)
    ? null
    : streamingTurn;

  const deleteSavedConversation = (id: string, title: string | null) => {
    if (!window.confirm(`Delete “${title || "Untitled conversation"}” and its saved messages?`)) return;
    deleteConversation.mutate(id, {
      onSuccess: () => {
        if (selectedId === id) startNewConversation();
      },
    });
  };

  const hasConversation = messages.length > 0 || Boolean(visibleStreamingTurn);
  const savedConversationLoading = Boolean(auth.enabled && selectedId && conversation.isLoading && !visibleStreamingTurn);
  const scopeNote = auth.enabled
    ? "Questions use this organization’s saved attendance evidence and retrieved policies."
    : "Questions are limited to the selected manager’s team and retrieved attendance policies.";

  return (
    <main className="page-shell assistant-page">
      <PageHeading
        eyebrow="Read-only assistant"
        title={`Ask ${BRAND.name}`}
        subtitle="Ask about attendance cases or company policy. Every answer cites the records and policy passages it used."
      >
        {!auth.enabled && hasConversation ? (
          <button className="button button-outline" disabled={assistant.isPending} onClick={startNewConversation} type="button"><IconPlus /> New chat</button>
        ) : null}
      </PageHeading>

      <div className={`assistant-layout ${auth.enabled ? "has-history" : ""}`}>
        {auth.enabled ? (
          <ConversationSidebar
            items={conversations.data}
            loading={conversations.isLoading}
            errorNode={conversations.error ? <ErrorBanner error={conversations.error} /> : null}
            selectedId={selectedId}
            busy={assistant.isPending || deleteConversation.isPending}
            onNew={startNewConversation}
            onOpen={(id) => { setStreamingTurn(null); shouldAutoScroll.current = true; setSearchParams({ conversation: id }); assistant.reset(); }}
            onDelete={deleteSavedConversation}
          />
        ) : null}

        <section className="assistant-panel" aria-label="Attendance questions">
          {availabilityMessage ? (
            <div className="assistant-availability" role="status" aria-live="polite">
              <strong>Assistant unavailable</strong>
              <p>{availabilityMessage}</p>
            </div>
          ) : null}

          <div
            className="assistant-thread"
            onScroll={(event) => {
              const element = event.currentTarget;
              shouldAutoScroll.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
            }}
            ref={threadRef}
          >
            {assistant.error ? <ErrorBanner error={assistant.error} onRetry={() => ask(question)} /> : null}
            {savedConversationLoading ? <div className="assistant-empty" role="status">Loading saved conversation…</div> : null}
            {auth.enabled && conversation.error ? <ErrorBanner error={conversation.error} /> : null}

            {!hasConversation && !savedConversationLoading ? (
              <Welcome
                prompts={QUICK_QUESTIONS}
                disabled={assistant.isPending || !assistantReady}
                onAsk={ask}
                empty={Boolean(selectedId && auth.enabled)}
              />
            ) : hasConversation ? (
              <div aria-live="polite" className="assistant-conversation">
                {messages.map((message) => message.role === "user" ? (
                  <UserBubble key={message.id || message.localId}>{message.content}</UserBubble>
                ) : (
                  <AnswerCard
                    key={message.id || message.localId}
                    content={message.content}
                    label={message.model ?? "Evidence-only answer"}
                    sources={message.source_references}
                  />
                ))}
                {visibleStreamingTurn ? (
                  <>
                    <UserBubble>{visibleStreamingTurn.question}</UserBubble>
                    <AnswerCard
                      content={visibleStreamingTurn.answer}
                      status={visibleStreamingTurn.status}
                      streaming={!visibleStreamingTurn.complete}
                      label={visibleStreamingTurn.complete
                        ? visibleStreamingTurn.model ?? "Evidence-based answer"
                        : visibleStreamingTurn.answer ? "Writing…" : "Thinking…"}
                      warning={visibleStreamingTurn.warning}
                      sources={visibleStreamingTurn.sources}
                    />
                  </>
                ) : null}
              </div>
            ) : null}
          </div>

          <Composer
            value={question}
            onChange={setQuestion}
            onSubmit={submit}
            disabled={!assistantReady}
            pending={assistant.isPending}
            scopeNote={scopeNote}
          />
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

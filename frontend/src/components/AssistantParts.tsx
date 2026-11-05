import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { z } from "zod";
import { assistantSourceSchema, conversationSummarySchema } from "../api/schemas";
import { BRAND } from "../brand";
import { BrandMark } from "./BrandMark";
import { IconClipboard, IconFileText, IconPlus, IconSearch, IconSend, IconShield, IconTrash } from "./icons";

type Source = z.infer<typeof assistantSourceSchema>;
type ConversationSummary = z.infer<typeof conversationSummarySchema>;

export function UserBubble({ children }: { children: string }) {
  return <div className="assistant-question-bubble"><p>{children}</p></div>;
}

/** One assistant reply: avatar, readable paragraphs, a thinking indicator while streaming, and source cards. */
export function AnswerCard({
  content,
  status,
  label,
  warning,
  sources,
  streaming = false,
}: {
  content: string;
  status?: string;
  label: string;
  warning?: string | null;
  sources: Source[];
  streaming?: boolean;
}) {
  const paragraphs = content.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  return (
    <article className={`assistant-answer${streaming ? " assistant-answer-streaming" : ""}`}>
      <BrandMark className="assistant-avatar" />
      <div className="assistant-answer-body">
        <div className="assistant-answer-heading">
          <strong>{BRAND.name}</strong>
          <span className="assistant-model">{label}</span>
        </div>
        {paragraphs.length ? (
          <div className="assistant-text">{paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div>
        ) : status ? (
          <p className="assistant-status"><span className="assistant-dots" aria-hidden="true"><span /><span /><span /></span>{status}</p>
        ) : null}
        {warning ? <small className="assistant-save-warning">{warning}</small> : null}
        {sources.length ? (
          <section className="assistant-sources-block" aria-label="Sources">
            <h3 className="assistant-sources-title">Sources · {sources.length}</h3>
            <ul className="assistant-sources">
              {sources.map((source) => (
                <li className="assistant-source" key={source.source_id}>
                  <span className={`source-icon source-${source.kind}`} aria-hidden="true">
                    {source.kind === "case" ? <IconClipboard /> : <IconFileText />}
                  </span>
                  <div className="assistant-source-main">
                    {source.href ? <Link to={source.href}>{source.label} ↗</Link> : <strong>{source.label}</strong>}
                    <p>{source.excerpt}</p>
                  </div>
                  <span className={`source-badge source-${source.kind}`}>{source.kind}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </article>
  );
}

/** Empty state: what the assistant can do, with suggested questions as one-click cards. */
export function Welcome({
  prompts,
  disabled,
  onAsk,
  empty = false,
}: {
  prompts: string[];
  disabled: boolean;
  onAsk: (prompt: string) => void;
  empty?: boolean;
}) {
  return (
    <div className="assistant-welcome">
      <BrandMark className="assistant-welcome-mark" />
      <h2>{empty ? "This conversation has no messages" : "How can I help with attendance today?"}</h2>
      <p>Ask about cases, employees or company policy. I answer from your records and cite every source. I never guess why someone was absent, and I don’t make attendance decisions.</p>
      <div className="assistant-suggestions" role="group" aria-label="Suggested questions">
        <span>Try asking</span>
        {prompts.map((prompt) => (
          <button className="assistant-chip" disabled={disabled} key={prompt} onClick={() => onAsk(prompt)} type="button">
            <IconSearch className="assistant-chip-icon" />{prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Message box: Enter sends, Shift+Enter adds a line, with the read-only reminder underneath. */
export function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  pending,
  scopeNote,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  disabled: boolean;
  pending: boolean;
  scopeNote: string;
}) {
  return (
    <form className="assistant-composer" onSubmit={onSubmit}>
      <label className="visually-hidden" htmlFor="assistant-question">Your question</label>
      <div className="composer-box">
        <textarea
          autoComplete="off"
          disabled={disabled || pending}
          id="assistant-question"
          maxLength={500}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Ask about a case, an employee or a policy…"
          rows={1}
          value={value}
        />
        <button className="button button-primary composer-send" disabled={!value.trim() || pending || disabled} type="submit">
          {pending ? "Answering…" : <>Ask <IconSend /></>}
        </button>
      </div>
      <div className="composer-meta">
        <small>The assistant cannot change timecards or approve decisions. {scopeNote}</small>
        <small className="composer-count">{value.length}/500</small>
      </div>
    </form>
  );
}

function formatChatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (date.getTime() >= startOfToday) {
    return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(date);
  }
  if (date.getTime() >= startOfToday - 86_400_000) return "Yesterday";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(date);
}

/** Saved conversations (HR mode): newest first, with a clear New chat action. */
export function ConversationSidebar({
  items,
  loading,
  errorNode,
  selectedId,
  busy,
  onNew,
  onOpen,
  onDelete,
}: {
  items: ConversationSummary[] | undefined;
  loading: boolean;
  errorNode?: ReactNode;
  selectedId: string | null;
  busy: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string, title: string | null) => void;
}) {
  return (
    <aside className="assistant-history" aria-label="Saved conversations">
      <div className="assistant-history-heading">
        <h2>Conversations</h2>
        <button className="button button-primary" disabled={busy} onClick={onNew} type="button"><IconPlus /> New chat</button>
      </div>
      {loading ? <p className="assistant-history-empty">Loading saved chats…</p> : null}
      {errorNode}
      {items?.length ? (
        <div className="assistant-history-list">
          {items.map((item) => (
            <div className={`assistant-history-item ${item.id === selectedId ? "is-selected" : ""}`} key={item.id}>
              <button
                className="assistant-history-open"
                aria-current={item.id === selectedId ? "page" : undefined}
                disabled={busy}
                onClick={() => onOpen(item.id)}
                type="button"
              >
                <strong>{item.title || "Untitled conversation"}</strong>
                <span>{item.last_message_preview || "No messages"}</span>
                <small>{formatChatDate(item.updated_at)}</small>
              </button>
              <button
                aria-label={`Delete ${item.title || "conversation"}`}
                className="assistant-history-delete"
                disabled={busy}
                onClick={() => onDelete(item.id, item.title)}
                type="button"
              ><IconTrash /></button>
            </div>
          ))}
        </div>
      ) : !loading && !errorNode ? (
        <p className="assistant-history-empty">Saved chats will appear here.</p>
      ) : null}
      <p className="assistant-retention-note"><IconShield /> Chats are private to your HR account and organization, and stay saved until you delete them.</p>
    </aside>
  );
}

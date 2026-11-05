import { useEffect, useRef, type ReactNode } from "react";
import { IconX } from "./icons";

/** Right-hand slide-over for focused tasks (editing a correction, rejecting a suggestion). */
export function SlideOver({
  eyebrow,
  title,
  closeLabel = "Close",
  onClose,
  children,
}: {
  eyebrow: string;
  title: string;
  closeLabel?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bodyRef.current?.querySelector<HTMLElement>("input, select, textarea")?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside className="slide-over" role="dialog" aria-modal="true" aria-labelledby="slide-over-title">
        <header className="slide-over-header">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h2 id="slide-over-title">{title}</h2>
          </div>
          <button type="button" className="icon-button" aria-label={closeLabel} onClick={onClose}><IconX /></button>
        </header>
        <div className="slide-over-body" ref={bodyRef}>{children}</div>
      </aside>
    </div>
  );
}

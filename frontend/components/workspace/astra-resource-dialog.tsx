"use client";

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";

/**
 * Lightweight native modal in Astra's approved form geometry.
 * A native <dialog> provides focus containment, Esc handling and inert
 * background without introducing a second global component system.
 * Existing pages keep their API, authorization, and validation logic.
 */
export function AstraResourceDialog({
  open, title, description, onDismiss, busy=false, children,
}: {
  open: boolean;
  title: string;
  description: string;
  onDismiss: () => void;
  busy?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="astra-resource-dialog"
      aria-labelledby="astra-resource-dialog-title"
      aria-describedby="astra-resource-dialog-description"
      onCancel={(event) => {
        if (busy) event.preventDefault();
        else onDismiss();
      }}
      onClose={() => {
        if (open && !busy) onDismiss();
      }}
    >
      <header className="astra-resource-dialog-header">
        <div>
          <span className="astra-resource-eyebrow">MateMail Hub · Organization administration</span>
          <h2 id="astra-resource-dialog-title">{title}</h2>
          <p id="astra-resource-dialog-description">{description}</p>
        </div>
        <button type="button" onClick={onDismiss} disabled={busy} aria-label="Close dialog" className="astra-resource-dialog-close">
          <X size={16} aria-hidden="true"/>
        </button>
      </header>
      <div className="astra-resource-dialog-body">{children}</div>
    </dialog>
  );
}

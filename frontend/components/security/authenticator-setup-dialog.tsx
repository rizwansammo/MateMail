"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { ShieldCheck, X } from "lucide-react";
import { AuthenticatorSetupCode } from "@/components/security/authenticator-setup-code";

interface Props {
  setup: { secret: string; uri: string } | null;
  code: string;
  error: string;
  busy: boolean;
  onCodeChange: (value: string) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  onRetry: () => void;
  onClose: () => void;
}

/** Only the X explicitly dismisses enrollment; backdrop and Escape do nothing. */
export function AuthenticatorSetupDialog({
  setup, code, error, busy, onCodeChange, onSubmit, onRetry, onClose,
}: Props) {
  const dialog = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    return () => { document.body.style.overflow = oldOverflow; previous?.focus(); };
  }, []);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); return; }
    if (event.key !== "Tab") return;
    const nodes = Array.from(dialog.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((node) => node.getClientRects().length > 0);
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (!first || !last) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center overflow-y-auto bg-slate-950/70 p-3 backdrop-blur-[5px] sm:p-6">
      <div ref={dialog} role="dialog" aria-modal="true"
        aria-labelledby="hub-mfa-dialog-title" aria-describedby="hub-mfa-dialog-description"
        onKeyDown={onKeyDown}
        className="relative my-auto w-full max-w-[465px] rounded-3xl border border-slate-200 bg-white p-5 shadow-2xl outline-none sm:p-7 dark:border-slate-700 dark:bg-slate-900">
        <button ref={closeButton} type="button" aria-label="Close two-factor setup"
          title="Close setup" onClick={onClose} disabled={busy}
          className="absolute right-4 top-4 inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-slate-800 dark:hover:text-white">
          <X className="h-[19px] w-[19px]" aria-hidden="true" />
        </button>
        <div className="mb-5 flex flex-col items-center gap-2 text-center">
          <div className="mb-1 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300">
            <ShieldCheck className="h-6 w-6" aria-hidden="true" />
          </div>
          <h2 id="hub-mfa-dialog-title" className="pr-3 text-xl font-bold tracking-tight text-slate-900 dark:text-white">
            Set up two-factor authentication
          </h2>
          <p id="hub-mfa-dialog-description" className="max-w-[360px] text-sm leading-6 text-slate-500 dark:text-slate-400">
            Scan the QR code in your authenticator app, then enter its six-digit verification code.
          </p>
        </div>
        {error && <div role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/50 dark:text-red-200">{error}</div>}
        {setup ? (
          <>
            <AuthenticatorSetupCode secret={setup.secret} uri={setup.uri} />
            <form onSubmit={onSubmit} className="mt-5 space-y-3">
              <label htmlFor="hub-mfa-verify-code" className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                Six-digit verification code
              </label>
              <input id="hub-mfa-verify-code" type="text" inputMode="numeric"
                autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required
                value={code} onChange={(event) => onCodeChange(event.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="000 000"
                className="h-12 w-full rounded-xl border border-slate-300 bg-white px-4 text-center font-mono text-lg font-semibold tracking-[0.35em] text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-white" />
              <button type="submit" disabled={busy || code.length !== 6}
                className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:cursor-not-allowed disabled:opacity-55">
                <ShieldCheck className="h-4 w-4" aria-hidden="true" /> {busy ? "Verifying…" : "Verify & enable 2FA"}
              </button>
            </form>
          </>
        ) : (
          <button type="button" disabled={busy} onClick={onRetry}
            className="flex h-11 w-full items-center justify-center rounded-xl bg-blue-600 px-4 font-semibold text-white disabled:opacity-55">
            {busy ? "Loading…" : "Try setup again"}
          </button>
        )}
        <p className="mt-4 text-center text-[11px] leading-4 text-slate-400">Setup stays open if you click outside. Use × to close.</p>
      </div>
    </div>,
    document.body,
  );
}

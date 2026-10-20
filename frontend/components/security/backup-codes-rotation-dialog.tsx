"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { KeyRound, RotateCcw, ShieldAlert, X } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { BackupCodesDisclosure } from "@/components/security/backup-codes-disclosure";

interface Props { onClose: () => void }

/** Recovery rotation is account-wide, never organization-wide. */
export function BackupCodesRotationDialog({ onClose }: Props) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, []);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); return; }
    if (event.key !== "Tab") return;
    const nodes = Array.from(panel.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((item) => item.getClientRects().length > 0);
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (!first || !last) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  async function rotate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!password || !/^[0-9]{6}$/.test(code) || busy || codes.length > 0) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiRequest("/api/auth/2fa/backup-codes/regenerate/", {
        method: "POST",
        body: JSON.stringify({ password, code }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setError(data?.detail ?? "Could not regenerate backup codes.");
        return;
      }
      if (!Array.isArray(data?.backup_codes) || data.backup_codes.length !== 10
          || !data.backup_codes.every((item: unknown) => typeof item === "string")) {
        setError("Backup codes changed but could not be displayed. Keep your authenticator and generate a fresh set using a new code.");
        return;
      }
      setCodes(data.backup_codes);
      setPassword("");
      setCode("");
    } catch {
      setError("Could not regenerate backup codes. Check your connection and try a new authenticator code.");
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[210] flex items-center justify-center overflow-y-auto bg-slate-950/70 p-3 backdrop-blur-[5px] sm:p-6">
      <div ref={panel} onKeyDown={onKeyDown} role="dialog" aria-modal="true"
        aria-labelledby="hub-rotation-title" aria-describedby="hub-rotation-description"
        className="relative my-auto w-full max-w-[465px] rounded-3xl border border-slate-200 bg-white p-5 shadow-2xl sm:p-7 dark:border-slate-700 dark:bg-slate-900">
        <button ref={closeButton} type="button" title="Close" aria-label="Close backup code regeneration"
          onClick={onClose} disabled={busy || codes.length > 0}
          className="absolute right-4 top-4 inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:opacity-40 dark:hover:bg-slate-800">
          <X className="h-[19px] w-[19px]" aria-hidden="true" />
        </button>
        <div className="mb-5 flex flex-col items-center gap-2 text-center">
          <div className="mb-1 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300">
            {codes.length > 0 ? <KeyRound className="h-6 w-6" aria-hidden="true" /> : <RotateCcw className="h-6 w-6" aria-hidden="true" />}
          </div>
          <h2 id="hub-rotation-title" className="pr-3 text-xl font-bold text-slate-900 dark:text-white">
            {codes.length > 0 ? "Your new backup codes" : "Regenerate backup codes"}
          </h2>
          <p id="hub-rotation-description" className="max-w-[365px] text-sm leading-6 text-slate-500 dark:text-slate-400">
            {codes.length > 0
              ? "Your new one-time recovery codes are ready to save."
              : "Confirm your account password and a new six-digit code from Google Authenticator or another TOTP app."}
          </p>
        </div>

        {codes.length > 0 ? (
          <BackupCodesDisclosure codes={codes} replaced onSaved={onClose} />
        ) : (
          <>
            <div className="mb-4 flex gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm leading-5 text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
              Generating new codes immediately invalidates ALL previous backup codes. Your authenticator app will keep working.
            </div>
            {error && <p role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">{error}</p>}
            <form onSubmit={rotate} className="space-y-4">
              <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                Current account password
                <input type="password" autoComplete="current-password" required value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="mt-2 h-12 w-full rounded-xl border border-slate-300 bg-white px-3 text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-white" />
              </label>
              <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                Current authenticator code
                <input type="text" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code"
                  required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="000 000"
                  className="mt-2 h-12 w-full rounded-xl border border-slate-300 bg-white px-3 text-center font-mono text-lg font-semibold tracking-widest text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-white" />
              </label>
              <button type="submit" disabled={busy || !password || code.length !== 6}
                className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:cursor-not-allowed disabled:opacity-50">
                <RotateCcw className="h-4 w-4" aria-hidden="true" /> {busy ? "Regenerating…" : "Revoke old & generate new codes"}
              </button>
            </form>
          </>
        )}
        <p className="mt-4 text-center text-[11px] leading-4 text-slate-400">
          Clicking outside will not close this window.
        </p>
      </div>
    </div>,
    document.body,
  );
}

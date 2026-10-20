"use client";

import { useState } from "react";
import { Check, Copy, ShieldCheck, ShieldAlert } from "lucide-react";

interface Props {
  codes: string[];
  onSaved: () => void;
  replaced?: boolean;
}

/** One-time display. Never persist codes to browser storage or telemetry. */
export function BackupCodesDisclosure({ codes, onSaved, replaced = false }: Props) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  async function copyCodes() {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
        <div className="mb-1 flex items-center gap-2 font-bold">
          <ShieldAlert className="h-5 w-5" aria-hidden="true" />
          Save these codes before leaving
        </div>
        {replaced
          ? "Your previous backup codes are now invalid. Only these new codes can be used."
          : "Two-factor authentication is now enabled. These recovery codes are shown only once."}
        {" "}Each code works for one login if you cannot use your authenticator.
      </div>

      <div className="grid grid-cols-2 gap-2 rounded-2xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/70" aria-label="Your new one-time backup codes">
        {codes.map((code) => (
          <code key={code} className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-center font-mono text-sm font-semibold tracking-widest text-slate-800 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100">
            {code}
          </code>
        ))}
      </div>

      <button type="button" onClick={() => void copyCodes()}
        className="flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-4 text-sm font-semibold text-blue-700 hover:bg-blue-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-300">
        {copied ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
        {copied ? "Copied to clipboard" : "Copy all backup codes"}
      </button>
      {copyError && <p role="alert" className="text-sm text-red-600">Copy was blocked by your browser. Save the codes manually.</p>}
      <p className="text-center text-xs leading-5 text-slate-500 dark:text-slate-400">
        Store these codes in your password manager. They cannot be viewed again after closing.
      </p>
      <button type="button" onClick={onSaved}
        className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500">
        <ShieldCheck className="h-4 w-4" aria-hidden="true" />
        I&apos;ve saved my backup codes
      </button>
    </div>
  );
}

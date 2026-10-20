"use client";

import { useState } from "react";
import { Check, Copy, KeyRound, QrCode } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";

interface Props { secret: string; uri: string; compact?: boolean }

/** Encode the otpauth URI in this browser, never through a remote QR website. */
export function AuthenticatorSetupCode({ secret, uri, compact = false }: Props) {
  const [manual, setManual] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copyKey() {
    try { await navigator.clipboard.writeText(secret); setCopied(true); }
    catch { setCopied(false); }
  }

  return (
    <div className="mx-auto flex w-full max-w-[350px] flex-col items-center gap-3">
      {!manual ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <QRCodeSVG value={uri} size={compact ? 174 : 196} level="M"
            bgColor="#ffffff" fgColor="#0f172a" title="Authenticator setup QR code" />
        </div>
      ) : (
        <div className="flex min-h-[214px] w-full flex-col items-center justify-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-5 py-6 text-slate-900">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <KeyRound className="h-4 w-4" aria-hidden="true" /> Manual setup key
          </div>
          <code className="select-all break-all text-center font-mono text-[13px] font-semibold leading-6 tracking-[0.08em]">{secret}</code>
          <button type="button" onClick={() => void copyKey()}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500">
            {copied ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
            {copied ? "Copied" : "Copy secret key"}
          </button>
        </div>
      )}
      <button type="button" aria-pressed={manual} onClick={() => { setManual(!manual); setCopied(false); }}
        className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-blue-600 hover:bg-blue-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 dark:text-blue-400 dark:hover:bg-white/10">
        {manual ? <QrCode className="h-4 w-4" aria-hidden="true" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />}
        {manual ? "Show QR code" : "Show secret key"}
      </button>
      <p className="max-w-[330px] text-center text-xs leading-5 text-slate-500 dark:text-slate-400">
        {manual
          ? "In your authenticator app, select Enter a setup key and paste this secret."
          : "Open Google Authenticator, Microsoft Authenticator, 2FAS or another TOTP app and scan the code."}
      </p>
    </div>
  );
}

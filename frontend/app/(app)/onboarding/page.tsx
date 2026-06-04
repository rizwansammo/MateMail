"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Copy,
  Globe2,
  Inbox,
  RefreshCw,
  ShieldCheck,
  XCircle,
  AlertCircle,
  Clock,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { api, ApiError, apiRequest } from "@/lib/api";

const STEPS = ["Workspace", "Domain", "DNS", "Verify", "Mailbox", "Complete"];

interface Domain {
  id: string;
  domain: string;
  status: string;
  dns_health_score: number;
  dkim_selector: string;
  dkim_public_key: string;
}

interface DNSRecord {
  id: string;
  record_type: string;
  host: string;
  expected_value: string;
  detected_value: string;
  status: "verified" | "pending" | "missing" | "failed";
  last_checked: string | null;
}

interface Mailbox {
  id: string;
  email: string;
}

export default function OnboardingPage() {
  const router = useRouter();
  const { tenant, user } = useAuth();

  const [step, setStep] = useState(0);
  const [domain, setDomain] = useState<Domain | null>(null);
  const [mailbox, setMailbox] = useState<Mailbox | null>(null);

  // Step 1 state
  const [domainInput, setDomainInput] = useState("");
  const [domainError, setDomainError] = useState("");
  const [domainLoading, setDomainLoading] = useState(false);

  // Step 3 state
  const [dnsRecordResults, setDnsRecordResults] = useState<DNSRecord[]>([]);
  const [dnsChecking, setDnsChecking] = useState(false);
  const [dnsChecked, setDnsChecked] = useState(false);

  // Step 4 state
  const [mbLocalPart, setMbLocalPart] = useState("");
  const [mbFullName, setMbFullName] = useState(user?.full_name ?? "");
  const [mbPassword, setMbPassword] = useState("");
  const [mbError, setMbError] = useState("");
  const [mbLoading, setMbLoading] = useState(false);

  // Copy to clipboard helper
  const [copied, setCopied] = useState("");
  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(""), 2000);
    });
  }

  async function handleCheckDNS() {
    if (!domain) return;
    setDnsChecking(true);
    try {
      const res = await apiRequest(`/api/domains/${domain.id}/check/`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setDnsRecordResults(data.records ?? []);
        setDnsChecked(true);
        // Update domain state with fresh score/status
        setDomain((prev) => prev ? { ...prev, ...data.domain } : data.domain);
      }
    } finally {
      setDnsChecking(false);
    }
  }

  async function handleAddDomain() {
    setDomainError("");
    setDomainLoading(true);
    try {
      const result = await api.post<Domain>("/api/domains/", {
        domain: domainInput.trim().toLowerCase(),
      });
      setDomain(result);
      setStep(2);
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.message);
          setDomainError(body.domain ?? body.detail ?? "Failed to add domain.");
        } catch {
          setDomainError("Failed to add domain. Please try again.");
        }
      }
    } finally {
      setDomainLoading(false);
    }
  }

  async function handleCreateMailbox() {
    if (!domain) return;
    setMbError("");
    setMbLoading(true);
    try {
      const result = await api.post<Mailbox>("/api/mailboxes/", {
        local_part: mbLocalPart,
        domain_id: domain.id,
        full_name: mbFullName,
        quota_mb: 10240,
        password: mbPassword,
      });
      setMailbox(result);
      setStep(5);
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.message);
          setMbError(
            body.local_part ?? body.detail ?? "Failed to create mailbox."
          );
        } catch {
          setMbError("Failed to create mailbox. Please try again.");
        }
      }
    } finally {
      setMbLoading(false);
    }
  }

  const dnsRecords = domain
    ? [
        {
          type: "MX",
          host: "@",
          value: "10 mx.matemail.online",
          note: "Required for receiving email",
        },
        {
          type: "TXT",
          host: "@",
          value: "v=spf1 include:matemail.online ~all",
          note: "SPF — authorize MateMail to send on your behalf",
        },
        {
          type: "TXT",
          host: `${domain.dkim_selector}._domainkey`,
          value: domain.dkim_public_key
            ? `v=DKIM1; k=rsa; p=${domain.dkim_public_key}`
            : "(generating…)",
          note: "DKIM — email signing key",
          pending: !domain.dkim_public_key,
        },
        {
          type: "TXT",
          host: "_dmarc",
          value: "v=DMARC1; p=none; rua=mailto:dmarc@matemail.online",
          note: "DMARC — controls handling of unauthenticated mail",
        },
      ]
    : [];

  return (
    <div className="flex flex-col gap-8 p-6">
      {/* Progress bar */}
      <div className="grid grid-cols-6 gap-2">
        {STEPS.map((s, i) => (
          <div key={s}>
            <div
              className={`h-1.5 transition-colors ${
                i <= step ? "bg-cyan-500" : "bg-slate-200"
              }`}
            />
            <p
              className={`mt-1.5 hidden text-xs font-semibold md:block ${
                i <= step ? "text-slate-950" : "text-slate-400"
              }`}
            >
              {s}
            </p>
          </div>
        ))}
      </div>

      <div className="border border-slate-200 bg-white p-8 shadow-sm">
        {/* ── Step 0: Workspace ───────────────────────────────────── */}
        {step === 0 && (
          <div>
            <CheckCircle2 className="mb-4 h-8 w-8 text-emerald-500" />
            <h1 className="text-3xl font-black tracking-tight text-slate-950">
              Workspace created
            </h1>
            <p className="mt-2 text-slate-600">
              Your workspace <strong>{tenant?.name}</strong> is ready. Next,
              add the domain you want to host email for.
            </p>
            <div className="mt-8">
              <button
                onClick={() => setStep(1)}
                className="inline-flex items-center gap-2 bg-cyan-500 px-6 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400"
              >
                Add a domain
              </button>
            </div>
          </div>
        )}

        {/* ── Step 1: Domain ──────────────────────────────────────── */}
        {step === 1 && (
          <div>
            <Globe2 className="mb-4 h-8 w-8 text-cyan-600" />
            <h1 className="text-3xl font-black tracking-tight text-slate-950">
              Add your first domain
            </h1>
            <p className="mt-2 text-slate-600">
              Enter the root domain you want to host email for. You can add
              more domains later.
            </p>

            <div className="mt-8 max-w-md space-y-4">
              {domainError && (
                <div className="flex items-start gap-3 border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  {domainError}
                </div>
              )}

              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-slate-800">
                  Domain
                </span>
                <input
                  type="text"
                  autoFocus
                  value={domainInput}
                  onChange={(e) => setDomainInput(e.target.value)}
                  placeholder="example.com"
                  className="w-full border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
                />
                <p className="mt-1 text-xs text-slate-400">
                  Use the root domain only — not www.example.com
                </p>
              </label>

              <div className="flex gap-3">
                <button
                  onClick={() => setStep(0)}
                  className="border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Back
                </button>
                <button
                  onClick={handleAddDomain}
                  disabled={!domainInput.trim() || domainLoading}
                  className="bg-cyan-500 px-6 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {domainLoading ? "Adding…" : "Continue to DNS setup"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── Step 2: DNS Records ─────────────────────────────────── */}
        {step === 2 && domain && (
          <div>
            <h1 className="text-3xl font-black tracking-tight text-slate-950">
              Configure DNS records
            </h1>
            <p className="mt-2 text-slate-600">
              Add these records in your DNS provider for{" "}
              <strong>{domain.domain}</strong>. Verification is automatic once
              records propagate (usually within a few minutes).
            </p>

            <div className="mt-6 grid gap-3 md:grid-cols-2">
              {dnsRecords.map((rec, i) => (
                <div
                  key={i}
                  className={`border p-4 ${
                    rec.pending
                      ? "border-amber-200 bg-amber-50"
                      : "border-slate-200 bg-white"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="inline-flex bg-slate-950 px-2 py-0.5 text-xs font-bold text-white">
                      {rec.type}
                    </span>
                    {!rec.pending && (
                      <button
                        onClick={() => copy(rec.value, `${i}-value`)}
                        className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700"
                      >
                        {copied === `${i}-value` ? (
                          <Check className="h-3 w-3 text-emerald-500" />
                        ) : (
                          <Copy className="h-3 w-3" />
                        )}
                        Copy value
                      </button>
                    )}
                  </div>
                  <div className="mt-2 space-y-1 text-xs">
                    <div>
                      <span className="font-semibold text-slate-500">Host: </span>
                      <code className="font-mono text-slate-800">{rec.host}</code>
                    </div>
                    <div>
                      <span className="font-semibold text-slate-500">Value: </span>
                      <code className={`font-mono ${rec.pending ? "text-amber-700" : "text-slate-800"} break-all`}>
                        {rec.value}
                      </code>
                    </div>
                    <p className="text-slate-400">{rec.note}</p>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-6 flex gap-3">
              <button
                onClick={() => setStep(1)}
                className="border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                Back
              </button>
              <button
                onClick={() => setStep(3)}
                className="bg-cyan-500 px-6 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400"
              >
                I&apos;ve added these records
              </button>
            </div>
          </div>
        )}

        {/* ── Step 3: Verify ──────────────────────────────────────── */}
        {step === 3 && domain && (
          <div>
            <ShieldCheck className="mb-4 h-8 w-8 text-cyan-600" />
            <h1 className="text-3xl font-black tracking-tight text-slate-950">
              Verify DNS records
            </h1>
            <p className="mt-2 text-slate-600">
              Once you&apos;ve published the DNS records, click{" "}
              <strong>Check DNS</strong> to verify them. Propagation can take
              up to 48 hours but usually completes within minutes.
            </p>

            {/* Check button */}
            <div className="mt-6">
              <button
                onClick={handleCheckDNS}
                disabled={dnsChecking}
                className="inline-flex items-center gap-2 bg-cyan-500 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:opacity-60"
              >
                <RefreshCw className={`h-4 w-4 ${dnsChecking ? "animate-spin" : ""}`} />
                {dnsChecking ? "Checking…" : dnsChecked ? "Re-check DNS" : "Check DNS now"}
              </button>
            </div>

            {/* Results */}
            <div className="mt-4 space-y-2">
              {!dnsChecked
                ? [
                    { label: "MX record" },
                    { label: "SPF record" },
                    { label: "DKIM record" },
                    { label: "DMARC record" },
                  ].map(({ label }) => (
                    <div
                      key={label}
                      className="flex items-center justify-between border border-slate-200 bg-white px-4 py-3 text-sm"
                    >
                      <span className="font-semibold text-slate-700">{label}</span>
                      <span className="inline-flex items-center gap-1 border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-500">
                        <Clock className="h-3 w-3" />
                        Not checked
                      </span>
                    </div>
                  ))
                : dnsRecordResults.map((rec) => {
                    const statusMap = {
                      verified: { icon: CheckCircle2, cls: "border-emerald-200 bg-emerald-50 text-emerald-700" },
                      pending:  { icon: Clock,         cls: "border-amber-200  bg-amber-50  text-amber-700"  },
                      missing:  { icon: AlertCircle,   cls: "border-orange-200 bg-orange-50 text-orange-700" },
                      failed:   { icon: XCircle,       cls: "border-red-200   bg-red-50    text-red-700"    },
                    };
                    const cfg = statusMap[rec.status] ?? statusMap.pending;
                    const Icon = cfg.icon;
                    return (
                      <div
                        key={rec.id}
                        className="flex items-center justify-between border border-slate-200 bg-white px-4 py-3 text-sm"
                      >
                        <span className="font-semibold text-slate-700">{rec.record_type} — {rec.host}</span>
                        <span className={`inline-flex items-center gap-1 border px-2 py-0.5 text-xs font-medium capitalize ${cfg.cls}`}>
                          <Icon className="h-3 w-3" />
                          {rec.status}
                        </span>
                      </div>
                    );
                  })}
            </div>

            {dnsChecked && domain.dns_health_score > 0 && (
              <div className="mt-4 border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
                <strong>DNS health: {domain.dns_health_score}/100.</strong>{" "}
                {domain.dns_health_score === 100
                  ? "All records verified! Email is fully configured."
                  : "Some records verified. You can continue and finish setup — verification continues in the background."}
              </div>
            )}

            {dnsChecked && domain.dns_health_score === 0 && (
              <div className="mt-4 border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                No records detected yet. DNS changes can take time to propagate — you can continue
                and come back to check later via the Domains page.
              </div>
            )}

            <div className="mt-6 flex gap-3">
              <button
                onClick={() => setStep(2)}
                className="border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                Back
              </button>
              <button
                onClick={() => setStep(4)}
                className="bg-cyan-500 px-6 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400"
              >
                Continue — create first mailbox
              </button>
            </div>
          </div>
        )}

        {/* ── Step 4: Mailbox ─────────────────────────────────────── */}
        {step === 4 && domain && (
          <div>
            <Inbox className="mb-4 h-8 w-8 text-cyan-600" />
            <h1 className="text-3xl font-black tracking-tight text-slate-950">
              Create your first mailbox
            </h1>
            <p className="mt-2 text-slate-600">
              Create the first email address for{" "}
              <strong>{domain.domain}</strong>. More mailboxes can be added
              from the dashboard.
            </p>

            <div className="mt-8 max-w-md space-y-4">
              {mbError && (
                <div className="flex items-start gap-3 border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  {mbError}
                </div>
              )}

              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-slate-800">
                  Email address
                </span>
                <div className="flex">
                  <input
                    type="text"
                    value={mbLocalPart}
                    onChange={(e) => setMbLocalPart(e.target.value.toLowerCase())}
                    placeholder="you"
                    className="min-w-0 flex-1 border border-r-0 border-slate-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-slate-950 focus:ring-2 focus:ring-inset focus:ring-slate-950/10"
                  />
                  <span className="flex items-center border border-slate-300 bg-slate-50 px-3 text-sm font-semibold text-slate-500">
                    @{domain.domain}
                  </span>
                </div>
              </label>

              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-slate-800">
                  Display name
                </span>
                <input
                  type="text"
                  value={mbFullName}
                  onChange={(e) => setMbFullName(e.target.value)}
                  placeholder="Jane Smith"
                  className="w-full border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
                />
              </label>

              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-slate-800">
                  Mailbox password
                </span>
                <input
                  type="password"
                  value={mbPassword}
                  onChange={(e) => setMbPassword(e.target.value)}
                  placeholder="Min 10 characters"
                  className="w-full border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
                />
                <p className="mt-1 text-xs text-slate-400">
                  Used to access this mailbox via email clients and webmail.
                </p>
              </label>

              <div className="flex gap-3">
                <button
                  onClick={() => setStep(3)}
                  className="border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Back
                </button>
                <button
                  onClick={handleCreateMailbox}
                  disabled={
                    !mbLocalPart.trim() ||
                    !mbFullName.trim() ||
                    mbPassword.length < 10 ||
                    mbLoading
                  }
                  className="bg-cyan-500 px-6 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {mbLoading ? "Creating…" : "Create mailbox"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── Step 5: Complete ────────────────────────────────────── */}
        {step === 5 && (
          <div>
            <CheckCircle2 className="mb-4 h-10 w-10 text-emerald-500" />
            <h1 className="text-3xl font-black tracking-tight text-slate-950">
              Setup complete
            </h1>
            <p className="mt-2 text-slate-600">
              Your workspace is configured. Here&apos;s a summary of what was
              set up:
            </p>

            <div className="mt-6 space-y-2">
              {[
                { label: "Workspace", value: tenant?.name ?? "—" },
                { label: "Domain", value: domain?.domain ?? "—" },
                {
                  label: "First mailbox",
                  value: mailbox?.email ?? "—",
                },
              ].map(({ label, value }) => (
                <div
                  key={label}
                  className="flex items-center justify-between border border-slate-200 bg-white px-4 py-3 text-sm"
                >
                  <span className="font-semibold text-slate-500">{label}</span>
                  <span className="font-semibold text-slate-900">{value}</span>
                </div>
              ))}
            </div>

            <div className="mt-4 border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
              <strong>DNS verification pending.</strong> Check DNS Health for
              real-time record status once your DNS changes propagate. Email
              delivery activates when all records are verified.
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <Link
                href="/app"
                className="bg-slate-950 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                Go to dashboard
              </Link>
              <Link
                href="/app/mailboxes"
                className="border border-slate-300 bg-white px-5 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                Manage mailboxes
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

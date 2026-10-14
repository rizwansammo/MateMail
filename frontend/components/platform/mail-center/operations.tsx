"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Download, FileText, Mail, PenLine, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { describeError } from "@/contexts/platform-auth-context";
import {
  mailCenterApi, type MailRole, type MailFolder, type MailMessagePage,
  type MailDetail, type MailCompose, type MailDraftRef, type MailReplyRef,
} from "@/lib/mail-center-api";
import { Card, EmptyState, ErrorNote, Spinner, SuccessNote } from "@/components/platform/ui";

const OP = "operations@system.matemail.pro";
const INITIAL: MailCompose = { to: [], cc: [], bcc: [], subject: "", text: "" };

function emails(text: string): string[] {
  return text.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean);
}
function safeFilename(name: string): string {
  const clean = name.replace(/[/\\\u0000-\u001f\u007f]+/g, "_").slice(0, 120);
  return clean || "download";
}

const CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'";

export function MailReader({ role, sending = false }: { role: MailRole; sending?: boolean }) {
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [folder, setFolder] = useState("INBOX");
  const [page, setPage] = useState(1);
  const [folderError, setFolderError] = useState("");
  const [list, setList] = useState<MailMessagePage | null>(null);
  const [detail, setDetail] = useState<MailDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [showHtml, setShowHtml] = useState(false);
  const [compose, setCompose] = useState<MailCompose | null>(null);
  const [draftRef, setDraftRef] = useState<MailDraftRef | null>(null);
  const [replyRef, setReplyRef] = useState<MailReplyRef | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmSend, setConfirmSend] = useState(false);
  const [sendKey, setSendKey] = useState("");
  const [notice, setNotice] = useState("");
  const [recipientFields, setRecipientFields] = useState({ to: "", cc: "", bcc: "" });
  const [downloadId, setDownloadId] = useState<string | null>(null);

  const reload = useCallback(() => setRevision((x) => x + 1), []);

  useEffect(() => {
    let live = true;
    mailCenterApi.folders(role).then((res) => {
      if (!live) return;
      setFolders(res.results);
      setFolderError("");
    }).catch((err) => {
      if (live) setFolderError(describeError(err, "Folders are unavailable."));
    });
    return () => { live = false; };
  }, [role, revision]);

  useEffect(() => {
    let live = true;
    mailCenterApi.messages(role, folder, page).then((res) => {
      if (!live) return;
      setList(res);
      setError("");
    }).catch((err) => {
      if (live) {
        setList(null);
        setError(describeError(err, "Messages are unavailable."));
      }
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [role, folder, page, revision]);

  async function openMessage(uid: number) {
    if (!list) return;
    setOpening(true);
    setError("");
    setShowHtml(false);
    setDetail(null);
    try {
      const next = await mailCenterApi.detail(role, uid, folder, list.uid_validity);
      setDetail(next);
    } catch (caught) {
      setError(describeError(caught, "Unable to open message. Refresh its folder."));
    } finally {
      setOpening(false);
    }
  }

  function newCompose(source?: MailDetail, mode?: "reply" | "reply_all") {
    if (!sending || role !== "operations") return;
    const isDraft = source && folder.toLowerCase().includes("draft");
    const replied = source && mode ? {
      uid: source.uid, uid_validity: source.uid_validity, folder: source.folder, mode,
    } satisfies MailReplyRef : null;
    const draft = isDraft ? { uid: source.uid, uid_validity: source.uid_validity } : null;
    const value = source && isDraft
      ? { to: source.to ?? [], cc: source.cc ?? [], bcc: [],
          subject: source.subject, text: source.text }
      : source && replied
        ? { ...INITIAL, subject: source.subject.toLowerCase().startsWith("re:")
            ? source.subject : "Re: " + source.subject }
        : { ...INITIAL };
    setCompose(value);
    setRecipientFields({
      to: value.to.join(", "), cc: value.cc.join(", "), bcc: value.bcc.join(", "),
    });
    setDraftRef(draft);
    setReplyRef(replied);
    setConfirmSend(false);
    setSendKey("");
    setNotice("");
  }

  function outgoing(): MailCompose {
    if (!compose) throw new Error("Nothing to send.");
    return {
      ...compose,
      to: replyRef ? [] : emails(recipientFields.to),
      cc: replyRef ? [] : emails(recipientFields.cc),
      bcc: replyRef ? [] : emails(recipientFields.bcc),
      ...(draftRef ? { draft: draftRef } : {}),
      ...(replyRef ? { reply: replyRef } : {}),
    };
  }

  async function saveDraft() {
    if (!compose || !sending || replyRef || busy) return;
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const result = await mailCenterApi.saveDraft(outgoing());
      setDraftRef({ uid: result.uid, uid_validity: result.uid_validity });
      setNotice(result.previous_removed === false && draftRef
        ? "Saved. The earlier draft may still exist and can be reviewed."
        : "Draft saved in the Operations mailbox.");
      reload();
    } catch (caught) {
      setError(describeError(caught, "Draft was not confirmed saved. Refresh Drafts before retrying."));
    } finally {
      setBusy(false);
    }
  }

  async function sendNow() {
    if (!compose || !sending || busy || !confirmSend || !sendKey) return;
    setBusy(true);
    setError("");
    try {
      const result = await mailCenterApi.send(outgoing(), sendKey);
      setConfirmSend(false);
      setSendKey("");
      setCompose(null);
      setDraftRef(null);
      setReplyRef(null);
      setNotice(result.filed_in_sent
        ? "Message accepted by SMTP and filed in Sent."
        : "Message accepted by SMTP, but the Sent copy could not be filed. Do not resend.");
      reload();
    } catch (caught) {
      // Preserve the key. An ambiguous failure must not become a second send.
      setConfirmSend(false);
      setError(describeError(caught,
        "Sending was not confirmed. Check Sent and delivery status before starting a NEW request."));
    } finally {
      setBusy(false);
    }
  }

  async function deleteDraft() {
    if (!draftRef || !sending || busy || !window.confirm("Delete this saved Operations draft?")) return;
    setBusy(true);
    setError("");
    try {
      await mailCenterApi.deleteDraft(draftRef.uid, draftRef.uid_validity);
      setNotice("Draft deleted.");
      setCompose(null);
      setDraftRef(null);
      setDetail(null);
      reload();
    } catch (caught) {
      setError(describeError(caught, "Could not delete draft. Refresh Drafts and try again."));
    } finally { setBusy(false); }
  }

  async function download(partId: string, filename: string) {
    if (!detail || downloadId) return;
    setDownloadId(partId);
    setError("");
    try {
      const blob = await mailCenterApi.attachment(
        role, detail.uid, partId, detail.folder, detail.uid_validity,
      );
      const objectUrl = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = safeFilename(filename);
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      }
    } catch (caught) {
      setError(describeError(caught, "The attachment could not be downloaded."));
    } finally {
      setDownloadId(null);
    }
  }

  return (
    <section className="space-y-4" aria-label={role === "operations" ? "Operations mailbox" : role + " mailbox"}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>
            {role === "operations" ? "Operations" : role === "security" ? "Security" : "Notifications"}
          </h2>
          <p className="mt-1 text-xs pf-muted">
            {role === "operations" ? OP : "Read access is limited to your approved mailbox role."}
          </p>
        </div>
        <div className="flex gap-2">
          {sending && role === "operations" && (
            <button type="button" className="pf-btn pf-btn-primary" onClick={() => newCompose()}>
              <PenLine className="h-4 w-4" /> Compose
            </button>
          )}
          <button type="button" className="pf-btn pf-btn-ghost" onClick={reload} aria-label="Refresh mail folders">
            <RefreshCw className="h-4 w-4" /> Refresh
          </button>
        </div>
      </div>

      {role !== "operations" && (
        <p className="rounded-lg p-3 text-xs" style={{ background: "var(--pf-warn-soft)", color: "var(--pf-warn)" }}>
          Sensitive mailbox: read only. Access expires and cannot be used to send mail.
        </p>
      )}
      {error && <ErrorNote message={error} />}
      {folderError && <ErrorNote message={folderError} onRetry={reload} />}
      {notice && <SuccessNote message={notice} />}

      {compose && (
        <Card>
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
              {replyRef ? "Reply" : draftRef ? "Edit draft" : "New message"}
            </h3>
            <button className="pf-btn pf-btn-ghost" type="button"
              onClick={() => { setCompose(null); setConfirmSend(false); }}>Close</button>
          </div>
          <p className="mt-2 text-xs pf-muted">From: {OP}</p>
          {replyRef ? (
            <p className="mt-2 text-sm pf-muted">Recipient and thread are derived securely from the original message.</p>
          ) : (
            <div className="mt-3 grid gap-3 md:grid-cols-3">
              {(["to", "cc", "bcc"] as const).map((key) => (
                <label key={key} className="text-xs pf-muted">
                  {key.toUpperCase()} (comma separated)
                  <input className="pf-input mt-1" value={recipientFields[key]}
                    placeholder="person@example.com" disabled={busy}
                    onChange={(e) => setRecipientFields((old) => ({ ...old, [key]: e.target.value }))} />
                </label>
              ))}
            </div>
          )}
          <label className="mt-3 block text-xs pf-muted">Subject
            <input className="pf-input mt-1" maxLength={300} value={compose.subject} disabled={busy}
              onChange={(e) => setCompose((old) => old ? { ...old, subject: e.target.value } : old)} />
          </label>
          <label className="mt-3 block text-xs pf-muted">Plain text message
            <textarea className="pf-input mt-1 min-h-48 w-full" maxLength={50000} value={compose.text} disabled={busy}
              onChange={(e) => setCompose((old) => old ? { ...old, text: e.target.value } : old)} />
          </label>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button type="button" className="pf-btn pf-btn-primary" disabled={busy}
              onClick={() => { setConfirmSend(true); if (!sendKey) setSendKey(crypto.randomUUID()); }}>
              <Send className="h-4 w-4" /> Review and send
            </button>
            {!replyRef && (
              <button type="button" className="pf-btn pf-btn-ghost" onClick={() => void saveDraft()} disabled={busy}>Save draft</button>
            )}
            {draftRef && (
              <button type="button" className="pf-btn pf-btn-danger" disabled={busy} onClick={() => void deleteDraft()}>Delete draft</button>
            )}
          </div>
          {confirmSend && (
            <div className="mt-3 rounded-lg border p-3" style={{ borderColor: "var(--pf-border)" }}>
              <p className="text-sm font-semibold">Confirm SMTP submission</p>
              <p className="mt-1 text-xs pf-muted">
                {replyRef ? "Send to the original message recipients." :
                  "Send to " + [...emails(recipientFields.to), ...emails(recipientFields.cc),
                    ...emails(recipientFields.bcc)].length + " recipient(s)."}
                {" "}Sending may be irreversible. If the request fails, check Sent before retrying.
              </p>
              <div className="mt-3 flex gap-2">
                <button type="button" className="pf-btn pf-btn-primary" disabled={busy}
                  onClick={() => void sendNow()}>{busy ? "Sending…" : "Confirm send"}</button>
                <button type="button" className="pf-btn pf-btn-ghost" disabled={busy}
                  onClick={() => setConfirmSend(false)}>Cancel</button>
              </div>
            </div>
          )}
        </Card>
      )}

      <div className="grid gap-3 lg:grid-cols-[175px_minmax(0,1fr)]">
        <div className="pf-card p-2">
          {folders.length === 0 && !folderError && <p className="p-3 text-xs pf-muted">No folders available.</p>}
          {folders.map((item) => (
            <button key={item.name} type="button"
              className="pf-nav-item mb-1 flex w-full items-center justify-between gap-2 text-left"
              aria-current={item.name === folder ? "page" : undefined}
              onClick={() => {
                setFolder(item.name); setPage(1); setList(null); setDetail(null);
                setLoading(true); setShowHtml(false);
              }}>
              <span className="flex min-w-0 items-center gap-2">
                <Mail className="h-4 w-4 shrink-0" /><span className="truncate">{item.name}</span>
              </span>
              <span className="text-xs pf-muted">{item.unseen}</span>
            </button>
          ))}
        </div>
        <div className="space-y-3">
          <div className="pf-card overflow-hidden">
            <div className="flex items-center justify-between border-b p-3" style={{ borderColor: "var(--pf-border)" }}>
              <span className="text-sm font-semibold">{folder}</span>
              <span className="text-xs pf-muted">{list?.total ?? 0} messages</span>
            </div>
            {loading ? <Spinner label="Loading messages" /> :
              !list || !list.results.length ? <EmptyState title="No messages in this folder" icon={Mail} /> :
                <div className="divide-y" style={{ borderColor: "var(--pf-border)" }}>
                  {list.results.map((item) => (
                    <button key={item.uid} type="button"
                      onClick={() => void openMessage(item.uid)}
                      className="flex w-full flex-col gap-1 px-4 py-3 text-left hover:opacity-75"
                      style={{ borderColor: "var(--pf-border)" }}>
                      <span className="flex items-center justify-between gap-3">
                        <span className="min-w-0 truncate text-sm font-medium" style={{ color: "var(--pf-text)" }}>
                          {!item.seen ? "● " : ""}{item.from_name || item.from_address || "Unknown sender"}
                        </span>
                        <span className="shrink-0 text-xs pf-faint">
                          {item.date ? new Date(item.date).toLocaleDateString() : ""}
                        </span>
                      </span>
                      <span className="truncate text-xs pf-muted">{item.subject || "(No subject)"}</span>
                    </button>
                  ))}
                </div>
            }
            {list && (
              <div className="flex items-center justify-between border-t p-3" style={{ borderColor: "var(--pf-border)" }}>
                <button type="button" className="pf-btn pf-btn-ghost" disabled={page <= 1}
                  onClick={() => { setPage((x) => x - 1); setDetail(null); setLoading(true); }}>
                  <ChevronLeft className="h-4 w-4" /> Previous
                </button>
                <span className="text-xs pf-muted">Page {page}</span>
                <button type="button" className="pf-btn pf-btn-ghost" disabled={!list.has_next}
                  onClick={() => { setPage((x) => x + 1); setDetail(null); setLoading(true); }}>
                  Next <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>
          {opening && <Spinner label="Opening message" />}
          {detail && !opening && (
            <Card>
              <h3 className="break-words text-sm font-semibold" style={{ color: "var(--pf-text)" }}>{detail.subject || "(No subject)"}</h3>
              <div className="mt-3 space-y-1 text-xs pf-muted">
                <p><b>From:</b> {detail.from.name} &lt;{detail.from.address}&gt;</p>
                <p><b>To:</b> {detail.to.join(", ") || "—"}</p>
                {detail.cc.length > 0 && <p><b>CC:</b> {detail.cc.join(", ")}</p>}
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button type="button" className="pf-btn pf-btn-ghost"
                  onClick={() => setShowHtml(false)}>Plain text</button>
                {detail.html && <button type="button" className="pf-btn pf-btn-ghost"
                  onClick={() => setShowHtml(true)}>Safe HTML</button>}
                {sending && role === "operations" && (
                  <>
                    <button type="button" className="pf-btn pf-btn-ghost" onClick={() => newCompose(detail, "reply")}>Reply</button>
                    <button type="button" className="pf-btn pf-btn-ghost" onClick={() => newCompose(detail, "reply_all")}>Reply all</button>
                    {folder.toLowerCase().includes("draft") && (
                      <button type="button" className="pf-btn pf-btn-ghost" onClick={() => newCompose(detail)}>Edit draft</button>
                    )}
                  </>
                )}
              </div>
              <div className="mt-3 rounded-md p-3" style={{ background: "var(--pf-surface-2)" }}>
                {showHtml && detail.html ? (
                  <iframe title="Isolated email content" sandbox="" referrerPolicy="no-referrer"
                    srcDoc={'<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="' + CSP + '"></head><body>' + detail.html + '</body></html>'}
                    className="h-72 w-full border-0" />
                ) : (
                  <pre className="whitespace-pre-wrap break-words text-sm" style={{ color: "var(--pf-text)", fontFamily: "inherit" }}>
                    {detail.text || "(No plain text content)"}
                  </pre>
                )}
              </div>
              {detail.remote_images_blocked && <p className="mt-2 text-xs pf-muted"><ShieldCheck className="mr-1 inline h-3.5 w-3.5" />Remote images and trackers blocked.</p>}
              {!!detail.attachments.length && (
                <div className="mt-4 space-y-2">
                  <h4 className="text-xs font-semibold">Attachments</h4>
                  {detail.attachments.map((item) => (
                    <div key={item.part_id} className="flex items-center justify-between gap-2 border-t py-2" style={{ borderColor: "var(--pf-border)" }}>
                      <span className="flex min-w-0 items-center gap-2 text-xs pf-muted">
                        <FileText className="h-4 w-4 shrink-0" />
                        <span className="truncate">{item.filename || "Attachment"} ({Math.ceil(item.size / 1024)} KB)</span>
                      </span>
                      <button className="pf-btn pf-btn-ghost" type="button"
                        disabled={!item.downloadable || !!downloadId}
                        onClick={() => void download(item.part_id, item.filename)}>
                        <Download className="h-4 w-4" /> {item.downloadable ? "Download" : "Too large"}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </section>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Globe2,
  LoaderCircle,
  Mail,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalCopyButton,
  PortalNotice,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

type Surface = "hub" | "postbox";

interface CustomHostname {
  id: string;
  hostname: string;
  surface: Surface;
  dns_status: "pending" | "verified" | "failed";
  dns_verified_at: string | null;
  dns_last_checked_at: string | null;
  provisioning_status:
    | "unprovisioned"
    | "provisioning"
    | "ready"
    | "active"
    | "error"
    | "deactivating"
    | "inactive";
  certificate_status:
    | "not_requested"
    | "issuing"
    | "active"
    | "error"
    | "revoked";
  last_error: string;
  activated_at: string | null;
  deactivated_at: string | null;
  cname_record_type: "CNAME";
  cname_target: string;
  cname_zone: string | null;
  cname_host: string | null;
  setup_instructions: string;
  created_at: string;
  updated_at: string;
}

const SURFACES: Array<{
  key: Surface;
  title: string;
  description: string;
  placeholder: string;
  icon: typeof Globe2;
}> = [
  {
    key: "hub",
    title: "MateMail Hub",
    description: "Give organization admins a branded URL for mailbox and domain administration.",
    placeholder: "manage.company.com",
    icon: Globe2,
  },
  {
    key: "postbox",
    title: "PostBox",
    description: "Give mailbox users a branded URL for reading and sending email.",
    placeholder: "mail.company.com",
    icon: Mail,
  },
];

function pretty(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function errorText(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;
  const body = data as Record<string, unknown>;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.hostname) && typeof body.hostname[0] === "string") {
    return body.hostname[0];
  }
  return fallback;
}

function needsPolling(row: CustomHostname) {
  return (
    row.provisioning_status === "provisioning" ||
    row.provisioning_status === "ready" ||
    row.provisioning_status === "deactivating" ||
    (row.dns_status === "verified" &&
      row.provisioning_status === "unprovisioned")
  );
}

function CustomHostnamePanel({
  row,
  surface,
  canEdit,
  input,
  onInput,
  busy,
  onAdd,
  onVerify,
  onRemove,
}: {
  row?: CustomHostname;
  surface: (typeof SURFACES)[number];
  canEdit: boolean;
  input: string;
  onInput: (value: string) => void;
  busy: string;
  onAdd: () => void;
  onVerify: () => void;
  onRemove: () => void;
}) {
  const Icon = surface.icon;
  const isBusy = Boolean(busy);
  // DNS providers often append their managed zone; only copy an API-confirmed
  // relative label, never guess the zone from a public suffix.
  const copyableHost = row?.cname_host ?? null;

  return (
    <article className="portal-custom-host">
      <div className="portal-custom-host-head">
        <span className="portal-custom-host-icon">
          <Icon className="h-4 w-4" />
        </span>
        <div>
          <h3>{surface.title}</h3>
          <p>{surface.description}</p>
        </div>
      </div>

      {!row ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onAdd();
          }}
          className="portal-custom-host-add"
        >
          <div className="portal-field">
            <label htmlFor={"custom-host-" + surface.key}>Custom hostname</label>
            <input
              id={"custom-host-" + surface.key}
              type="text"
              inputMode="url"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              placeholder={surface.placeholder}
              value={input}
              disabled={!canEdit || isBusy}
              onChange={(event) => onInput(event.target.value)}
            />
            <div className="portal-field-hint">
              Enter only the hostname. Do not include <code>https://</code> or a path.
            </div>
          </div>
          {canEdit && (
            <div className="portal-detail-actions">
              <PortalButton type="submit" disabled={isBusy || !input.trim()}>
                {busy === "adding" && <LoaderCircle className="h-4 w-4 animate-spin" />}
                Add hostname
              </PortalButton>
            </div>
          )}
        </form>
      ) : (
        <div className="portal-custom-host-configured">
          <div className="portal-custom-host-titleline">
            <div>
              <span>Custom hostname</span>
              <strong>{row.hostname}</strong>
            </div>
            <PortalStatus
              value={
                row.provisioning_status === "active"
                  ? "Active"
                  : row.provisioning_status === "deactivating"
                    ? "Removing"
                    : pretty(row.provisioning_status)
              }
            />
          </div>

          <div className="portal-custom-host-statuses">
            <div>
              <span>DNS</span>
              <PortalStatus value={pretty(row.dns_status)} />
            </div>
            <div>
              <span>HTTPS</span>
              <PortalStatus value={pretty(row.certificate_status)} />
            </div>
          </div>

          {row.provisioning_status !== "deactivating" && (
            <div className="portal-custom-host-dns">
              <div className="portal-custom-host-dns-head">
                <div>
                  <strong>Add this DNS record</strong>
                  <span>At the DNS provider that manages {row.cname_zone ?? row.hostname}.</span>
                </div>
                {row.dns_status === "verified" && (
                  <span className="portal-custom-host-verified">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Verified
                  </span>
                )}
              </div>

              <div className="portal-custom-host-record">
                <div>
                  <span>Type</span>
                  <div className="portal-code-field portal-dns-type-value"><code>{row.cname_record_type}</code></div>
                </div>
                <div>
                  <span>Name / Host{copyableHost ? " (copy this)" : ""}</span>
                  <div className="portal-code-field">
                    <code>{copyableHost ?? "Check your DNS zone"}</code>
                    {copyableHost && (
                      <PortalCopyButton value={copyableHost} label="Copy relative DNS host" />
                    )}
                  </div>
                </div>
                <div>
                  <span>Target / Value</span>
                  <div className="portal-code-field">
                    <code>{row.cname_target}</code>
                    <PortalCopyButton value={row.cname_target} label="Copy CNAME target" />
                  </div>
                </div>
              </div>
              <p className="portal-dns-provider-note">
                {row.cname_zone ? (
                  <>
                    Most providers append <strong>{row.cname_zone}</strong> automatically.
                    Copy <code>{row.cname_host}</code> into Host/Name, not the full
                    <code> {row.hostname}</code>. If your provider explicitly requests
                    a full hostname, use <code>{row.hostname}</code>.
                  </>
                ) : (
                  <>
                    Full hostname: <code>{row.hostname}</code>. This organization has
                    no matching verified DNS zone, so MateMail cannot safely
                    suggest a shortened name. Check your provider&apos;s zone:
                    enter the portion before that zone in Host/Name, or the full
                    hostname only if the provider explicitly requires it.
                  </>
                )}
              </p>
            </div>
          )}

          {row.dns_status !== "verified" && row.provisioning_status !== "deactivating" && (
            <PortalNotice tone={row.dns_status === "failed" ? "warn" : "info"}>
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{row.setup_instructions}</span>
            </PortalNotice>
          )}

          {row.dns_status === "verified" &&
            ["unprovisioned", "provisioning", "ready"].includes(row.provisioning_status) && (
              <PortalNotice tone="info">
                <LoaderCircle className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
                <span>
                  DNS is verified. MateMail is preparing HTTPS and the final route automatically.
                  You can leave this page; status will keep updating.
                </span>
              </PortalNotice>
            )}

          {row.provisioning_status === "active" && (
            <PortalNotice tone="success">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                <strong>{row.hostname}</strong> is live with HTTPS. The browser stays on your
                custom hostname.
              </span>
            </PortalNotice>
          )}

          {row.provisioning_status === "error" && (
            <PortalNotice tone="danger">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                {row.last_error ||
                  "HTTPS setup could not be completed. Confirm the CNAME and verify again."}
              </span>
            </PortalNotice>
          )}

          {row.provisioning_status === "deactivating" && (
            <PortalNotice tone="info">
              <LoaderCircle className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
              <span>
                Removing this hostname from the edge and retiring its certificate. It is
                already no longer authorized for organization access.
              </span>
            </PortalNotice>
          )}

          <div className="portal-detail-actions">
            {canEdit &&
              row.provisioning_status !== "deactivating" &&
              row.provisioning_status !== "active" && (
                <PortalButton
                  type="button"
                  variant="secondary"
                  disabled={isBusy}
                  onClick={onVerify}
                >
                  {busy === "verifying" ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  {row.dns_status === "verified" ? "Check again" : "Verify DNS"}
                </PortalButton>
              )}

            {row.provisioning_status === "active" && (
              <a
                className="portal-button secondary"
                href={"https://" + row.hostname}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink className="h-4 w-4" />
                Open URL
              </a>
            )}

            {canEdit && row.provisioning_status !== "deactivating" && (
              <PortalButton
                type="button"
                variant="danger"
                disabled={isBusy}
                onClick={onRemove}
              >
                {busy === "removing" ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4" />
                )}
                Remove
              </PortalButton>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

export function CustomHostnamesSettings({ canEdit }: { canEdit: boolean }) {
  const [rows, setRows] = useState<CustomHostname[]>([]);
  const [inputs, setInputs] = useState<Record<Surface, string>>({
    hub: "",
    postbox: "",
  });
  const [busy, setBusy] = useState<Record<Surface, string>>({
    hub: "",
    postbox: "",
  });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionSuccess, setActionSuccess] = useState("");

  const bySurface = useMemo(() => {
    return new Map(rows.map((row) => [row.surface, row]));
  }, [rows]);

  const fetchRows = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const response = await apiRequest("/api/custom-hostnames/");
      const data = await response.json().catch(() => null);
      if (!response.ok || !Array.isArray(data)) {
        setLoadError(errorText(data, "Custom URLs could not be loaded."));
        return;
      }
      setRows(data);
      setLoadError("");
    } catch {
      setLoadError("Custom URLs could not be loaded.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchRows(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [fetchRows]);

  useEffect(() => {
    if (!rows.some(needsPolling)) return;
    const timer = window.setInterval(() => {
      void fetchRows(false);
    }, 6000);
    return () => window.clearInterval(timer);
  }, [fetchRows, rows]);

  function setSurfaceBusy(surface: Surface, value: string) {
    setBusy((current) => ({ ...current, [surface]: value }));
  }

  async function add(surface: Surface) {
    const hostname = inputs[surface].trim();
    if (!hostname) return;
    setActionError("");
    setActionSuccess("");
    setSurfaceBusy(surface, "adding");
    try {
      const response = await apiRequest("/api/custom-hostnames/", {
        method: "POST",
        body: JSON.stringify({ hostname, surface }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || !data) {
        setActionError(errorText(data, "The custom hostname could not be added."));
        return;
      }
      setRows((current) => [...current.filter((row) => row.surface !== surface), data]);
      setInputs((current) => ({ ...current, [surface]: "" }));
      setActionSuccess(
        data.hostname + " was added. Create the CNAME shown below, then verify DNS."
      );
    } catch {
      setActionError("The custom hostname could not be added.");
    } finally {
      setSurfaceBusy(surface, "");
    }
  }

  async function verify(row: CustomHostname) {
    setActionError("");
    setActionSuccess("");
    setSurfaceBusy(row.surface, "verifying");
    try {
      const response = await apiRequest(
        "/api/custom-hostnames/" + row.id + "/verify/",
        { method: "POST", body: JSON.stringify({}) }
      );
      const data = await response.json().catch(() => null);
      const updated = data?.custom_hostname as CustomHostname | undefined;
      if (updated) {
        setRows((current) =>
          current.map((item) => (item.id === updated.id ? updated : item))
        );
      }
      if (!response.ok) {
        setActionError(errorText(data, "DNS verification did not pass yet."));
        return;
      }
      setActionSuccess(
        "DNS verified. MateMail will provision HTTPS and activate the URL automatically."
      );
      window.setTimeout(() => void fetchRows(false), 1500);
    } catch {
      setActionError("DNS verification could not be completed.");
    } finally {
      setSurfaceBusy(row.surface, "");
    }
  }

  async function remove(row: CustomHostname) {
    const confirmed = window.confirm(
      "Remove " + row.hostname + " from " +
        (row.surface === "hub" ? "MateMail Hub" : "PostBox") +
        "? The custom URL will stop being authorized immediately."
    );
    if (!confirmed) return;

    setActionError("");
    setActionSuccess("");
    setSurfaceBusy(row.surface, "removing");
    try {
      const response = await apiRequest("/api/custom-hostnames/" + row.id + "/", {
        method: "DELETE",
      });
      const data = response.status === 204
        ? null
        : await response.json().catch(() => null);

      if (!response.ok) {
        setActionError(errorText(data, "The custom hostname could not be removed."));
        return;
      }

      if (response.status === 202 && data) {
        setRows((current) =>
          current.map((item) => (item.id === data.id ? data : item))
        );
        setActionSuccess(
          row.hostname + " is being removed. It is already blocked for organization access."
        );
      } else {
        setRows((current) => current.filter((item) => item.id !== row.id));
        setActionSuccess(row.hostname + " was removed.");
      }
    } catch {
      setActionError("The custom hostname could not be removed.");
    } finally {
      setSurfaceBusy(row.surface, "");
    }
  }

  return (
    <PortalCard
      className="mt-5"
      title="Custom access URLs"
      subtitle="Use customer-owned subdomains for MateMail Hub and PostBox. Mail delivery DNS is separate."
      action={
        <PortalButton
          type="button"
          variant="secondary"
          disabled={loading}
          onClick={() => void fetchRows(true)}
        >
          <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          Refresh status
        </PortalButton>
      }
    >
      <div className="portal-custom-host-intro">
        <div>
          <strong>One CNAME per URL</strong>
          <span>
            You choose the hostname. MateMail verifies the CNAME, provisions HTTPS and
            activates the route automatically. Existing verified URLs that point to
            custom.matemail.online remain supported during migration; do not delete
            them until custom.matemail.pro is published and verified.
          </span>
        </div>
        <div>
          <strong>No mail-flow change</strong>
          <span>
            These are browser URLs only. MX, SPF, DKIM and DMARC stay exactly where
            your mail-domain setup manages them.
          </span>
        </div>
      </div>

      {loadError && (
        <div className="mb-4">
          <PortalNotice tone="danger">{loadError}</PortalNotice>
        </div>
      )}

      {actionError && (
        <div className="mb-4">
          <PortalNotice tone="danger">{actionError}</PortalNotice>
        </div>
      )}

      {actionSuccess && (
        <div className="mb-4">
          <PortalNotice tone="success">{actionSuccess}</PortalNotice>
        </div>
      )}

      {loading && rows.length === 0 ? (
        <div className="portal-custom-host-grid">
          <PortalSkeleton className="h-[280px] w-full" />
          <PortalSkeleton className="h-[280px] w-full" />
        </div>
      ) : (
        <div className="portal-custom-host-grid">
          {SURFACES.map((surface) => (
            <CustomHostnamePanel
              key={surface.key}
              row={bySurface.get(surface.key)}
              surface={surface}
              canEdit={canEdit}
              input={inputs[surface.key]}
              onInput={(value) =>
                setInputs((current) => ({ ...current, [surface.key]: value }))
              }
              busy={busy[surface.key]}
              onAdd={() => void add(surface.key)}
              onVerify={() => {
                const row = bySurface.get(surface.key);
                if (row) void verify(row);
              }}
              onRemove={() => {
                const row = bySurface.get(surface.key);
                if (row) void remove(row);
              }}
            />
          ))}
        </div>
      )}

      {!canEdit && (
        <div className="mt-4">
          <PortalNotice tone="info">
            Only workspace Owners and Admins can add, verify or remove custom URLs.
          </PortalNotice>
        </div>
      )}
    </PortalCard>
  );
}

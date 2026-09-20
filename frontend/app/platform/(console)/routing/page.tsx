"use client";

/**
 * Aliases and forwarding, platform-wide.
 *
 * Oversight, not editing. Creating and changing these belongs to the
 * MateMail Workspace; what a platform operator needs is the cross-tenant
 * view an abuse report starts from, and one button to stop a rule that is
 * being used to relay.
 *
 * Destinations that leave the platform are flagged, because that is the shape
 * both mail-relay abuse and mailbox exfiltration take.
 */
import { useCallback, useState } from "react";
import Link from "next/link";
import { Ban, ExternalLink, Play } from "lucide-react";

import { platformApi, type PlatformRoute } from "@/lib/platform-api";
import { describeError } from "@/contexts/platform-auth-context";
import {
  Badge,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorNote,
  FilterSelect,
  PageHeader,
  Pagination,
  SearchInput,
  Spinner,
  SuccessNote,
  Toolbar,
  formatDateTime,
  useDebounced,
  useListData,
} from "@/components/platform/ui";

const KIND_OPTIONS = [
  { value: "aliases", label: "Aliases" },
  { value: "forwarding", label: "Forwarding" },
];

const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "disabled", label: "Disabled" },
];

type Target = { route: PlatformRoute; disable: boolean } | null;

export default function RoutingPage() {
  const [kind, setKind] = useState("aliases");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [externalOnly, setExternalOnly] = useState(false);
  const [page, setPage] = useState(1);
  const term = useDebounced(search);

  const { data, error, loading, reload } = useListData(
    () =>
      kind === "aliases"
        ? platformApi.aliases({ search: term, status, page })
        : platformApi.forwarding({
            search: term,
            status,
            page,
            external: externalOnly ? "true" : "",
          }),
    [kind, term, status, externalOnly, page],
  );

  const [target, setTarget] = useState<Target>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async (reason: string) => {
      if (!target) return;
      setBusy(true);
      setActionError(null);
      try {
        if (target.route.kind === "alias") {
          await platformApi.setAliasDisabled(target.route.id, target.disable, reason);
        } else {
          await platformApi.setForwardingDisabled(target.route.id, target.disable, reason);
        }
        setNotice(
          `${target.route.source ?? "Rule"} ${target.disable ? "disabled" : "re-enabled"}.`,
        );
        setTarget(null);
        reload();
      } catch (caught) {
        setActionError(describeError(caught, "The action could not be completed."));
      } finally {
        setBusy(false);
      }
    },
    [target, reload],
  );

  return (
    <>
      <PageHeader
        title="Aliases & Forwarding"
        description="Cross-organization oversight. Day-to-day changes belong to the MateMail Workspace."
      />

      <Toolbar>
        <FilterSelect
          label="Type"
          value={kind}
          onChange={(v) => { setKind(v); setPage(1); }}
          options={KIND_OPTIONS}
        />
        <SearchInput
          value={search}
          onChange={(v) => { setSearch(v); setPage(1); }}
          placeholder="Search source, destination, organization…"
        />
        <FilterSelect
          label="Status"
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          options={STATUS_OPTIONS}
        />
        {kind === "forwarding" && (
          <label className="flex items-center gap-1.5 text-xs pf-muted">
            <input
              type="checkbox"
              checked={externalOnly}
              onChange={(event) => {
                setExternalOnly(event.target.checked);
                setPage(1);
              }}
            />
            External destinations only
          </label>
        )}
      </Toolbar>

      {notice && (
        <div className="mb-3">
          <SuccessNote message={notice} />
        </div>
      )}
      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.results.length === 0 ? (
        <div className="pf-card">
          <EmptyState title="Nothing matches" detail="Adjust the filters above." />
        </div>
      ) : (
        <>
          <DataTable
            columns={["Source", "Destination", "Organization", "Status", "Created", ""]}
          >
            {data.results.map((route) => {
              const disabled = route.status === "disabled";
              return (
                <tr key={route.id}>
                  <td className="font-medium" style={{ color: "var(--pf-text)" }}>
                    {route.source ?? "—"}
                  </td>
                  <td>
                    <span style={{ color: "var(--pf-text)" }}>{route.destination ?? "—"}</span>
                    {route.external_destination && (
                      <span className="ml-1.5 inline-flex">
                        <Badge tone="warn">
                          <ExternalLink className="h-3 w-3" aria-hidden="true" />
                          External
                        </Badge>
                      </span>
                    )}
                    {route.kind === "forwarding" && route.keep_copy === false && (
                      <div className="text-xs pf-faint">no local copy kept</div>
                    )}
                  </td>
                  <td>
                    {route.tenant ? (
                      <Link
                        href={`/platform/organizations/${route.tenant.id}`}
                        style={{ color: "var(--pf-accent-text)" }}
                      >
                        {route.tenant.name}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <Badge status={route.status} />
                  </td>
                  <td className="pf-muted whitespace-nowrap">
                    {formatDateTime(route.created_at)}
                  </td>
                  <td>
                    <button
                      type="button"
                      className={`pf-btn ${disabled ? "pf-btn-ghost" : "pf-btn-danger"}`}
                      onClick={() => {
                        setNotice(null);
                        setTarget({ route, disable: !disabled });
                      }}
                    >
                      {disabled ? (
                        <>
                          <Play className="h-3.5 w-3.5" aria-hidden="true" />
                          Enable
                        </>
                      ) : (
                        <>
                          <Ban className="h-3.5 w-3.5" aria-hidden="true" />
                          Disable
                        </>
                      )}
                    </button>
                  </td>
                </tr>
              );
            })}
          </DataTable>
          <Pagination
            page={data.page}
            pageSize={data.page_size}
            total={data.total}
            hasNext={data.has_next}
            onPage={setPage}
          />
        </>
      )}

      <ConfirmDialog
        open={target !== null}
        title={target?.disable ? "Disable rule" : "Re-enable rule"}
        body={
          target?.disable ? (
            <>
              Mail to <strong>{target.route.source}</strong> will stop being
              delivered to <strong>{target.route.destination}</strong>. The
              organization can see the rule but the platform has disabled it.
            </>
          ) : (
            <>
              <strong>{target?.route.source}</strong> will resume delivering to{" "}
              <strong>{target?.route.destination}</strong>.
            </>
          )
        }
        confirmLabel={target?.disable ? "Disable" : "Enable"}
        destructive={target?.disable}
        busy={busy}
        error={actionError}
        onConfirm={run}
        onCancel={() => {
          setTarget(null);
          setActionError(null);
        }}
      />
    </>
  );
}

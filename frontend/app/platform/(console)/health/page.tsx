"use client";

/**
 * System health.
 *
 * The page's one rule: a component that was not contacted does not get a green
 * tick. PostgreSQL, Redis, the Mail Engine API and the Celery workers are
 * genuinely probed; the mail daemons run outside this application and are
 * shown as "unknown" with the place an operator can actually check them.
 *
 * A console that guessed here would be worse than no console, because the tick
 * is what stops somebody looking.
 */
import { platformApi } from "@/lib/platform-api";
import {
  Badge,
  Card,
  ErrorNote,
  PageHeader,
  Spinner,
  UnknownNote,
  formatDateTime,
  useListData,
} from "@/components/platform/ui";

const TONES = {
  ok: "ok",
  error: "danger",
  unknown: "warn",
} as const;

const LABELS = {
  ok: "Healthy",
  error: "Failing",
  unknown: "Not checkable",
} as const;

export default function HealthPage() {
  const { data, error, loading, reload } = useListData(() => platformApi.health(), []);

  const unknownCount = (data?.components ?? []).filter((c) => c.status === "unknown").length;

  return (
    <>
      <PageHeader
        title="System Health"
        description="Live checks of the components this application can reach."
        actions={
          <button type="button" className="pf-btn pf-btn-ghost" onClick={reload}>
            Re-check
          </button>
        }
      />

      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner label="Checking components" />
      ) : !data ? null : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Badge tone={data.overall === "ok" ? "ok" : "danger"}>
              {data.overall === "ok" ? "All checked components healthy" : "Attention needed"}
            </Badge>
            <span className="text-xs pf-faint">
              Checked {formatDateTime(data.checked_at)}
            </span>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.components.map((component) => (
              <Card key={component.name}>
                <div className="mb-2 flex items-start justify-between gap-2">
                  <h2 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
                    {component.name}
                  </h2>
                  <Badge tone={TONES[component.status]}>{LABELS[component.status]}</Badge>
                </div>
                {component.latency_ms !== null && (
                  <p className="pf-num mb-1 text-xs pf-muted">{component.latency_ms} ms</p>
                )}
                <p className="text-xs pf-muted">{component.detail}</p>
              </Card>
            ))}
          </div>

          {unknownCount > 0 && (
            <div className="mt-4">
              <UnknownNote message={data.note} />
            </div>
          )}
        </>
      )}
    </>
  );
}

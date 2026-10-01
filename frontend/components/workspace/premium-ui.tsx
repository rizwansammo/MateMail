import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { Inbox } from "lucide-react";

export function PortalPageHeading({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="portal-page-heading">
      <div>
        {eyebrow && (
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-[var(--portal-faint)]">
            {eyebrow}
          </div>
        )}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function PortalCard({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={"portal-card " + className}>
      {(title || subtitle || action) && (
        <div className="portal-card-header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="portal-card-body">{children}</div>
    </section>
  );
}

export function PortalButton({
  variant = "primary",
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
}) {
  return (
    <button
      className={"portal-button " + variant + (className ? " " + className : "")}
      {...props}
    >
      {children}
    </button>
  );
}

export function PortalStatus({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const tone =
    /(active|verified|completed|success|ready|released|protected)/.test(normalized)
      ? "good"
      : /(pending|warning|attention|retrying|deferred|quarantined|trial)/.test(normalized)
        ? "warn"
        : /(failed|cancelled|canceled|revoked|incorrect|suspended|rejected)/.test(normalized)
          ? "bad"
          : /(running|queued|checking|processing)/.test(normalized)
            ? "info"
            : "";

  return <span className={"portal-status " + tone}>{value}</span>;
}

export function PortalEmptyState({
  title = "Nothing here yet",
  description = "Items will appear here when they become available.",
  action,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="portal-empty">
      <div>
        <span className="portal-empty-icon">
          <Inbox className="h-5 w-5" />
        </span>
        <h3>{title}</h3>
        <p>{description}</p>
        {action && <div className="mt-4 flex justify-center">{action}</div>}
      </div>
    </div>
  );
}

export function PortalSkeleton({
  className = "",
  style,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={"portal-skeleton " + className}
      style={style}
      aria-hidden="true"
      {...props}
    />
  );
}

export function PortalTableFrame({ children }: { children: ReactNode }) {
  return <div className="portal-card overflow-hidden">{children}</div>;
}

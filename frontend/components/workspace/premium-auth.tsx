"use client";

import Link from "next/link";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import {
  Building2,
  Check,
  Globe2,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

export function AuthBrand({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="auth-brand" aria-label="MateMail home">
      <BrandMark size={35} className="auth-brand-mark" preload />
      <span className="auth-brand-word">MateMail</span>
    </Link>
  );
}

export function PremiumAuthShell({
  title,
  description,
  icon,
  children,
  topNote,
}: {
  title: string;
  description: ReactNode;
  icon: ReactNode;
  children: ReactNode;
  topNote?: ReactNode;
}) {
  return (
    <div className="auth-premium">
      <div className="auth-layout">
        <aside className="auth-story">
          <AuthBrand />
          <div className="auth-story-body">
            <div className="auth-eyebrow">
              {IS_NETAMATE_EMAIL ? "SECURE MAIL ADMINISTRATION" : "BUILT FOR YOUR ORGANIZATION"}
            </div>
            <h1>
              Great email.
              <br />
              Thoughtfully
              <br />
              managed.
            </h1>
            <p>
              Your domains, your people, your peace of mind.
              <br />
              One place to bring it all together.
            </p>
            <div className="auth-feature-list">
              <span><Globe2 className="h-5 w-5" />Your brand, on every email</span>
              <span><Building2 className="h-5 w-5" />Your whole team, connected</span>
              <span><ShieldCheck className="h-5 w-5" />Security at every step</span>
            </div>
            <div className="auth-signal">
              <span className="auth-signal-icon"><Check className="h-4 w-4" /></span>
              <div>
                <strong>Everything, in its right place.</strong>
                <small>A calmer way to manage business email.</small>
              </div>
            </div>
          </div>
          <footer className="auth-story-footer">
            <span>MateMail</span>
            <span>{IS_NETAMATE_EMAIL ? "MailAdmin" : "Organization Portal"}</span>
          </footer>
        </aside>

        <main className="auth-main">
          <div className="auth-top">
            <AuthBrand />
            <span>{topNote ?? (IS_NETAMATE_EMAIL ? "Private administration" : "Secure organization access")}</span>
          </div>
          <div className="auth-form-wrap">
            <span className="auth-form-icon">{icon}</span>
            <h1>{title}</h1>
            <div className="auth-form-description">{description}</div>
            {children}
          </div>
          <footer className="auth-main-footer">
            Secure administration, made simple.
            <ShieldCheck className="h-4 w-4" />
          </footer>
        </main>
      </div>
    </div>
  );
}

export function AuthField({
  label,
  hint,
  error,
  children,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string;
  children?: ReactNode;
}) {
  return (
    <label className="auth-field">
      <span className="auth-field-label">{label}</span>
      {children ?? <input className="auth-input" {...props} />}
      {hint && <small className="auth-hint">{hint}</small>}
      {error && <small className="auth-field-error">{error}</small>}
    </label>
  );
}

export function AuthButton({
  secondary = false,
  loading = false,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  secondary?: boolean;
  loading?: boolean;
}) {
  return (
    <button
      {...props}
      className={"auth-button" + (secondary ? " secondary" : "") + (props.className ? " " + props.className : "")}
      disabled={loading || props.disabled}
    >
      {loading && <Loader2 className="auth-loading-spin h-4 w-4" aria-hidden="true" />}
      {children}
    </button>
  );
}

export function AuthError({ children }: { children: ReactNode }) {
  return <div className="auth-error" role="alert">{children}</div>;
}

export function AuthNotice({ children }: { children: ReactNode }) {
  return <div className="auth-notice">{children}</div>;
}

export function AuthSuccess({ children }: { children: ReactNode }) {
  return <div className="auth-success">{children}</div>;
}

export function workspaceInitials(value: string) {
  return value
    .split(/[\\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function titleCase(value?: string | null) {
  if (!value) return "";
  return value.replaceAll("_", " ").replace(/\\b\\w/g, (letter) => letter.toUpperCase());
}

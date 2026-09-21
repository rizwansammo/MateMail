"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  AtSign,
  Check,
  Command,
  Database,
  Mail,
  Menu,
  Monitor,
  Moon,
  Network,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import Image from "next/image";
import { api, ApiError } from "@/lib/api";

type ThemeChoice = "light" | "dark" | "system";

const WORKSPACE_URL = "https://portal.matemail.online/login";
const POSTBOX_URL = "https://postbox.matemail.online/login";
const THEME_STORAGE_KEY = "matemail.public.theme";

const themeOptions: Array<{
  value: ThemeChoice;
  label: string;
  Icon: typeof Sun;
}> = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "system", label: "System", Icon: Monitor },
  { value: "dark", label: "Dark", Icon: Moon },
];

const capabilities = [
  {
    Icon: AtSign,
    title: "Custom-domain business email",
    copy: "Professional email using a domain your organization owns.",
  },
  {
    Icon: Command,
    title: "Organization administration",
    copy: "Manage domains, mailboxes, aliases and forwarding through Workspace.",
  },
  {
    Icon: Mail,
    title: "MateMail PostBox",
    copy: "A focused webmail experience for everyday business email.",
  },
  {
    Icon: ShieldCheck,
    title: "Mail security",
    copy: "TLS-protected access, spam filtering, domain authentication and sender protection.",
  },
  {
    Icon: Network,
    title: "DNS guidance",
    copy: "Clear status and guidance around records required for mail readiness.",
  },
  {
    Icon: Database,
    title: "Mailbox controls",
    copy: "Manage account state, quotas and appropriate organization-level settings.",
  },
];

const faqs = [
  [
    "Can I use my own domain?",
    "Yes. MateMail is currently designed around customer-owned business domains.",
  ],
  [
    "Is MateMail a free public email provider?",
    "No. MateMail is focused on business email rather than consumer mailbox signup.",
  ],
  [
    "What is MateMail Workspace?",
    "MateMail Workspace is the administration area for organization owners and administrators.",
  ],
  [
    "What is MateMail PostBox?",
    "MateMail PostBox is the webmail interface used by mailbox users to read, send and manage email.",
  ],
  [
    "Can I create an account immediately?",
    "No. Private Beta access currently requires approval before an organization is activated.",
  ],
  [
    "Is MateMail currently paid?",
    "During the current Private Beta, approved access may be provided without charge. Commercial plans may be introduced later.",
  ],
] as const;

function resolveTheme(choice: ThemeChoice): "light" | "dark" {
  if (choice !== "system") return choice;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function setDocumentTheme(choice: ThemeChoice) {
  const root = document.documentElement;
  root.dataset.mmHomeTheme = choice;
  root.dataset.mmHomeResolved = resolveTheme(choice);
}

export function MateMailPublicHome() {
  const [theme, setTheme] = useState<ThemeChoice>("system");
  const [menuOpen, setMenuOpen] = useState(false);
  const [applicationOpen, setApplicationOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [formError, setFormError] = useState(false);
  const [applicationError, setApplicationError] = useState("");
  const [applicationBusy, setApplicationBusy] = useState(false);
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    let stored: ThemeChoice = "system";
    try {
      const candidate = window.localStorage.getItem(THEME_STORAGE_KEY);
      if (candidate === "light" || candidate === "dark" || candidate === "system") {
        stored = candidate;
      }
    } catch {
      // A theme preference should never prevent the public page from rendering.
    }

    setTheme(stored);
    setDocumentTheme(stored);

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => {
      let current: ThemeChoice = "system";
      try {
        const candidate = window.localStorage.getItem(THEME_STORAGE_KEY);
        if (candidate === "light" || candidate === "dark" || candidate === "system") {
          current = candidate;
        }
      } catch {
        current = stored;
      }
      if (current === "system") setDocumentTheme("system");
    };
    media.addEventListener?.("change", onSystemChange);

    return () => {
      media.removeEventListener?.("change", onSystemChange);
      delete document.documentElement.dataset.mmHomeTheme;
      delete document.documentElement.dataset.mmHomeResolved;
    };
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (applicationOpen && !dialog.open) {
      dialog.showModal();
    }
    if (!applicationOpen && dialog.open) {
      dialog.close();
    }
  }, [applicationOpen]);

  function chooseTheme(next: ThemeChoice) {
    setTheme(next);
    setDocumentTheme(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // The current page still changes even when browser storage is blocked.
    }
  }

  function openApplication() {
    setMenuOpen(false);
    setSubmitted(false);
    setFormError(false);
    setApplicationError("");
    setApplicationBusy(false);
    setApplicationOpen(true);
  }

  function closeApplication() {
    setApplicationOpen(false);
  }

  async function submitApplication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.checkValidity()) {
      setFormError(true);
      form.reportValidity();
      return;
    }

    setFormError(false);
    setApplicationError("");
    setApplicationBusy(true);

    const formData = new FormData(form);

    try {
      await api.post("/api/auth/signup/", {
        email: String(formData.get("email") ?? "").trim(),
        password: String(formData.get("password") ?? ""),
        full_name: String(formData.get("name") ?? "").trim(),
        workspace_name: String(formData.get("organization") ?? "").trim(),
        application_only: true,
      });
      setSubmitted(true);
      form.reset();
    } catch (caught) {
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          const first =
            body?.email ??
            body?.password ??
            body?.full_name ??
            body?.workspace_name ??
            body?.detail;
          setApplicationError(
            Array.isArray(first)
              ? String(first[0])
              : String(first || "Application could not be submitted."),
          );
        } catch {
          setApplicationError("Application could not be submitted. Please try again.");
        }
      } else {
        setApplicationError("Network error. Please try again.");
      }
    } finally {
      setApplicationBusy(false);
    }
  }

  return (
    <div className="mm-home">
      <a className="mm-skip-link" href="#main">
        Skip to content
      </a>

      <header className="mm-site-header" id="top">
        <div className="mm-shell mm-header-inner">
          <a className="mm-brand" href="#top" aria-label="MateMail home">
            <Image src="/matemail-logo.png" alt="" width={34} height={26} priority />
            <span className="mm-wordmark">MateMail</span>
          </a>

          <nav className="mm-desktop-nav" aria-label="Primary navigation">
            <a href="#product">Product</a>
            <a href="#security">Security</a>
            <a href="#how-it-works">How it works</a>
            <a href="#private-beta">Private Beta</a>
          </nav>

          <div className="mm-header-actions">
            <div className="mm-theme-switcher" role="radiogroup" aria-label="Color theme">
              {themeOptions.map(({ value, label, Icon }) => (
                <button
                  key={value}
                  type="button"
                  className="mm-theme-button"
                  data-active={theme === value}
                  role="radio"
                  aria-checked={theme === value}
                  aria-label={"Use " + label.toLowerCase() + " theme"}
                  title={label + " theme"}
                  onClick={() => chooseTheme(value)}
                >
                  <Icon aria-hidden="true" />
                </button>
              ))}
            </div>
            <a className="mm-link-button mm-compact mm-hide-mid" href={POSTBOX_URL}>
              Open PostBox
            </a>
            <a className="mm-link-button mm-compact mm-hide-mid" href={WORKSPACE_URL}>
              Open Workspace
            </a>
            <button
              type="button"
              className="mm-button mm-primary mm-compact"
              onClick={openApplication}
            >
              Apply for Private Beta
            </button>
            <button
              type="button"
              className="mm-menu-toggle"
              aria-expanded={menuOpen}
              aria-controls="mm-mobile-menu"
              aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
              onClick={() => setMenuOpen((open) => !open)}
            >
              {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
            </button>
          </div>
        </div>

        {menuOpen && (
          <div className="mm-mobile-menu" id="mm-mobile-menu">
            <div className="mm-shell mm-mobile-menu-inner">
              <a href="#product" onClick={() => setMenuOpen(false)}>Product</a>
              <a href="#security" onClick={() => setMenuOpen(false)}>Security</a>
              <a href="#how-it-works" onClick={() => setMenuOpen(false)}>How it works</a>
              <a href="#private-beta" onClick={() => setMenuOpen(false)}>Private Beta</a>
              <div className="mm-mobile-menu-actions">
                <a className="mm-link-button" href={POSTBOX_URL}>Open PostBox</a>
                <a className="mm-link-button" href={WORKSPACE_URL}>Open Workspace</a>
                <button type="button" className="mm-button mm-primary" onClick={openApplication}>
                  Apply for Private Beta
                </button>
              </div>
            </div>
          </div>
        )}
      </header>

      <main id="main">
        <section className="mm-hero mm-section-grid-bg">
          <div className="mm-shell mm-hero-grid">
            <div className="mm-hero-copy">
              <p className="mm-eyebrow">
                <span className="mm-eyebrow-line" />
                Business Email by NetaMate Solutions
              </p>
              <h1>
                Business email built around <span>your domain.</span>
              </h1>
              <p className="mm-hero-lede">
                MateMail gives organizations a secure place to manage custom-domain email,
                while mailbox users get a focused webmail experience through MateMail PostBox.
              </p>
              <div className="mm-hero-actions">
                <button type="button" className="mm-button mm-primary" onClick={openApplication}>
                  Apply for Private Beta
                </button>
                <a className="mm-button mm-secondary" href={WORKSPACE_URL}>Open Workspace</a>
                <a className="mm-text-link" href={POSTBOX_URL}>
                  Open PostBox <ArrowUpRight aria-hidden="true" />
                </a>
              </div>
              <div className="mm-hero-note">
                <span className="mm-status-dot" aria-hidden="true" />
                <span>Private Beta. Organization activation requires approval.</span>
              </div>
            </div>

            <div
              className="mm-hero-visual"
              aria-label="Representative MateMail Workspace and PostBox interfaces"
            >
              <div className="mm-visual-frame">
                <div className="mm-visual-rail">
                  <div className="mm-visual-brand">
                    <Image src="/matemail-logo.png" alt="" width={28} height={21} />
                    <span>Workspace</span>
                  </div>
                  <div className="mm-rail-item mm-active">Overview</div>
                  <div className="mm-rail-item">Domains</div>
                  <div className="mm-rail-item">Mailboxes</div>
                  <div className="mm-rail-item">Aliases</div>
                  <div className="mm-rail-item">Forwarding</div>
                </div>
                <div className="mm-visual-main">
                  <div className="mm-visual-topbar">
                    <span>Organization email</span>
                    <span className="mm-sample-chip">Sample interface</span>
                  </div>
                  <div className="mm-domain-card">
                    <div>
                      <span className="mm-micro-label">PRIMARY DOMAIN</span>
                      <strong>yourcompany.com</strong>
                    </div>
                    <span className="mm-state-ready">Verified</span>
                  </div>
                  <div className="mm-metric-grid">
                    <div><span>Mailboxes</span><strong>8 active</strong></div>
                    <div><span>Aliases</span><strong>12</strong></div>
                    <div><span>Domain health</span><strong>Ready</strong></div>
                  </div>
                  <div className="mm-record-lines">
                    <div><span>MX</span><b /><em>Ready</em></div>
                    <div><span>SPF</span><b /><em>Ready</em></div>
                    <div><span>DKIM</span><b /><em>Ready</em></div>
                  </div>
                </div>
              </div>

              <div className="mm-postbox-float">
                <div className="mm-postbox-top">
                  <span>PostBox</span>
                  <span aria-hidden="true">⌕</span>
                </div>
                <div className="mm-postbox-body">
                  <div className="mm-mail-nav">
                    <strong>Inbox</strong>
                    <span>Sent</span>
                    <span>Drafts</span>
                    <span>Archive</span>
                  </div>
                  <div className="mm-mail-list">
                    <div className="mm-mail-row mm-unread">
                      <i />
                      <div><strong>Project update</strong><span>Timeline and next steps</span></div>
                      <time>09:42</time>
                    </div>
                    <div className="mm-mail-row">
                      <i />
                      <div><strong>Invoice</strong><span>September statement</span></div>
                      <time>08:18</time>
                    </div>
                    <div className="mm-mail-row">
                      <i />
                      <div><strong>Support request</strong><span>Account access question</span></div>
                      <time>Yesterday</time>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="mm-trust-strip" aria-label="MateMail product principles">
          <div className="mm-shell mm-trust-grid">
            <div>
              <strong>Custom domain first</strong>
              <span>Use the domain your business already owns.</span>
            </div>
            <div>
              <strong>Separate admin and mailbox experiences</strong>
              <span>Clear responsibilities for administrators and users.</span>
            </div>
            <div>
              <strong>Focused business email</strong>
              <span>No consumer mailbox signup or unrelated office-suite clutter.</span>
            </div>
          </div>
        </section>

        <section className="mm-section" id="product">
          <div className="mm-shell">
            <div className="mm-section-heading mm-split-heading">
              <div>
                <p className="mm-eyebrow">Product</p>
                <h2>One email platform.<br />Two focused experiences.</h2>
              </div>
              <p>
                Administrators handle organization-level email configuration in Workspace.
                Mailbox users read, send and organize mail in PostBox.
              </p>
            </div>

            <div className="mm-experience-grid">
              <article className="mm-experience-card mm-workspace-card">
                <div className="mm-card-number">01</div>
                <div className="mm-experience-title">
                  <div className="mm-mini-brand">
                    <Image src="/matemail-logo.png" alt="" width={32} height={24} />
                  </div>
                  <div>
                    <p>For organization owners and administrators</p>
                    <h3>MateMail Workspace</h3>
                  </div>
                </div>
                <p>
                  Connect business domains, create mailboxes, manage aliases and forwarding,
                  and review organization email setup from one administration surface.
                </p>
                <ul className="mm-feature-list">
                  <li>Connect and verify domains</li>
                  <li>Create and manage mailboxes</li>
                  <li>Configure aliases and forwarding</li>
                  <li>Review DNS and email readiness</li>
                </ul>
                <a className="mm-button mm-secondary" href={WORKSPACE_URL}>
                  Open Workspace <ArrowUpRight aria-hidden="true" />
                </a>
              </article>

              <article className="mm-experience-card mm-postbox-card">
                <div className="mm-card-number">02</div>
                <div className="mm-experience-title">
                  <div className="mm-mini-brand">
                    <Image src="/matemail-logo.png" alt="" width={32} height={24} />
                  </div>
                  <div>
                    <p>For mailbox users</p>
                    <h3>MateMail PostBox</h3>
                  </div>
                </div>
                <p>
                  A dedicated webmail experience for everyday email, with the essentials
                  people expect and without mixing in organization administration.
                </p>
                <ul className="mm-feature-list">
                  <li>Inbox, Sent, Drafts and folders</li>
                  <li>Search and message organization</li>
                  <li>Compose, signatures and contacts</li>
                  <li>Mailbox-level preferences</li>
                </ul>
                <a className="mm-button mm-secondary" href={POSTBOX_URL}>
                  Open PostBox <ArrowUpRight aria-hidden="true" />
                </a>
              </article>
            </div>
          </div>
        </section>

        <section className="mm-section mm-domain-section">
          <div className="mm-shell mm-domain-grid">
            <div className="mm-domain-copy">
              <p className="mm-eyebrow">Custom-domain business email</p>
              <h2>Your business.<br />Your domain.<br />Your email.</h2>
              <p>
                MateMail is built for organizations that want professional email on domains
                they own, with a dedicated Workspace for administration and PostBox for mail.
              </p>
              <div className="mm-address-tree" aria-label="Example business mailbox addresses">
                <strong>yourcompany.com</strong>
                <span>├── hello@yourcompany.com</span>
                <span>├── support@yourcompany.com</span>
                <span>├── billing@yourcompany.com</span>
                <span>└── name@yourcompany.com</span>
              </div>
            </div>

            <div className="mm-dns-panel">
              <div className="mm-panel-kicker">
                DOMAIN READINESS <span>Conceptual view</span>
              </div>
              <div className="mm-dns-domain">
                <span>yourcompany.com</span>
                <strong>Connected</strong>
              </div>
              <div className="mm-dns-table">
                <div><code>MX</code><span>Mail routing</span><b className="mm-dns-state">Ready</b></div>
                <div><code>SPF</code><span>Sender authorization</span><b className="mm-dns-state">Ready</b></div>
                <div><code>DKIM</code><span>Message signing</span><b className="mm-dns-state">Ready</b></div>
                <div><code>DMARC</code><span>Domain policy</span><b className="mm-dns-state mm-neutral">Configured</b></div>
              </div>
              <p className="mm-panel-footnote">
                Example status only. Actual DNS configuration depends on each organization&apos;s domain setup.
              </p>
            </div>
          </div>
        </section>

        <section className="mm-section mm-how-section" id="how-it-works">
          <div className="mm-shell">
            <div className="mm-section-heading mm-compact-heading">
              <p className="mm-eyebrow">How MateMail works</p>
              <h2>From application to inbox.</h2>
            </div>
            <div className="mm-steps-grid">
              <article><span>01</span><h3>Request access</h3><p>Your organization applies for the MateMail Private Beta.</p></article>
              <article><span>02</span><h3>Connect your domain</h3><p>Verify domain ownership and configure the required email DNS records.</p></article>
              <article><span>03</span><h3>Create mailboxes</h3><p>Administrators configure mailboxes and related settings through Workspace.</p></article>
              <article><span>04</span><h3>Use PostBox</h3><p>Mailbox users sign in to send, receive and manage professional email.</p></article>
            </div>
          </div>
        </section>

        <section className="mm-section mm-capabilities-section">
          <div className="mm-shell">
            <div className="mm-section-heading mm-split-heading">
              <div>
                <p className="mm-eyebrow">Core capabilities</p>
                <h2>The essentials for running business email.</h2>
              </div>
              <p>
                MateMail keeps the product centered on real email administration,
                domain readiness and mailbox access.
              </p>
            </div>
            <div className="mm-capability-grid">
              {capabilities.map(({ Icon, title, copy }) => (
                <article key={title}>
                  <span className="mm-cap-icon"><Icon aria-hidden="true" /></span>
                  <h3>{title}</h3>
                  <p>{copy}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="mm-section mm-security-section" id="security">
          <div className="mm-shell mm-security-grid">
            <div className="mm-security-copy">
              <p className="mm-eyebrow">Security and responsibility</p>
              <h2>Business email should be treated like infrastructure.</h2>
              <p>
                MateMail separates platform operations, organization administration and
                individual mailbox access so each role has a clear boundary.
              </p>
              <div className="mm-security-points">
                <div><span>01</span><p><strong>Controlled administration</strong> Organization administrators manage infrastructure and account configuration.</p></div>
                <div><span>02</span><p><strong>Mailbox isolation</strong> Everyday mailbox content remains part of the user&apos;s PostBox experience.</p></div>
                <div><span>03</span><p><strong>Domain authentication</strong> DNS readiness supports proper sender identity and mail delivery configuration.</p></div>
              </div>
            </div>

            <div className="mm-security-diagram" aria-label="MateMail responsibility separation diagram">
              <div className="mm-diagram-node mm-root">
                <small>PLATFORM OPERATIONS</small>
                <strong>MateMail Platform</strong>
              </div>
              <div className="mm-diagram-line mm-vertical" />
              <div className="mm-diagram-split" />
              <div className="mm-diagram-children">
                <div>
                  <div className="mm-diagram-line mm-child-line" />
                  <div className="mm-diagram-node">
                    <small>ORGANIZATION ADMIN</small>
                    <strong>MateMail Workspace</strong>
                    <span>Domains · Mailboxes · Email configuration</span>
                  </div>
                </div>
                <div>
                  <div className="mm-diagram-line mm-child-line" />
                  <div className="mm-diagram-node">
                    <small>MAILBOX USER</small>
                    <strong>MateMail PostBox</strong>
                    <span>Inbox · Sending · Mailbox preferences</span>
                  </div>
                </div>
              </div>
              <p className="mm-diagram-note">
                Administration is separated from normal mailbox access.
              </p>
            </div>
          </div>
        </section>

        <section className="mm-section mm-access-section">
          <div className="mm-shell">
            <div className="mm-section-heading mm-compact-heading mm-center-heading">
              <p className="mm-eyebrow">Access gateway</p>
              <h2>Already using MateMail?</h2>
              <p>Choose the area that matches what you need to do.</p>
            </div>
            <div className="mm-access-grid">
              <a className="mm-access-card" href={WORKSPACE_URL}>
                <span className="mm-access-type">ORGANIZATION ADMINISTRATOR</span>
                <h3>Open MateMail Workspace</h3>
                <p>Manage your organization&apos;s domains, mailboxes and email configuration.</p>
                <strong>portal.matemail.online <ArrowUpRight aria-hidden="true" /></strong>
              </a>
              <a className="mm-access-card" href={POSTBOX_URL}>
                <span className="mm-access-type">MAILBOX USER</span>
                <h3>Open MateMail PostBox</h3>
                <p>Open your inbox, send mail and manage your mailbox.</p>
                <strong>postbox.matemail.online <ArrowUpRight aria-hidden="true" /></strong>
              </a>
            </div>
          </div>
        </section>

        <section className="mm-section mm-beta-section" id="private-beta">
          <div className="mm-shell mm-beta-panel">
            <div>
              <p className="mm-eyebrow mm-light-eyebrow">Private Beta</p>
              <h2>Bring your business domain to MateMail.</h2>
              <p>
                MateMail is currently available through a limited Private Beta.
                Organizations can request access and are activated after review.
              </p>
              <p className="mm-beta-small">
                No payment required during the current Private Beta. Access is subject to approval.
              </p>
            </div>
            <div className="mm-beta-action">
              <button type="button" className="mm-button mm-light-primary" onClick={openApplication}>
                Apply for Private Beta
              </button>
              <span>Application takes about 2 minutes.</span>
            </div>
          </div>
        </section>

        <section className="mm-section mm-faq-section">
          <div className="mm-shell mm-faq-grid">
            <div className="mm-faq-heading">
              <p className="mm-eyebrow">FAQ</p>
              <h2>Common questions.</h2>
              <p>Short answers about access, domains and the two MateMail product experiences.</p>
            </div>
            <div className="mm-faq-list">
              {faqs.map(([question, answer]) => (
                <details key={question}>
                  <summary>{question}<span aria-hidden="true">+</span></summary>
                  <p>{answer}</p>
                </details>
              ))}
            </div>
          </div>
        </section>
      </main>

      <footer className="mm-site-footer">
        <div className="mm-shell mm-footer-grid">
          <div className="mm-footer-brand">
            <a className="mm-brand" href="#top">
              <Image src="/matemail-logo.png" alt="" width={34} height={26} />
              <span className="mm-wordmark">MateMail</span>
            </a>
            <p>Business email by NetaMate Solutions.</p>
          </div>
          <div>
            <strong>Product</strong>
            <a href={WORKSPACE_URL}>MateMail Workspace</a>
            <a href={POSTBOX_URL}>MateMail PostBox</a>
            <a href="#private-beta">Private Beta</a>
          </div>
          <div>
            <strong>Company</strong>
            <a href="https://netamate.com">NetaMate Solutions</a>
          </div>
          <div>
            <strong>Legal</strong>
            <a href="#" onClick={(event) => event.preventDefault()}>Privacy</a>
            <a href="#" onClick={(event) => event.preventDefault()}>Terms</a>
          </div>
        </div>
        <div className="mm-shell mm-footer-bottom">
          <span>© 2026 NetaMate Solutions. MateMail is currently in Private Beta.</span>
          <a href="#top">Back to top ↑</a>
        </div>
      </footer>

      <dialog
        ref={dialogRef}
        className="mm-application-dialog"
        aria-labelledby="mm-application-title"
        onClose={() => setApplicationOpen(false)}
        onPointerDown={(event) => {
          // Close only when the pointer press itself starts on the backdrop.
          // A native <select> can finish its interaction outside the dialog's
          // DOM and then emit a click against the dialog surface in Chromium,
          // which made choosing an option accidentally dismiss the form.
          if (event.target === event.currentTarget) closeApplication();
        }}
      >
        <div className="mm-dialog-shell">
          {!submitted ? (
            <>
              <div className="mm-dialog-header">
                <div>
                  <p className="mm-eyebrow">Private Beta application</p>
                  <h2 id="mm-application-title">Request MateMail access</h2>
                </div>
                <button
                  type="button"
                  className="mm-dialog-close"
                  aria-label="Close application"
                  onClick={closeApplication}
                >
                  <X aria-hidden="true" />
                </button>
              </div>
              <div className="mm-dialog-progress" aria-hidden="true"><span /></div>

              <form className="mm-application-form" onSubmit={submitApplication} noValidate>
                <p className="mm-form-intro">
                  Tell us about your organization. Submitting creates your MateMail account
                  and places the organization in the Platform approval queue.
                </p>
                <div className="mm-form-grid">
                  <label>
                    Organization name
                    <input name="organization" autoComplete="organization" required placeholder="Example Company Ltd." />
                  </label>
                  <label>
                    Your name
                    <input name="name" autoComplete="name" required placeholder="Full name" />
                  </label>
                  <label>
                    Business email
                    <input type="email" name="email" autoComplete="email" required placeholder="you@company.com" />
                  </label>
                  <label>
                    Account password
                    <input
                      type="password"
                      name="password"
                      autoComplete="new-password"
                      minLength={10}
                      required
                      placeholder="Minimum 10 characters"
                    />
                  </label>
                  <label>
                    Business domain
                    <input name="domain" required placeholder="company.com" pattern="^(?!https?://)([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$" />
                  </label>
                  <label>
                    Country / region
                    <input name="country" autoComplete="country-name" required placeholder="Country or region" />
                  </label>
                  <label>
                    Role
                    <select name="role" required defaultValue="">
                      <option value="" disabled>Select a role</option>
                      <option>Owner</option>
                      <option>IT Administrator</option>
                      <option>Technical Lead</option>
                      <option>Operations</option>
                      <option>Other</option>
                    </select>
                  </label>
                  <label>
                    Approximate number of mailboxes
                    <select name="mailboxes" required defaultValue="">
                      <option value="" disabled>Select range</option>
                      <option>1–10</option>
                      <option>11–25</option>
                      <option>26–50</option>
                      <option>51–100</option>
                      <option>100+</option>
                    </select>
                  </label>
                  <label>
                    Primary use
                    <select name="use" required defaultValue="">
                      <option value="" disabled>Select use</option>
                      <option>Company email</option>
                      <option>Internal team email</option>
                      <option>Customer communication</option>
                      <option>Support email</option>
                      <option>General business email</option>
                    </select>
                  </label>
                </div>
                <label className="mm-full-width">
                  How do you plan to use MateMail?
                  <textarea name="details" rows={4} placeholder="A short note about your organization and intended use." />
                </label>
                <label className="mm-checkbox-label">
                  <input type="checkbox" name="approval" required />
                  <span>I understand that Private Beta access requires approval.</span>
                </label>
                {formError && (
                  <div className="mm-form-error" role="alert">
                    Please complete the required fields before submitting.
                  </div>
                )}
                {applicationError && (
                  <div className="mm-form-error" role="alert">
                    {applicationError}
                  </div>
                )}
                <div className="mm-form-actions">
                  <button type="button" className="mm-button mm-secondary" onClick={closeApplication}>
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="mm-button mm-primary"
                    disabled={applicationBusy}
                  >
                    {applicationBusy ? "Submitting…" : "Submit Application"}
                  </button>
                </div>
              </form>
            </>
          ) : (
            <div className="mm-application-success">
              <div className="mm-success-mark"><Check aria-hidden="true" /></div>
              <p className="mm-eyebrow">Application received</p>
              <h2 id="mm-application-title">Thanks. Your request is pending review.</h2>
              <p>
                Your MateMail account and organization have been created and are now waiting
                for Platform approval. Once approved, the organization becomes Active.
              </p>
              <button type="button" className="mm-button mm-primary" onClick={closeApplication}>
                Close
              </button>
            </div>
          )}
        </div>
      </dialog>
    </div>
  );
}

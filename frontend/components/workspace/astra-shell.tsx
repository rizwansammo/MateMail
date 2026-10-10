"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity, Building2, CheckCircle2, ChevronDown, ChevronRight,
  Clock, DatabaseBackup, ExternalLink, Globe2, HardDrive, Inbox,
  KeyRound, LayoutDashboard, Link2, Lock, LogOut, Mail, Moon,
  Network, PanelLeft, Plug, Search, Settings, Shield, ShieldCheck, Sun,
  Users, Waypoints, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import { useWorkspaceTheme, WorkspaceThemeProvider } from "@/components/workspace/theme";

type NavItem = { label: string; href: string; icon: LucideIcon; adminOnly?: boolean; onboarding?: boolean };
type NavGroup = { label: string; items: NavItem[] };
const home: NavItem[] = [
  { label: "Overview", href: "/app", icon: LayoutDashboard },
  { label: "Getting started", href: "/app/onboarding", icon: CheckCircle2, onboarding: true },
];
const sections: NavGroup[] = [
  { label: "Email workspace", items: [
    { label: "Mailboxes", href: "/app/mailboxes", icon: Mail },
    { label: "TeamBox", href: "/app/team-boxes", icon: Inbox },
    { label: "Forward Groups", href: "/app/forward-groups", icon: Users },
    { label: "Aliases", href: "/app/aliases", icon: Link2 },
    { label: "Forwarding", href: "/app/forwarding", icon: Waypoints },
    { label: "Delegation", href: "/app/delegation", icon: ShieldCheck, adminOnly: true },
  ] },
  { label: "Organization", items: [
    { label: "Domains", href: "/app/domains", icon: Globe2 },
    { label: "Custom hostnames", href: "/app/settings#hub-custom-hostnames", icon: Network },
    { label: "Users & access", href: "/app/team", icon: Users },
    { label: "Plan & usage", href: "/app/billing", icon: HardDrive },
  ] },
  { label: "Operations", items: [
    { label: "Quarantine", href: "/app/spam", icon: Shield, adminOnly: true },
    { label: "Mail queue", href: "/app/queue", icon: Clock, adminOnly: true },
    { label: "Activity log", href: "/app/logs", icon: Activity, adminOnly: true },
    { label: "Backup history", href: "/app/backups", icon: DatabaseBackup, adminOnly: true },
    { label: "Connected apps", href: "/app/settings/integrations", icon: Plug, adminOnly: true },
  ] },
];
const settings: NavItem = { label: "Settings", href: "/app/settings", icon: Settings };
const allItems = [...home, ...sections.flatMap(group => group.items), settings];
type Resource = { id: string; text: string; description: string; searchText: string; category: string; href: string; icon: LucideIcon };
type SearchState = { tenantId: string; rows: Resource[]; loading: boolean; unavailable: number };
type SearchSource = {
  path: string; category: string; href: string; icon: LucideIcon;
  titleFields: string[]; detailFields: string[]; detailRoute?: boolean; adminOnly?: boolean;
};
type Onboarding = {
  workspace_created: boolean;
  domain_added: boolean;
  dns_verified: boolean;
  first_mailbox_created: boolean;
  completed: boolean;
  completed_at: string | null;
};
function initials(text: string) {
  return text.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(segment => segment[0]?.toUpperCase()).join("") || "?";
}
function accessible(item: NavItem, admin: boolean) {
  return !item.adminOnly || admin;
}
function currentItem(pathname: string): NavItem | undefined {
  return [...allItems, { label:"Account security",href:"/app/security",icon:ShieldCheck }, {label:"API keys",href:"/app/settings/api-keys",icon:KeyRound}]
    .filter(item => pathname === item.href || (item.href !== "/app" && pathname.startsWith(item.href + "/")))
    .sort((a,b) => b.href.length-a.href.length)[0];
}
function field(row: Record<string, unknown>, names: string[]): string {
  for (const name of names) {
    const candidate = row[name];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return "";
}
/** The backend remains authoritative for tenant isolation and permissions. */
function parseItems(data: unknown, source: SearchSource): Resource[] {
  const values: unknown[] = Array.isArray(data)
    ? data
    : data && typeof data === "object" && "results" in data && Array.isArray(data.results) ? data.results : [];
  return values.slice(0, 200).flatMap((value): Resource[] => {
    if (!value || typeof value !== "object") return [];
    const row = value as Record<string, unknown>;
    const id = typeof row.id === "string" ? row.id : "";
    const title = field(row, source.titleFields);
    if (!id || !title) return [];
    const description = field(row, source.detailFields);
    const href = source.detailRoute
      ? source.href + "/" + encodeURIComponent(id)
      : source.href + "?q=" + encodeURIComponent(title);
    return [{
      id: source.category + ":" + id, text: title, description,
      searchText: [title, description].join(" ").toLocaleLowerCase(),
      category: source.category, href, icon: source.icon,
    }];
  });
}
const searchSources: SearchSource[] = [
  {path:"/api/mailboxes/",category:"Mailboxes",href:"/app/mailboxes",icon:Mail,titleFields:["email"],detailFields:["full_name"],detailRoute:true},
  {path:"/api/domains/",category:"Domains",href:"/app/domains",icon:Globe2,titleFields:["domain"],detailFields:[],detailRoute:true},
  {path:"/api/team-boxes/",category:"TeamBox",href:"/app/team-boxes",icon:Inbox,titleFields:["email"],detailFields:["full_name"],detailRoute:true},
  {path:"/api/forward-groups/",category:"Forward Groups",href:"/app/forward-groups",icon:Users,titleFields:["address"],detailFields:["display_name"],detailRoute:true},
  {path:"/api/aliases/",category:"Aliases",href:"/app/aliases",icon:Link2,titleFields:["source_address"],detailFields:["destination_email"]},
  {path:"/api/forwarding/",category:"Forwarding",href:"/app/forwarding",icon:Waypoints,titleFields:["source_mailbox_email"],detailFields:["destination_email"]},
  {path:"/api/delegations/",category:"Delegation",href:"/app/delegation",icon:ShieldCheck,titleFields:["target_email"],detailFields:["delegate_email"],adminOnly:true},
  {path:"/api/teams/members/",category:"Users & access",href:"/app/team",icon:Users,titleFields:["email"],detailFields:["full_name"]},
];
/** Static settings shortcuts supplement navigation; no credential values are indexed. */
const extraPages: NavItem[] = [
  {label:"Account security",href:"/app/security",icon:ShieldCheck},
  {label:"API keys",href:"/app/settings/api-keys",icon:KeyRound,adminOnly:true},
];

export default function AppLayout({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading, user, tenant, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      const suffix = pathname.startsWith("/app/integrations/authorize")
        ? "?next=" + encodeURIComponent(pathname + (typeof window !== "undefined" ? window.location.search : ""))
        : "";
      router.replace("/login" + suffix);
    }
  }, [isLoading, isAuthenticated, pathname, router]);

  if (isLoading) {
    return <div className="grid min-h-screen place-items-center bg-slate-50"><div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-slate-950" /></div>;
  }
  if (!isAuthenticated) return null;

  return <WorkspaceThemeProvider>
    <AstraWorkspaceShell
      pathname={pathname}
      tenantId={tenant?.id || ""}
      workspaceName={tenant?.name || "MateMail"}
      accountName={user?.full_name || user?.email || "Account"}
      accountEmail={user?.email || ""}
      accountRole={tenant?.role || "read_only"}
      isPlatformAdmin={!!user?.is_platform_admin}
      onLogout={async () => { await logout(); router.push("/login"); }}
    >{children}</AstraWorkspaceShell>
  </WorkspaceThemeProvider>;
}

function AstraWorkspaceShell({ children,pathname,tenantId,workspaceName,accountName,accountEmail,accountRole,isPlatformAdmin,onLogout }:{
  children:ReactNode;pathname:string;tenantId:string;workspaceName:string;accountName:string;
  accountEmail:string;accountRole:string;isPlatformAdmin:boolean;onLogout:()=>Promise<void>;
}) {
  const {theme,setTheme} = useWorkspaceTheme();
  const router = useRouter();
  const [collapsed,setCollapsed] = useState(false);
  const [mobileOpen,setMobileOpen] = useState(false);
  const [closedGroups,setClosedGroups] = useState<string[]>([]);
  const [searchOpen,setSearchOpen] = useState(false);
  const [query,setQuery] = useState("");
  const [selected,setSelected] = useState(0);
  const [searchState,setSearchState] = useState<SearchState>({tenantId:"",rows:[],loading:false,unavailable:0});
  const [macShortcut,setMacShortcut] = useState(true);
  const [accountOpen,setAccountOpen] = useState(false);
  const [setupStatus,setSetupStatus] = useState<{tenantId:string;state:"required"|"done"}|null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const admin = accountRole==="admin"||accountRole==="owner";
  const current = currentItem(pathname);
  const done = setupStatus?.tenantId===tenantId && setupStatus.state==="done";
  // While the initial API response is loading or unavailable, show Getting Started.
  const showOnboarding = !done;

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    // The backend's persisted completion timestamp is authoritative across
    // devices. No localStorage flags or browser-specific setup state.
    const refresh = async () => {
      try {
        const response = await apiRequest(
          "/api/workspaces/" + encodeURIComponent(tenantId) + "/onboarding/"
        );
        if (!response.ok) return;
        const state = await response.json() as Onboarding;
        if (!cancelled) {
          setSetupStatus({ tenantId, state: state.completed ? "done" : "required" });
        }
      } catch {
        // A network outage must never falsely mark setup complete.
      }
    };
    void refresh();
    const onFocus = () => { void refresh(); };
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    // Mailbox provisioning can complete while this layout stays mounted on
    // the same route; refresh on explicit operations as well as navigation.
    window.addEventListener("focus", onFocus);
    window.addEventListener("matemail:workspace-onboarding-updated", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("matemail:workspace-onboarding-updated", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [tenantId, pathname]);

  useEffect(() => {
    if (!searchOpen) return;
    const controller = new AbortController();
    const sources = searchSources.filter(source => !source.adminOnly || admin);
    // Keep the results tagged with their tenant: no previous workspace's data
    // can be displayed while new requests are pending or if they fail.
    setSearchState({tenantId,rows:[],loading:!!tenantId,unavailable:0});
    if (!tenantId) return;
    void Promise.allSettled(sources.map(async source => {
      const path = source.category === "Users & access"
        ? "/api/workspaces/" + encodeURIComponent(tenantId) + "/members/"
        : source.path;
      const response = await apiRequest(path,{signal:controller.signal});
      if (!response.ok) throw new Error("Search source not available");
      return parseItems(await response.json(),source);
    })).then(results => {
      if (controller.signal.aborted) return;
      const rows: Resource[] = [];
      let unavailable = 0;
      for (const result of results) {
        if (result.status === "fulfilled") rows.push(...result.value);
        else unavailable++;
      }
      setSearchState({tenantId,rows,loading:false,unavailable});
    });
    return () => controller.abort();
  },[searchOpen,tenantId,admin]);

  useEffect(() => {
    const listener=(event:KeyboardEvent)=>{
      if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){
        event.preventDefault();
        setSearchOpen(value=>!value);
      } else if(event.key==="Escape"){
        setSearchOpen(false);
        setAccountOpen(false);
        setMobileOpen(false);
      }
    };
    document.addEventListener("keydown",listener);
    return ()=>document.removeEventListener("keydown",listener);
  },[]);

  useEffect(() => {
    setMacShortcut(/Mac|iPhone|iPad/i.test(navigator.platform));
  },[]);

  useEffect(() => {
    if (!searchOpen) return;
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    searchRef.current?.focus();
    return () => {
      document.body.style.overflow = priorOverflow;
      openerRef.current?.focus();
    };
  },[searchOpen]);

  useEffect(() => {
    const closeMenus = () => { setMobileOpen(false); setAccountOpen(false); };
    window.addEventListener("popstate", closeMenus);
    return () => window.removeEventListener("popstate", closeMenus);
  },[]);

  const pageItems=[...allItems,...extraPages].filter(item=>accessible(item,admin)&&(!item.onboarding||showOnboarding));
  const matches=useMemo(() => {
    const needle=query.trim().toLocaleLowerCase();
    const pages:Resource[]=pageItems.filter(item=>!needle||item.label.toLocaleLowerCase().includes(needle)).map(item=>({
      text:item.label,description:"",searchText:item.label.toLocaleLowerCase(),
      category:"Pages",href:item.href,icon:item.icon,id:"page:"+item.href,
    }));
    const currentRows=searchState.tenantId===tenantId?searchState.rows:[];
    const records=currentRows.filter(item=>!needle||item.searchText.includes(needle));
    const rank=(item:Resource) => item.text.toLocaleLowerCase().startsWith(needle)?0:1;
    return [...pages,...records.sort((a,b)=>rank(a)-rank(b)||a.text.localeCompare(b.text))].slice(0,100);
    // Every search result belongs to this tenant and is filtered by role.
  },[query,searchState,tenantId,admin,showOnboarding]);
  const openSearch=()=>{openerRef.current=document.activeElement as HTMLElement;setQuery("");setSelected(0);setSearchOpen(true);};
  const navigate=(href:string)=>{setSearchOpen(false);setMobileOpen(false);router.push(href);};
  const choose=(item:Resource)=>navigate(item.href);
  const activeIndex=Math.min(selected,Math.max(0,matches.length-1));
  useEffect(() => {
    if (!searchOpen) return;
    document.getElementById("astra-search-result-"+activeIndex)?.scrollIntoView({block:"nearest"});
  },[searchOpen,activeIndex,query,searchState]);
  const navLink=(item:NavItem)=>{
    const active=current?.href===item.href;
    return <li key={item.href} data-slot="sidebar-menu-item">
      <Link href={item.href} data-slot="sidebar-menu-button" data-active={active} aria-current={active?"page":undefined}
        title={collapsed?item.label:undefined} onClick={()=>setMobileOpen(false)}>
        <item.icon aria-hidden="true"/><span className="astra-nav-text">{item.label}</span>
        {item.adminOnly&&!admin&&<Lock size={12} aria-label="Restricted" />}
      </Link>
    </li>;
  };
  const shortLinks=home.filter(item=>!item.onboarding||showOnboarding);
  return <div className={"ws portal-premium astra-hub"+(theme==="dark"?" dark":"")}>
    <div className="astra-frame">
      <button type="button" aria-label="Close navigation" className="astra-backdrop" data-open={mobileOpen} onClick={()=>setMobileOpen(false)}/>
      <aside className="astra-sidebar hub-sidebar" data-collapsed={collapsed} data-mobile-open={mobileOpen} aria-label="Workspace navigation">
        <div data-slot="sidebar-header"><div className="brand">
          <BrandMark size={30}/><span className="wordmark">MateMail</span><span className="brand-hub">HUB</span>
        </div></div>
        <nav data-slot="sidebar-content">
          <div data-slot="sidebar-group"><ul data-slot="sidebar-menu">{shortLinks.map(navLink)}</ul></div>
          {sections.map(group=><div key={group.label} data-slot="sidebar-group">
            <div data-slot="sidebar-group-label">
              <button type="button" aria-expanded={!closedGroups.includes(group.label)} onClick={()=>setClosedGroups(old=>old.includes(group.label)?old.filter(x=>x!==group.label):[...old,group.label])}>
                {group.label}<ChevronDown size={12} className={closedGroups.includes(group.label)?"rotate-closed":""}/>
              </button>
            </div>
            {(!closedGroups.includes(group.label)||collapsed)&&<div data-slot="sidebar-group-content">
              <ul data-slot="sidebar-menu">{group.items.filter(item=>accessible(item,admin)).map(navLink)}</ul>
            </div>}
          </div>)}
          <div data-slot="sidebar-group"><ul data-slot="sidebar-menu">{navLink(settings)}</ul></div>
        </nav>
        <div data-slot="sidebar-footer"><div className="sidebar-footnote">ORGANIZATION ADMINISTRATION</div></div>
      </aside>
      <div className="astra-hub-main hub-workspace">
        <header className="topbar astra-topbar">
          <div className="topbar-left">
            <button type="button" className="astra-icon-button" aria-label={collapsed?"Expand sidebar":"Collapse sidebar"}
              onClick={()=>window.innerWidth<=800?setMobileOpen(true):setCollapsed(old=>!old)}>
              <PanelLeft size={17}/>
            </button>
            <span className="topbar-separator"/>
            <Building2 size={15}/>
            <span className="astra-org-name org-name">{workspaceName}</span>
          </div>
          <div className="topbar-right">
            <button type="button" className="global-search" onClick={openSearch} aria-label="Search workspace">
              <Search size={16}/><span>Search workspace</span><kbd>{macShortcut?"⌘ K":"Ctrl K"}</kbd>
            </button>
            <button type="button" className="astra-icon-button" aria-label={theme==="dark"?"Switch to light theme":"Switch to dark theme"}
              onClick={()=>setTheme(theme==="dark"?"light":"dark")}>{theme==="dark"?<Sun size={17}/>:<Moon size={17}/>}</button>
            <button type="button" className="profile-button" aria-label="Account menu" aria-expanded={accountOpen}
              onClick={()=>setAccountOpen(old=>!old)}>
              <span className="astra-avatar">{initials(accountName)}</span><ChevronDown size={13}/>
            </button>
          </div>
          {accountOpen&&<div className="astra-account-menu">
            <div className="astra-account-label"><strong>{accountName}</strong><small>{accountEmail} · {accountRole}</small></div>
            <Link href="/app/settings" onClick={()=>setAccountOpen(false)}><Settings size={15}/> Workspace settings</Link>
            <Link href="/app/security" onClick={()=>setAccountOpen(false)}><ShieldCheck size={15}/> My account & security</Link>
            <Link href="/app/settings/api-keys" onClick={()=>setAccountOpen(false)}><KeyRound size={15}/> API keys</Link>
            {isPlatformAdmin&&<Link href="/admin"><ExternalLink size={15}/> Admin console</Link>}
            <button type="button" onClick={()=>void onLogout()}><LogOut size={15}/> Sign out</button>
          </div>}
        </header>
        <main id="main-content" className="astra-main-area">
          <div className="astra-breadcrumb"><span>Hub</span><ChevronRight size={12}/><span className="astra-current-page">{current?.label||"Workspace"}</span></div>
          <div className="astra-page-content">{children}</div>
          <footer className="astra-footer"><span>MateMail Hub · Organization administration</span><span>Secure administration for your email organization</span></footer>
        </main>
      </div>
    </div>
    {searchOpen&&<div className="astra-search-layer" onMouseDown={event=>{if(event.target===event.currentTarget)setSearchOpen(false)}}>
      <section ref={dialogRef} className="astra-search-dialog" role="dialog" aria-modal="true" aria-label="Search Hub" onKeyDown={event=>{
        if(event.key==="ArrowDown"){event.preventDefault();setSelected(index=>Math.min(Math.max(0,matches.length-1),index+1))}
        if(event.key==="ArrowUp"){event.preventDefault();setSelected(index=>Math.max(0,index-1))}
        if(event.key==="Enter"&&matches[activeIndex]){event.preventDefault();choose(matches[activeIndex])}
        if(event.key==="Tab"){
          const focusable=Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('input,button:not([disabled])')??[]);
          const first=focusable[0],last=focusable[focusable.length-1];
          if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
          else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
        }
      }}>
        <div className="astra-search-inputrow">
          <Search size={16} aria-hidden="true"/>
          <input ref={searchRef} autoFocus aria-label="Search Hub pages and resources" placeholder="Search pages, mailboxes, domains…" value={query}
            aria-controls="astra-hub-search-results" aria-activedescendant={matches.length?"astra-search-result-"+activeIndex:undefined}
            onChange={event=>{setQuery(event.target.value);setSelected(0);}}/>
          <button type="button" className="astra-search-x" onClick={()=>setSearchOpen(false)} aria-label="Close search"><X size={16}/></button>
        </div>
        <div id="astra-hub-search-results" className="astra-search-results" role="listbox" aria-label="Search results" aria-busy={searchState.tenantId===tenantId&&searchState.loading}>
          {matches.length===0?<div className="astra-search-empty">{searchState.tenantId===tenantId&&searchState.loading?"Searching workspace…":"No matching results."}</div>:
            [...new Set(matches.map(item=>item.category))].map(category=><div key={category} role="group" aria-label={category}>
              <div className="astra-search-group">{category}</div>
              {matches.map((item,index)=>item.category===category&&<button type="button" key={item.id} id={"astra-search-result-"+index} role="option" aria-label={item.text} aria-selected={index===activeIndex} data-selected={index===activeIndex} className="astra-search-row"
                onMouseEnter={()=>setSelected(index)} onClick={()=>choose(item)}><item.icon size={16} aria-hidden="true"/><span className="astra-search-rowtext"><span>{item.text}</span>{item.description&&<small>{item.description}</small>}</span></button>)}
            </div>)}
          {searchState.tenantId===tenantId&&searchState.loading&&<div className="astra-search-feedback" role="status">Loading workspace resources…</div>}
          {searchState.tenantId===tenantId&&!searchState.loading&&searchState.unavailable>0&&<div className="astra-search-feedback" role="status">Some resources are unavailable. Page search is still working.</div>}
        </div>
        <div className="astra-search-footer"><span><kbd>↑</kbd><kbd>↓</kbd> Navigate <kbd>↵</kbd> Open</span><span><kbd>Esc</kbd> Close</span></div>
      </section>
    </div>}
  </div>;
}

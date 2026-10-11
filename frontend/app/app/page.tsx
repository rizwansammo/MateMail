"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import {
  Activity, AlertCircle, CheckCircle2, ChevronDown, ChevronRight,
  Globe2, Inbox, Plus, ShieldCheck, Users,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";

interface WorkspaceStats {
  domain_count: number;
  active_domain_count: number;
  mailbox_count: number;
  active_mailbox_count: number;
  storage_used_mb: number;
  storage_quota_mb: number;
  member_count: number;
  my_role: string;
  tenant_status: string;
  tenant_plan: string;
}
interface Domain {
  id: string;
  domain: string;
  status: string;
  ownership_verified: boolean;
  dns_health_score: number | null;
  mail_service_ready: boolean;
}
interface Member {
  id: string;
  email?: string;
  full_name?: string;
  role: string;
  status: string;
}
interface CustomHostname {
  id: string;
  hostname: string;
  surface: "hub" | "postbox";
  dns_status: string;
  provisioning_status: string;
}
interface Event {
  id: string;
  event_type: string;
  source?: string;
  result?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}
interface Onboarding { completed: boolean; workspace_created: boolean; domain_added: boolean;
  dns_verified: boolean; first_mailbox_created: boolean; }

function items<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === "object" && "results" in data) {
    const rows = (data as {results?: unknown}).results;
    if (Array.isArray(rows)) return rows as T[];
  }
  return [];
}
function formatDate(date: string): string {
  const d = new Date(date);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, {month:"short",day:"numeric",year:"numeric"});
}
function tone(domain: Domain): "good"|"warn" {
  return domain.ownership_verified && domain.status==="active"
    && domain.mail_service_ready && (domain.dns_health_score ?? 0)>=95 ? "good" : "warn";
}
function domainHelp(domain: Domain): {label:string;description:string} {
  if (!domain.ownership_verified) return {label:"Verify ownership",description:"Publish your domain verification record"};
  if (!domain.mail_service_ready) return {label:"Review mail service",description:"Check mail-service provisioning"};
  if (domain.status!=="active" || (domain.dns_health_score ?? 0)<95)
    return {label:"Review DNS",description:"Review mail DNS configuration"};
  return {label:"Configured",description:"Ownership and mail configuration healthy"};
}
function Metric({label,value,unit,description,progress,children}: {
  label:string;value:string|number;unit?:string;description:string;progress?:number;children?:ReactNode;
}) {
  return <div className="astra-ov-metric">
    <div className="astra-ov-metric-label">{label}</div>
    <div className="astra-ov-metric-value">{value}{unit&&<span> {unit}</span>}</div>
    <p>{description}</p>
    {progress!==undefined&&<div className="astra-ov-progress" aria-label={label+" usage"}
      role="progressbar" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{width:Math.min(100,Math.max(0,progress))+"%"}}/>
    </div>}
    {children}
  </div>;
}
function PanelHeader({title,subtitle,href,label}: {
  title:string;subtitle:string;href?:string;label?:string;
}) {
  return <div className="astra-ov-panel-head">
    <div><h2>{title}</h2><p>{subtitle}</p></div>
    {href&&<Link href={href}>{label||"View all"} <ChevronRight size={14}/></Link>}
  </div>;
}
function initials(name: string) {
  return name.split(/[\s@._-]+/).filter(Boolean).slice(0,2)
    .map(part=>part[0].toUpperCase()).join("")||"M";
}

/** The original Astra overview structure, populated only by authenticated APIs.
 * No prototype demo records, seeded storage usage, or fictional plan quotas. */
export default function DashboardPage() {
  const {tenant,user}=useAuth();
  const tenantId=tenant?.id;
  const [stats,setStats]=useState<WorkspaceStats|null>(null);
  const [domains,setDomains]=useState<Domain[]>([]);
  const [members,setMembers]=useState<Member[]|null>(null);
  const [events,setEvents]=useState<Event[]|null>(null);
  const [onboarding,setOnboarding]=useState<Onboarding|null>(null);
  const [hostnames,setHostnames]=useState<CustomHostname[]>([]);
  const [loading,setLoading]=useState(true);
  const [loadedTenant,setLoadedTenant]=useState("");
  const [createOpen,setCreateOpen]=useState(false);

  useEffect(()=>{
    if(!tenantId)return;
    let cancelled=false;
    const get=async(url:string)=>{
      const response=await apiRequest(url);
      if(!response.ok)throw new Error("Resource unavailable");
      return response.json() as Promise<unknown>;
    };
    void Promise.allSettled([
      get("/api/workspaces/"+encodeURIComponent(tenantId)+"/stats/"),
      get("/api/domains/"),
      get("/api/workspaces/"+encodeURIComponent(tenantId)+"/members/"),
      get("/api/logs/"),
      get("/api/workspaces/"+encodeURIComponent(tenantId)+"/onboarding/"),
      get("/api/custom-hostnames/"),
    ]).then(results=>{
      if(cancelled)return;
      const [st,dom,mem,log,setup,hosts]=results;
      // Reset every result on failed calls: never retain another tenant's
      // records or display stale role/status after an account transition.
      setStats(st.status==="fulfilled" && st.value && typeof st.value==="object"
        ?st.value as WorkspaceStats:null);
      setDomains(dom.status==="fulfilled"?items<Domain>(dom.value):[]);
      setMembers(mem.status==="fulfilled"?items<Member>(mem.value):null);
      setEvents(log.status==="fulfilled"?items<Event>(log.value).slice(0,4):null);
      setOnboarding(setup.status==="fulfilled" && setup.value && typeof setup.value==="object"
        ?setup.value as Onboarding:null);
      setHostnames(hosts.status==="fulfilled"?items<CustomHostname>(hosts.value):[]);
      setLoadedTenant(tenantId);
      setLoading(false);
    });
    return()=>{cancelled=true;};
  },[tenantId]);

  const authorized=stats?.my_role==="owner"||stats?.my_role==="admin";
  const overviewStatus=stats?.tenant_status || tenant?.status || "unknown";
  const active=overviewStatus==="active";
  const adminMembers=members?.filter(m=>m.status==="active"&&(m.role==="owner"||m.role==="admin"))||[];
  const pendingMembers=members?.filter(m=>m.status!=="active").length || 0;
  const usedGb=(stats?.storage_used_mb||0)/1024;
  const quotaGb=(stats?.storage_quota_mb||0)/1024;
  const attention=useMemo(()=>domains.filter(d=>tone(d)!=="good"),[domains]);
  const hostnameNeeds=useMemo(()=>hostnames.filter(host=>host.provisioning_status!=="active" && host.provisioning_status!=="inactive"),[hostnames]);
  const memberPreview=adminMembers.slice(0,4);
  const liveDate=useMemo(()=>new Date().toLocaleDateString(undefined,{weekday:"long",month:"long",day:"numeric"}),[]);

  if(!tenantId) return <div className="astra-overview">
    <div className="astra-ov-welcome"><div><h1>Workspace unavailable</h1>
      <p>No active organization is attached to this Hub session.</p></div></div>
  </div>;

  if(loading || loadedTenant!==tenantId) return <div className="astra-overview" aria-busy="true">
    <div className="astra-ov-topline"><span>ORGANIZATION OVERVIEW</span></div>
    <div className="astra-ov-welcome"><div><h1>Your workspace, in focus.</h1>
      <p>Loading verified organization data…</p></div></div>
    <div className="astra-ov-metrics astra-ov-loading">{[0,1,2,3].map(n=><div key={n}/>)}</div>
  </div>;

  return <div className="astra-overview">
    <div className="astra-ov-topline">
      <span>ORGANIZATION OVERVIEW</span><time>{liveDate}</time>
    </div>
    <div className="astra-ov-welcome">
      <div><h1>Your workspace, in focus.</h1>
        <p>Manage email, people and access for {tenant?.name||"your organization"}.</p></div>
      {authorized&&<div className="astra-ov-create">
        <button type="button" aria-expanded={createOpen} aria-label="Create new"
          onClick={()=>setCreateOpen(v=>!v)}><Plus size={16}/> Create new <ChevronDown size={14}/></button>
        {createOpen&&<div className="astra-ov-create-options" role="menu">
          <Link role="menuitem" href="/app/mailboxes" onClick={()=>setCreateOpen(false)}>Mailbox</Link>
          <Link role="menuitem" href="/app/team-boxes" onClick={()=>setCreateOpen(false)}>TeamBox</Link>
          <Link role="menuitem" href="/app/domains" onClick={()=>setCreateOpen(false)}>Domain</Link>
          <Link role="menuitem" href="/app/forward-groups" onClick={()=>setCreateOpen(false)}>Forward Group</Link>
        </div>}
      </div>}
    </div>
    <div className="astra-ov-statusline">
      {active?<CheckCircle2 size={15}/>:<AlertCircle size={15}/>}
      <strong>{active?"Organization active":"Organization "+overviewStatus.replaceAll("_"," ")}</strong>
      <span className="astra-ov-divider"/>
      <span>{active?"Mail provisioning enabled":"Some provisioning actions may be unavailable"}</span>
      {stats?.tenant_plan&&<span className="astra-ov-plan">{stats.tenant_plan.replaceAll("_"," ")} plan</span>}
    </div>
    {onboarding && !onboarding.completed && <div className="astra-ov-setup" role="status">
      <AlertCircle size={16}/><span>Finish setting up your organization.</span>
      <Link href="/app/onboarding">Open Getting started <ChevronRight size={14}/></Link>
    </div>}
    {!user?.email_verified&&<div className="astra-ov-setup" role="status">
      <AlertCircle size={16}/><span>Verify your Hub account email to enable administrative provisioning.</span>
      <Link href="/verify-email?sent=1">Verification options <ChevronRight size={14}/></Link>
    </div>}

    <div className="astra-ov-metrics" aria-label="Workspace statistics">
      <Metric label="Mailboxes" value={stats?.mailbox_count ?? "—"}
        description={stats?stats.active_mailbox_count+" active mailboxes":"Data unavailable"}/>
      <Metric label="Domains" value={stats?.domain_count ?? "—"}
        description={stats?domains.filter(d=>d.ownership_verified).length+" ownership verified":"Data unavailable"}>
        {attention.length>0&&<div className="astra-ov-metric-note">{attention.length} need attention</div>}
      </Metric>
      <Metric label="Storage used" value={stats?usedGb.toFixed(1):"—"} unit="GB"
        description={quotaGb>0?"of "+quotaGb.toFixed(1)+" GB allocated":"Storage allocation unavailable"}
        progress={quotaGb>0?usedGb/quotaGb*100:undefined}/>
      <Metric label="Administrators" value={members?adminMembers.length:"—"}
        description={members?pendingMembers+" pending invitation/member"+(pendingMembers===1?"":"s"):"Team data unavailable"}>
        {memberPreview.length>0&&<div className="astra-ov-avatars" aria-label="Workspace administrators">
          {memberPreview.map(m=><span key={m.id} title={m.full_name||m.email||"Administrator"}>{initials(m.full_name||m.email||"Admin")}</span>)}
        </div>}
      </Metric>
    </div>

    <div className="astra-ov-grid">
      <section className="astra-ov-panel">
        <PanelHeader title="Domain readiness" subtitle="Ownership and mail configuration"
          href="/app/domains" label="View domains"/>
        {domains.length===0?<div className="astra-ov-empty">No domains yet. <Link href="/app/domains">Add your first domain</Link></div>:
          <div className="astra-ov-domains">{domains.slice(0,5).map(d=>{
            const help=domainHelp(d);
            return <Link key={d.id} href={"/app/domains/"+encodeURIComponent(d.id)} className="astra-ov-domain">
              <Globe2 size={20}/>
              <span className="astra-ov-domain-name"><strong>{d.domain}</strong><small>{help.description}</small></span>
              <span className={"astra-ov-pill "+tone(d)}>{help.label}</span><ChevronRight size={15}/>
            </Link>;
          })}</div>}
      </section>
      <section className="astra-ov-panel">
        <div className="astra-ov-panel-head"><div><h2>Needs attention</h2>
          <p>A clear next step for every issue</p></div>
          <span className="astra-ov-count">{attention.length+hostnameNeeds.length}</span></div>
        {attention.length===0&&hostnameNeeds.length===0?<div className="astra-ov-allclear"><CheckCircle2 size={23}/>
          <h3>Nothing needs your attention</h3>
          <p>{domains.length?"No domain issues detected in the available data.":"Connect a domain to start monitoring its health."}</p>
          </div>:
          attention.slice(0,5).map((domain,index)=>{
            const help=domainHelp(domain);
            return <Link key={domain.id} className="astra-ov-attention" href={"/app/domains/"+encodeURIComponent(domain.id)}>
              <span className="astra-ov-counter">{String(index+1).padStart(2,"0")}</span>
              <span><strong>{help.label==="Verify ownership"?"Verify your new domain":help.label==="Review DNS"?"Review DNS configuration":help.label}</strong>
                <small>{domain.domain}</small></span><ChevronRight size={15}/>
            </Link>;
          })}
        {hostnameNeeds.slice(0,Math.max(0,5-attention.length)).map(host=><Link
          key={host.id} className="astra-ov-attention" href="/app/settings#hub-custom-hostnames">
          <span className="astra-ov-counter">{String(attention.length+hostnameNeeds.indexOf(host)+1).padStart(2,"0")}</span>
          <span><strong>Finish your custom address</strong><small>{host.hostname}</small></span>
          <ChevronRight size={15}/>
        </Link>)}
      </section>
    </div>

    <div className="astra-ov-grid astra-ov-lower">
      <section className="astra-ov-panel">
        <PanelHeader title="Recent activity" subtitle="Changes across your organization"
          href={authorized?"/app/logs":undefined} label="View activity"/>
        {!authorized?<div className="astra-ov-empty">Activity details are available to owners and administrators.</div>:
          !events?.length?<div className="astra-ov-empty">No recent activity available.</div>:
          events.map(e=><div key={e.id} className="astra-ov-activity">
            <Activity size={15}/><div><strong>{e.event_type.replaceAll("_"," ")}</strong>
              <small>{e.source||"Organization"} · {e.result||"Event"}</small></div>
            <time>{formatDate(e.created_at)}</time>
          </div>)}
      </section>
      <section className="astra-ov-panel">
        <PanelHeader title="Workspace essentials" subtitle="Bring the right people together"/>
        {[
          {href:"/app/team-boxes",name:"Shared mailboxes",detail:"Manage TeamBox access",icon:Inbox},
          {href:"/app/forward-groups",name:"Distribution groups",detail:"Review group recipients",icon:Users},
          {href:"/app/team",name:"Administrator access",detail:"People, roles and invitations",icon:ShieldCheck},
        ].map(item=><Link className="astra-ov-quick" key={item.href} href={item.href}>
          <item.icon size={20}/><span><strong>{item.name}</strong><small>{item.detail}</small></span><ChevronRight size={15}/>
        </Link>)}
      </section>
    </div>
  </div>;
}

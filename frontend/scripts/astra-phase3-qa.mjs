import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base = process.env.VISUAL_QA_URL || "http://127.0.0.1:3100";
const output = process.env.VISUAL_QA_OUTPUT || "visual-qa-artifacts";
fs.mkdirSync(output, { recursive: true });
const tid = "d9bdde9d-1234-4123-8123-777777777777";
const domainId = "d1111111-1111-4111-8111-111111111111";
const mailboxId = "e1111111-1111-4111-8111-111111111111";
const now = "2026-01-01T00:00:00Z";
const token = ["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", Buffer.from(JSON.stringify({tenant_id:tid,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"not-a-signature"].join(".");
const report = {phase:"Phase 3 Advanced Hub",checks:[],note:"All API routes intercepted; no real secrets, DNS or mail server actions."};
const browser = await chromium.launch({headless:true,args:["--no-sandbox"]});
function check(name,value,details={}) {
  report.checks.push({name,passed:!!value,details});
  assert.ok(value,name+": "+JSON.stringify(details));
}
async function make(role="owner",viewport={width:1440,height:900}) {
 const context=await browser.newContext({viewport,deviceScaleFactor:1,reducedMotion:"reduce"});
 const calls=[],errors=[],keys=[],integrations=[];
 const profile={id:"bd1a5653-1234-4234-8234-888888888888",email:"owner@example.test",full_name:"QA Owner",email_verified:true,two_factor_enabled:false,is_platform_admin:false,created_at:now};
 const mailbox={id:mailboxId,email:"owner@example.test",full_name:"QA Owner",status:"active",kind:"personal",mail_service_ready:true};
 await context.route("**/api/**",async route=>{
   const url=new URL(route.request().url()),q=url.pathname,method=route.request().method();
   let data=null;
   try {data=route.request().postData()?JSON.parse(route.request().postData()):null;} catch {}
   if(method!=="GET"&&!q.startsWith("/api/auth/"))calls.push({q,method,data});
   let body=[],status=200;
   if(q==="/api/auth/refresh/") body={access:token};
   else if(q==="/api/auth/me/") body=profile;
   else if(q==="/api/workspaces/"+tid+"/")body={id:tid,name:"QA Workspace",slug:"qa-workspace",status:"active",role,my_role:role,plan:"business",domain_count:1,mailbox_count:1,member_count:1,approved_at:now,review_reason:"",outbound_disabled:false,created_at:now};
   else if(q==="/api/workspaces/"+tid+"/stats/")body={my_role:role,tenant_status:"active",member_count:1,domain_count:1,mailbox_count:1};
   else if(q==="/api/workspaces/"+tid+"/onboarding/")body={workspace_created:true,domain_added:true,dns_verified:true,first_mailbox_created:true,completed:true,completed_at:now};
   else if(q==="/api/mailboxes/")body=[mailbox];
   else if(q==="/api/queue/")body=[{id:"q1111111-1111-4111-8111-111111111111",engine_message_id:"synthetic",sender:"owner@example.test",recipient:"recipient@example.org",subject:"Synthetic queue mail",status:"deferred",status_display:"Deferred",reason:"Temporary remote response",queued_at:now,last_retry:null,next_retry:null,retry_count:1}];
   else if(q==="/api/quarantine/")body=[{id:"q2222222-2222-4222-8222-222222222222",engine_message_id:"synthetic",sender:"suspicious@example.org",recipient:"owner@example.test",subject:"Synthetic spam mail",spam_score:"10.2",status:"held",status_display:"Held",received_at:now,actioned_at:null}];
   else if(q==="/api/backups/")body=[{id:"b1111111-1111-4111-8111-111111111111",scope:"workspace",status:"completed",size_mb:100,storage_location:"synthetic",error_message:"",started_at:now,completed_at:now,created_at:now,restore_metadata:{},duration_seconds:23}];
   else if(q==="/api/logs/")body=[{id:"l1111111-1111-4111-8111-111111111111",event_type:"domain_verified",source:"workspace",result:"success",ip_address:"192.0.2.1",metadata:{domain:"example.test"},created_at:now}];
   else if(q==="/api/billing/") {
     const plan={tier:"business",display_name:"Business",price_monthly:"20.00",max_domains:10,max_mailboxes:20,max_members:20,max_aliases:100,default_storage_per_mailbox_mb:1024,max_storage_per_mailbox_mb:10240,max_storage_total_mb:102400,max_messages_per_hour_per_mailbox:500,max_messages_per_day_per_tenant:10000,includes_spam_quarantine:true,includes_audit_logs:true,includes_queue_visibility:true,includes_backup_controls:true,includes_team_roles:true};
     const usage={domains:1,mailboxes:1,members:1,aliases:0,storage_allocated_mb:1024,storage_used_mb:100,max_domains:10,max_mailboxes:20,max_members:20,max_aliases:100,max_storage_per_mailbox_mb:10240,max_storage_total_mb:102400};
     body={subscription:{id:"sub-qa",plan,status:"active",status_display:"Active",trial_ends_at:null,current_period_start:now,current_period_end:"2026-11-01T00:00:00Z",created_at:now},usage,trial_days_left:0};
   }
   else if(q==="/api/custom-hostnames/"&&method==="GET")body=[];
   else if(q==="/api/custom-hostnames/"&&method==="POST") {body={id:"h1111111-1111-4111-8111-111111111111",hostname:data.hostname,surface:data.surface,status:"pending",cname_host:data.hostname,cname_target:"custom.matemail.pro",created_at:now,updated_at:now};status=201;}
   else if(q==="/api/teams/apikeys/"&&method==="GET"){if(role==="owner"||role==="admin")body=keys;else{status=403;body={detail:"Forbidden"}}}
   else if(q==="/api/teams/apikeys/"&&method==="POST"){if(role!=="owner"&&role!=="admin"){status=403;body={detail:"Forbidden"}}else{body={id:"k1111111-1111-4111-8111-111111111111",name:data.name,key_prefix:"qa",display:"qa-key",scopes:data.scopes,is_read_only:false,created_at:now,last_used_at:null,expires_at:null,is_active:true,key:"synthetic-fake-key"};keys.push(body);status=201}}
   else if(q==="/api/integrations/"&&method==="GET"){if(role==="owner"||role==="admin")body=integrations;else{status=403;body={detail:"Forbidden"}}}
   else if(q==="/api/integrations/"&&method==="POST"){if(role!=="owner"&&role!=="admin"){status=403;body={detail:"Forbidden"}}else{body={id:"i1111111-1111-4111-8111-111111111111",name:data.name,purpose:"sales_crm",mailbox_id:data.mailbox_id,mailbox_email:mailbox.email,permissions:data.permissions,tenant_id:tid,secret:"synthetic-fake-secret"};integrations.push(body);status=201}}
   else if(q.startsWith("/api/domains/")&&q.includes("/dmarc"))body={domain:"example.test",days:30,report_count:0,message_count:0,spf_pass:0,dkim_pass:0,dmarc_pass:0,top_sources:[],reports:[],notice:"No reports"};
   else if(q.startsWith("/api/domains/")&&q.includes("/tls-reports/"))body={domain:"example.test",days:30,report_count:0,successful_sessions:0,failed_sessions:0,failure_types:[],reports:[],notice:"No reports"};
   else if(q.startsWith("/api/integrations/authorize/")){status=400;body={detail:"No synthetic authorization request"}}
   await route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
 });
 const page=await context.newPage();page.on("pageerror",e=>errors.push(e.message));
 return{context,page,calls,errors};
}
async function open(t,route){
 await t.page.goto(base+route,{waitUntil:"domcontentloaded",timeout:60000});
 await t.page.locator(".astra-advanced-page").waitFor({state:"visible",timeout:30000});
 await t.page.waitForTimeout(330);
}
async function geom(t){
 return t.page.evaluate(()=>{const c=document.querySelector(".astra-advanced-page .portal-card");return {corner:c?getComputedStyle(c).borderRadius:null,scroll:document.documentElement.scrollWidth,width:innerWidth}});
}
async function image(t,name){await t.page.screenshot({path:path.join(output,name+".png"),fullPage:false,animations:"disabled"});}
try{
 const t=await make();
 const routes=[
  ["/app/queue","Mail queue"],["/app/spam","Spam & quarantine"],["/app/logs","Activity logs"],
  ["/app/backups","Backups"],["/app/billing","Billing & usage"],["/app/security","Account security"],
  ["/app/settings","Workspace settings"],["/app/settings/api-keys","API keys"],
  ["/app/settings/integrations","Connected Apps"],
  ["/app/domains/"+domainId+"/dmarc","DMARC reporting"],
  ["/app/domains/"+domainId+"/tls-reports","TLS failure reporting"]
 ];
 const results=[];
 for(let i=0;i<routes.length;i++){
   await open(t,routes[i][0]);
   await t.page.getByRole("heading",{name:routes[i][1],exact:true}).first().waitFor({state:"visible",timeout:8000});
   results.push({route:routes[i][0],...await geom(t)});
   if([0,1,5,6,7,8,9,10].includes(i))await image(t,"phase3-"+(i+1)+"-page");
 }
 check("Eleven advanced routes render without loading failures",results.length===11);
 check("Advanced card geometry respects 4px Astra design",results.filter(x=>x.corner!==null).every(x=>x.corner==="4px"),{geometry:results});
 check("Advanced desktop pages contain horizontal overflow",results.every(x=>x.scroll<=x.width+1),{geometry:results});
 await open(t,"/app/settings");
 check("Custom Hostnames keeps separate Hub and PostBox panels",await t.page.getByText("MateMail Hub",{exact:true}).count()>0&&await t.page.getByText("PostBox",{exact:true}).count()>0);
 check("Custom Hostnames retains CNAME migration guidance",await t.page.getByText("One CNAME per URL").count()===1);
 await open(t,"/app/settings/api-keys");
 await t.page.getByRole("button",{name:"New API key"}).click();
 const kd=t.page.getByRole("dialog",{name:"Create API key"});
 await kd.waitFor({state:"visible"});
 check("API key create uses native Astra dialog and accessible inputs",await kd.getByLabel("Key name").count()===1&&await kd.getByLabel("Expiry date (optional)").count()===1);
 await t.page.keyboard.press("Escape");
 check("Escape cancels credential issuance",!(await kd.isVisible())&&!t.calls.some(x=>x.q==="/api/teams/apikeys/"));
 await t.page.getByRole("button",{name:"New API key"}).click();
 await kd.getByLabel("Key name").fill("QA CI key");
 await kd.getByRole("button",{name:"Create key"}).click();
 await t.page.waitForTimeout(400);
 const key=t.calls.find(x=>x.q==="/api/teams/apikeys/"&&x.method==="POST");
 check("API key creation preserves read and scope controls",key?.data.name==="QA CI key"&&key.data.scopes.includes("read"),{payload:key?.data});
 // Never screenshot credential display, even synthetic data.
 await open(t,"/app/settings/integrations");
 await t.page.getByRole("button",{name:"Create connected app"}).click();
 const cd=t.page.getByRole("dialog",{name:"Create connected app"});
 await cd.waitFor({state:"visible"});
 check("Connected Apps modal has accessible name, purpose, mailbox",await cd.getByLabel("Application name").count()===1&&await cd.getByLabel("Purpose").count()===1&&await cd.getByLabel("Mailbox",{exact:true}).count()===1);
 await cd.getByLabel("Application name").fill("QA HelpDesk");
 await cd.getByRole("button",{name:"Create connected app"}).click();
 await t.page.waitForTimeout(400);
 const app=t.calls.find(x=>x.q==="/api/integrations/"&&x.method==="POST");
 check("Connected App credential bound to chosen mailbox and scopes",app?.data.name==="QA HelpDesk"&&app.data.mailbox_id===mailboxId&&Array.isArray(app.data.permissions),{payload:app?.data});
 check("Advanced desktop has no JS exceptions",t.errors.length===0,{errors:t.errors});
 await t.context.close();

 const viewer=await make("read_only",{width:390,height:844});
 await open(viewer,"/app/settings");
 await image(viewer,"phase3-mobile-readonly-settings");
 check("Read-only cannot add custom URL",await viewer.page.getByRole("button",{name:"Add hostname"}).count()===0);
 await open(viewer,"/app/settings/api-keys");
 check("Read-only cannot create API keys",await viewer.page.getByRole("button",{name:"New API key"}).isDisabled());
 await open(viewer,"/app/settings/integrations");
 check("Read-only cannot issue integrations",await viewer.page.getByRole("button",{name:"Create connected app"}).isDisabled());
 const mobile=await geom(viewer);
 check("Mobile credentials page does not overflow document",mobile.scroll<=391,mobile);
 check("Read-only performs no business mutations",viewer.calls.length===0,{calls:viewer.calls});
 check("Mobile read-only has no JS errors",viewer.errors.length===0,{errors:viewer.errors});
 await viewer.context.close();
}catch(e){report.error=e.stack||String(e);process.exitCode=1;}
finally{fs.writeFileSync(path.join(output,"phase3-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}

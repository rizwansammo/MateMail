/**
 * Phase 2E — joined user journeys across a single isolated synthetic tenant.
 * Browser requests to /api/ are intercepted; never uses production credentials.
 * All assertions guard UI wiring, NOT actual mail delivery or back-end storage.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {chromium} from "playwright";

const base=process.env.VISUAL_QA_URL||"http://127.0.0.1:3100";
const output=process.env.VISUAL_QA_OUTPUT||"visual-qa-artifacts";
fs.mkdirSync(output,{recursive:true});
const tenantId="d9bdde9d-1234-4123-8123-777777777777";
const d1="d1111111-1111-4111-8111-111111111111",d2="d2222222-2222-4222-8222-222222222222";
const m1="e1111111-1111-4111-8111-111111111111",m2="e2222222-2222-4222-8222-222222222222",m3="e3333333-3333-4333-8333-333333333333";
const tb="e5555555-5555-4555-8555-555555555555",fg="f5555555-5555-4555-8555-555555555555";
const uid="bd1a5653-1234-4234-8234-888888888888";
const token=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",Buffer.from(JSON.stringify({tenant_id:tenantId,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"qa-not-real-signature"].join(".");
const report={title:"Phase 2E — joined cross-feature user journeys",checks:[],notes:["Synthetic API only; live mail engine/DNS is not exercised."]};
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
function verify(name,pass,details={}){
 report.checks.push({name,pass:!!pass,details});
 assert.ok(pass,name+" "+JSON.stringify(details));
}
async function env(viewport={width:1440,height:900},role="owner"){
 const context=await browser.newContext({viewport,deviceScaleFactor:1,reducedMotion:"reduce"});
 const state={
  domains:[{id:d1,domain:"example.test",status:"active",ownership_status:"verified",ownership_verified:true,mail_service_ready:true,dns_health_score:100,added_at:"2026-01-01T00:00:00Z",verification_record_type:"TXT",verification_record_name:"_matemail-verification.example.test",verification_record_value:"test"}],
  mailboxes:[
   {id:m1,email:"amelia@example.test",full_name:"Amelia",local_part:"amelia",domain:d1,domain_name:"example.test",kind:"personal",status:"active",mail_service_ready:true,quota_mb:1024,storage_used_mb:20,created_at:"2026-01-01T00:00:00Z"},
   {id:m2,email:"oliver@example.test",full_name:"Oliver",local_part:"oliver",domain:d1,domain_name:"example.test",kind:"personal",status:"active",mail_service_ready:true,quota_mb:1024,storage_used_mb:30,created_at:"2026-01-01T00:00:00Z"}],
  teamBoxes:[{id:tb,email:"support@example.test",full_name:"Support",local_part:"support",domain:d1,domain_name:"example.test",kind:"team_box",status:"active",member_count:0,mail_service_ready:true,quota_mb:1024,storage_used_mb:15,members:[],created_at:"2026-01-01T00:00:00Z"}],
  groups:[{id:fg,address:"engineering@example.test",display_name:"Engineering",sender_policy:"anyone",status:"active",member_count:1,sender_count:0,mail_service_ready:true,members:[{id:"g1111111-1111-4111-8111-111111111111",mailbox_id:m1,email:"amelia@example.test",kind:"personal",role:"member"}],allowed_senders:[]}],
  aliases:[{id:"a1111111-1111-4111-8111-111111111111",source_address:"sales@example.test",domain:d1,domain_name:"example.test",destination_mailbox:m1,destination_email:"amelia@example.test",status:"active",mail_service_ready:true,created_at:"2026-01-01"}],
  rules:[{id:"f1111111-1111-4111-8111-111111111111",source_mailbox:m1,source_mailbox_email:"amelia@example.test",destination_email:"external@example.org",keep_copy:true,status:"active",mail_service_ready:true,created_at:"2026-01-01"}],
  grants:[],
  members:[{id:uid,email:"owner@example.test",full_name:"QA Owner",role:"owner",status:"active",created_at:"2026-01-01T00:00:00Z"},{id:"bd222222-2222-4222-8222-222222222222",email:"staff@example.test",full_name:"Staff",role:"support",status:"active",created_at:"2026-01-01T00:00:00Z"}],
  invites:[],calls:[]
 };
 await context.route("**/api/**",async route=>{
  const uri=new URL(route.request().url()),q=uri.pathname,method=route.request().method();
  const value=route.request().postData()?JSON.parse(route.request().postData()):null;
  if(method!=="GET"&&!q.startsWith("/api/auth/"))state.calls.push({q,method,value});
  let body=[],status=200;
  if(q==="/api/auth/refresh/")body={access:token};
  else if(q==="/api/auth/me/")body={id:uid,email:"owner@example.test",full_name:"QA Owner",email_verified:true,two_factor_enabled:false,is_platform_admin:false};
  else if(q==="/api/workspaces/"+tenantId+"/")body={id:tenantId,name:"QA Workspace",slug:"quality-assurance",status:"active",role,my_role:role,domain_count:state.domains.length,mailbox_count:state.mailboxes.length,member_count:state.members.length,plan:"business",created_at:"2026-01-01T00:00:00Z"};
  else if(q==="/api/workspaces/"+tenantId+"/stats/")body={my_role:role,tenant_status:"active",tenant_plan:"business",mailbox_count:state.mailboxes.length,active_mailbox_count:state.mailboxes.length,domain_count:state.domains.length,active_domain_count:1,member_count:state.members.length,storage_used_mb:100,storage_quota_mb:10240};
  else if(q==="/api/workspaces/"+tenantId+"/onboarding/")body={workspace_created:true,domain_added:true,dns_verified:true,first_mailbox_created:true,completed:true,completed_at:"2026-01-01T00:00:00Z"};
  else if(q==="/api/billing/")body={subscription:{plan:{display_name:"Business",default_storage_per_mailbox_mb:1024,max_storage_per_mailbox_mb:10240}}};
  else if(q==="/api/domains/"&&method==="GET")body=state.domains;
  else if(q==="/api/domains/"&&method==="POST"){
   const row={...state.domains[0],id:d2,domain:value.domain,status:"pending",ownership_verified:false,ownership_status:"pending",mail_service_ready:false,dns_health_score:0,verification_record_name:"_matemail-verification."+value.domain,verification_record_value:"matemail-verify=qa"};
   state.domains.push(row);body=row;status=201;
  }
  else if(q.startsWith("/api/domains/")&&q.endsWith("/records/"))body=[];
  else if(q.startsWith("/api/domains/")&&q.endsWith("/"))body=state.domains.find(d=>q.includes(d.id))||{};
  else if(q==="/api/mailboxes/"&&method==="GET")body=state.mailboxes;
  else if(q==="/api/mailboxes/"&&method==="POST"){
   const domain=state.domains.find(d=>d.id===value.domain_id);
   if(!domain?.ownership_verified){status=400;body={detail:"Domain must be verified"}}
   else{
    const row={...state.mailboxes[0],id:m3,email:value.local_part+"@"+domain.domain,local_part:value.local_part,full_name:value.full_name,domain:value.domain_id,domain_name:domain.domain,quota_mb:value.quota_mb,storage_used_mb:0};
    state.mailboxes.push(row);body=row;status=201;
   }
  }
  else if(q.startsWith("/api/mailboxes/")&&q.endsWith("/"))body=state.mailboxes.find(m=>q.includes(m.id))||{};
  else if(q==="/api/team-boxes/"&&method==="GET")body=state.teamBoxes;
  else if(q.startsWith("/api/team-boxes/")&&q.endsWith("/members/")&&method==="POST"){
   const member=state.mailboxes.find(m=>m.id===value.mailbox_id);
   const row={id:"cc111111-1111-4111-8111-111111111111",mailbox_id:value.mailbox_id,email:member?.email||"",full_name:member?.full_name||"",active:true,...value};
   state.teamBoxes[0].members.push(row);state.teamBoxes[0].member_count=state.teamBoxes[0].members.length;body=row;status=201;
  }
  else if(q.startsWith("/api/team-boxes/")&&q.endsWith("/"))body=state.teamBoxes.find(t=>q.includes(t.id))||{};
  else if(q==="/api/forward-groups/"&&method==="GET")body=state.groups;
  else if(q.startsWith("/api/forward-groups/")&&q.endsWith("/members/")&&method==="POST"){
   const member=state.mailboxes.find(m=>m.id===value.mailbox_id);
   const row={id:"dd111111-1111-4111-8111-111111111111",mailbox_id:value.mailbox_id,email:member?.email||"",full_name:member?.full_name||"",kind:"personal",role:value.role};
   state.groups[0].members.push(row);state.groups[0].member_count=state.groups[0].members.length;body=row;status=201;
  }
  else if(q.startsWith("/api/forward-groups/")&&q.endsWith("/"))body=state.groups.find(g=>q.includes(g.id))||{};
  else if(q==="/api/aliases/"&&method==="GET")body=state.aliases;
  else if(q==="/api/aliases/"&&method==="POST"){
   const m=state.mailboxes.find(m=>m.id===value.destination_mailbox_id);
   const row={...state.aliases[0],id:"a2222222-2222-4222-8222-222222222222",source_address:value.source_local_part+"@example.test",destination_mailbox:m.id,destination_email:m.email};
   state.aliases.push(row);body=row;status=201;
  }
  else if(q==="/api/forwarding/"&&method==="GET")body=state.rules;
  else if(q==="/api/forwarding/"&&method==="POST"){
   const m=state.mailboxes.find(m=>m.id===value.source_mailbox_id);
   const row={...state.rules[0],id:"f2222222-2222-4222-8222-222222222222",source_mailbox:value.source_mailbox_id,source_mailbox_email:m?.email||"",destination_email:value.destination_email,keep_copy:value.keep_copy};
   state.rules.push(row);body=row;status=201;
  }
  else if(q==="/api/delegations/"&&method==="GET")body=state.grants;
  else if(q==="/api/delegations/"&&method==="POST"){
   const target=state.mailboxes.find(m=>m.id===value.target_mailbox_id),delegate=state.mailboxes.find(m=>m.id===value.delegate_mailbox_id);
   const row={id:"bb111111-1111-4111-8111-111111111111",target_mailbox_id:target?.id,target_email:target?.email,target_name:target?.full_name,delegate_mailbox_id:delegate?.id,delegate_email:delegate?.email,delegate_name:delegate?.full_name,...value,active:true};
   state.grants.push(row);body=row;status=201;
  }
  else if(q==="/api/workspaces/"+tenantId+"/members/")body=state.members;
  else if(q==="/api/teams/invites/"&&method==="GET")body=role==="owner"||role==="admin"?state.invites:{detail:"Forbidden"};
  else if(q==="/api/teams/invites/"&&method==="POST"){
   if(role==="owner"||role==="admin"){
    body={id:"bb222222-2222-4222-8222-222222222222",email:value.email,role:value.role,invited_by_email:"owner@example.test",created_at:"2026-01-01T00:00:00Z",expires_at:"2027-01-01T00:00:00Z",accepted_at:null,is_pending:true,is_revoked:false,email_delivered:true};state.invites.push(body);status=201;
   }else{body={detail:"Forbidden"};status=403}
  }
  await route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
 });
 const page=await context.newPage(),errors=[];page.on("pageerror",e=>errors.push(e.message));
 return {context,page,state,errors};
}
async function open(page,route){
 await page.goto(base+route,{waitUntil:"domcontentloaded",timeout:60000});
 await page.locator(".astra-hub").waitFor({state:"visible",timeout:30000});
 await page.waitForTimeout(200);
}
async function shot(page,name){await page.screenshot({path:path.join(output,name+".png"),fullPage:false,animations:"disabled"});}
async function box(page){
 return page.evaluate(()=>{
  const el=document.querySelector(".astra-resource-page .portal-card");
  const tab=document.querySelector(".astra-resource-page table th");
  return {radius:el?getComputedStyle(el).borderRadius:null,tableHeader:tab?Math.round(tab.getBoundingClientRect().height):null,documentWidth:document.documentElement.scrollWidth,viewport:innerWidth};
 });
}
try{
 const t=await env();
 const p=t.page;
 await open(p,"/app/domains");
 verify("Global navigation and Domains route render",await p.getByRole("heading",{name:"Domains",exact:true}).count()===1);
 await p.getByRole("button",{name:"Add domain"}).first().click();
 const domainDialog=p.getByRole("dialog",{name:"Connect a new domain"});
 await domainDialog.getByLabel("Root domain").fill("not-verified.test");
 await domainDialog.getByRole("button",{name:"Add domain"}).click();
 await p.waitForTimeout(350);
 verify("New domain remains pending and not ownership-verified",t.state.domains.some(x=>x.domain==="not-verified.test"&&!x.ownership_verified));
 await open(p,"/app/mailboxes");
 await p.getByRole("button",{name:"Create mailbox"}).first().click();
 const mailboxDialog=p.getByRole("dialog",{name:"Create a mailbox"});
 const domainOptions=await mailboxDialog.locator("select option").allTextContents();
 verify("Mailbox creation excludes unverified domain",!domainOptions.some(x=>x.includes("not-verified.test")),{options:domainOptions});
 await mailboxDialog.getByLabel("Email address").fill("newteam");
 await mailboxDialog.getByLabel("Display name").fill("New Team");
 await mailboxDialog.getByLabel("Temporary password").fill("qa-strong-Password-246810");
 await mailboxDialog.getByRole("button",{name:"Create mailbox"}).click();
 await p.getByText("newteam@example.test").waitFor({timeout:8000});
 verify("New mailbox appears and uses only verified domain",t.state.mailboxes.length===3&&t.state.mailboxes.at(-1).id===m3);
 await shot(p,"phase2e-01-mailboxes-after-domain");

 await open(p,"/app/aliases");
 await p.getByRole("button",{name:"Create alias"}).first().click();
 const am=p.getByRole("dialog",{name:"Create an alias"});
 const aliasOptions=await am.getByLabel("Mailbox").locator("option").allTextContents();
 verify("New mailbox available for alias destination",aliasOptions.some(x=>x.includes("newteam@example.test")),{options:aliasOptions});
 await am.getByLabel("Alias address").fill("accounts");
 await am.getByLabel("Mailbox").selectOption(m3);
 await am.getByRole("button",{name:"Create alias"}).click();
 await p.getByText("accounts@example.test").waitFor({timeout:8000});
 verify("Alias assigned to newly created mailbox",t.state.aliases.at(-1)?.destination_mailbox===m3);
 await shot(p,"phase2e-02-alias-linked");

 await open(p,"/app/forwarding");
 await p.getByRole("button",{name:"Add forwarding rule"}).first().click();
 const fm=p.getByRole("dialog",{name:"Add a forwarding rule"});
 const sourceOptions=await fm.getByLabel("Source mailbox").locator("option").allTextContents();
 verify("New mailbox available for forwarding",sourceOptions.some(x=>x.includes("newteam@example.test")),{options:sourceOptions});
 await fm.getByLabel("Source mailbox").selectOption(m3);
 await fm.getByLabel("Forward to").fill("inbox@external.example");
 await fm.getByRole("button",{name:"Create rule"}).click();
 await p.getByText("inbox@external.example").waitFor({timeout:8000});
 verify("Forwarding source uses same new mailbox",t.state.rules.at(-1)?.source_mailbox===m3);
 await shot(p,"phase2e-03-forwarding-linked");

 await open(p,"/app/delegation");
 await p.getByRole("button",{name:"Add delegation"}).click();
 const dm=p.getByRole("dialog",{name:"Add mailbox delegation"});
 const targetOptions=await dm.getByLabel("Mailbox to delegate").locator("option").allTextContents();
 verify("Delegation picker includes newly created personal mailbox",targetOptions.some(x=>x.includes("newteam@example.test")),{options:targetOptions});
 await dm.getByLabel("Mailbox to delegate").selectOption(m3);
 await dm.getByLabel("Delegate mailbox").selectOption(m1);
 await dm.getByRole("button",{name:"Add delegation"}).click();
 await p.waitForTimeout(400);
 verify("Delegation grant targets the newly created mailbox",t.state.grants.length===1&&t.state.grants[0].target_mailbox_id===m3);
 await shot(p,"phase2e-04-delegation-linked");

 await open(p,"/app/team-boxes/"+tb);
 const opts=await p.locator("select").allTextContents();
 verify("TeamBox membership selection sees latest mailbox",opts.some(x=>x.includes("newteam@example.test")),{selects:opts.slice(0,4)});
 await shot(p,"phase2e-05-team-box-member-choices");
 await open(p,"/app/forward-groups");
 await p.getByRole("button",{name:"Create Forward Group"}).first().click();
 const gm=p.getByRole("dialog",{name:"Create a Forward Group"});
 verify("New personal mailbox appears in Forward Group recipients",await gm.getByText("newteam@example.test").count()>0);
 await p.keyboard.press("Escape");

 await open(p,"/app/team");
 await p.getByRole("button",{name:"Invite member"}).first().click();
 const invite=p.getByRole("dialog",{name:"Invite member"});
 verify("Membership invite clearly not automatic mailbox provisioning",await invite.getByText(/does not create or assign a mailbox/).count()===1);
 await invite.getByLabel("Email address").fill("newstaff@example.test");
 await invite.getByLabel("Workspace role").selectOption("support");
 await invite.getByRole("button",{name:"Send invitation"}).click();
 await p.getByText("newstaff@example.test").waitFor({timeout:8000});
 verify("Creating workspace invitation does not increase mailboxes",t.state.mailboxes.length===3);
 await shot(p,"phase2e-06-membership-no-mailbox");

 await p.getByRole("button",{name:/Search workspace/}).click();
 const search=p.getByRole("dialog",{name:"Search Hub"});
 await search.getByRole("textbox",{name:"Search Hub pages and resources"}).fill("newteam");
 await p.getByRole("group",{name:"Mailboxes"}).getByRole("option",{name:"newteam@example.test"}).waitFor({timeout:8000});
 await shot(p,"phase2e-07-search-new-mailbox");
 await p.getByRole("group",{name:"Mailboxes"}).getByRole("option",{name:"newteam@example.test"}).click();
 await p.waitForURL("**/app/mailboxes/"+m3,{timeout:10000});
 verify("Global Search resource navigation opens correct detail route",p.url().endsWith("/app/mailboxes/"+m3));

 const routes=["/app/mailboxes","/app/domains","/app/team-boxes","/app/forward-groups","/app/aliases","/app/forwarding","/app/delegation","/app/team"];
 const radii=[];
 for(let i=0;i<routes.length;i++){
  await open(p,routes[i]);const m=await box(p);
  radii.push({route:routes[i],...m});
 }
 verify("Eight core Astra feature pages share 4px card styling",radii.every(x=>x.radius==="4px"),{radii});
 verify("Eight core tables share 41px header style",radii.every(x=>x.tableHeader===41),{radii});
 verify("Desktop no page-wide horizontal scrolling",radii.every(x=>x.documentWidth<=x.viewport+1),{radii});
 verify("No client-side exceptions across linked feature journey",t.errors.length===0,{errors:t.errors.slice(0,6)});
 await t.context.close();

 const read=await env({width:390,height:844},"read_only");
 await open(read.page,"/app/team");
 verify("Read-only users cannot invoke invitation",await read.page.getByRole("button",{name:"Invite member"}).count()===0);
 verify("Read-only users cannot use role editing",await read.page.getByRole("combobox",{name:/Role for/}).count()===0);
 await read.page.getByRole("button",{name:/Search workspace/}).click();
 await read.page.getByRole("textbox",{name:"Search Hub pages and resources"}).fill("delegation");
 verify("Restricted Delegation not visible in read-only global search",await read.page.getByRole("option",{name:"Delegation"}).count()===0);
 await read.page.keyboard.press("Escape");
 await shot(read.page,"phase2e-08-mobile-read-only");
 verify("Mobile read-only page no global overflow",await read.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 verify("No client JS errors as read-only",read.errors.length===0,{errors:read.errors.slice(0,4)});
 await read.context.close();
}catch(error){report.error=error.stack||String(error);process.exitCode=1;}
finally{fs.writeFileSync(path.join(output,"phase2e-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}

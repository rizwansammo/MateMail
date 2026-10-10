/**
 * Phase 4 global command search — isolated tenant-scoped real-browser QA.
 * No live credentials or production mail: all /api/ intercepted.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base=process.env.VISUAL_QA_URL||"http://127.0.0.1:3100";
const dir=process.env.VISUAL_QA_OUTPUT||"visual-qa-artifacts";
fs.mkdirSync(dir,{recursive:true});
const tenant="d9bdde9d-1234-4123-8123-777777777777";
const userId="bd1a5653-1234-4234-8234-888888888888";
const mailboxId="e1111111-1111-4111-8111-111111111111", otherId="e2222222-2222-4222-8222-222222222222";
const domainId="d1111111-1111-4111-8111-111111111111";
const token=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",Buffer.from(JSON.stringify({tenant_id:tenant,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"not-a-real-signature"].join(".");
const report={phase:"Phase 4 comprehensive Global Search",checks:[],note:"Browser API intercepted, never production."};
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
function check(name,ok,info={}){report.checks.push({name,passed:!!ok,info});assert.ok(ok,name+" "+JSON.stringify(info));}
async function fixture(role="owner",viewport={width:1440,height:900},failedSource=""){
 const context=await browser.newContext({viewport,reducedMotion:"reduce"});
 const calls=[],errors=[];
 const mailbox=(id,email,full_name)=>({id,email,full_name,local_part:email.split("@")[0],domain:domainId,domain_name:"example.test",kind:"personal",status:"active",mail_service_ready:true,quota_mb:1024,storage_used_mb:50,created_at:"2026-01-01T00:00:00Z"});
 const mailboxes=[mailbox(mailboxId,"alice@example.test","Alice Cooper"),mailbox(otherId,"bob@example.test","Bob Smith")];
 const domains=[{id:domainId,domain:"example.test",status:"active",ownership_verified:true,ownership_status:"verified",mail_service_ready:true,dns_health_score:100,created_at:"2026-01-01T00:00:00Z"}];
 const aliases=[{id:"a1111111-1111-4111-8111-111111111111",source_address:"sales@example.test",destination_email:"alice@example.test",domain:domainId,domain_name:"example.test",destination_mailbox:mailboxId,status:"active",mail_service_ready:true,created_at:"2026-01-01"},{id:"a2222222-2222-4222-8222-222222222222",source_address:"billing@example.test",destination_email:"bob@example.test",domain:domainId,domain_name:"example.test",destination_mailbox:otherId,status:"active",mail_service_ready:true,created_at:"2026-01-01"}];
 const forwarding=[{id:"f1111111-1111-4111-8111-111111111111",source_mailbox:mailboxId,source_mailbox_email:"alice@example.test",destination_email:"supplier@outside.test",status:"active",keep_copy:true,mail_service_ready:true,created_at:"2026-01-01"},{id:"f2222222-2222-4222-8222-222222222222",source_mailbox:otherId,source_mailbox_email:"bob@example.test",destination_email:"other@outside.test",status:"active",keep_copy:true,mail_service_ready:true,created_at:"2026-01-01"}];
 const delegations=[{id:"b1111111-1111-4111-8111-111111111111",target_mailbox_id:mailboxId,target_email:"alice@example.test",target_name:"Alice Cooper",delegate_mailbox_id:otherId,delegate_email:"bob@example.test",delegate_name:"Bob Smith",can_read:true,can_manage:true,can_send_as:false,can_send_on_behalf:false,active:true}];
 const members=[{id:userId,email:"owner@example.test",full_name:"QA Owner",role:"owner",status:"active",created_at:"2026-01-01T00:00:00Z"},{id:"u2222222-2222-4222-8222-222222222222",email:"bob@example.test",full_name:"Bob Smith",role:"support",status:"active",created_at:"2026-01-01T00:00:00Z"}];
 await context.route("**/api/**",async route=>{
  const endpoint=new URL(route.request().url()).pathname,method=route.request().method();
  calls.push(endpoint);
  let body=[],status=200;
  if(endpoint==="/api/auth/refresh/")body={access:token};
  else if(endpoint==="/api/auth/me/")body={id:userId,email:"owner@example.test",full_name:"QA Owner",email_verified:true,two_factor_enabled:false,is_platform_admin:false};
  else if(endpoint==="/api/workspaces/"+tenant+"/")body={id:tenant,name:"QA Workspace",slug:"qa",status:"active",role,my_role:role};
  else if(endpoint==="/api/workspaces/"+tenant+"/stats/")body={my_role:role,tenant_status:"active",tenant_plan:"business"};
  else if(endpoint==="/api/workspaces/"+tenant+"/onboarding/")body={workspace_created:true,domain_added:true,dns_verified:true,first_mailbox_created:true,completed:true,completed_at:"2026-01-01"};
  else if(endpoint==="/api/billing/")body={subscription:{plan:{display_name:"Business",default_storage_per_mailbox_mb:1024,max_storage_per_mailbox_mb:10240}}};
  else if(endpoint==="/api/domains/")body=domains;
  else if(endpoint==="/api/mailboxes/")body=mailboxes;
  else if(endpoint.startsWith("/api/mailboxes/")&&endpoint.endsWith("/"))body=mailboxes.find(m=>endpoint.includes(m.id))||{};
  else if(endpoint==="/api/team-boxes/")body=[{id:"t1111111-1111-4111-8111-111111111111",email:"support@example.test",full_name:"Support team",member_count:1,status:"active",mail_service_ready:true,quota_mb:1024,storage_used_mb:50}];
  else if(endpoint==="/api/forward-groups/")body=[{id:"g1111111-1111-4111-8111-111111111111",address:"engineering@example.test",display_name:"Engineering",member_count:1,status:"active",mail_service_ready:true}];
  else if(endpoint==="/api/aliases/")body=aliases;
  else if(endpoint==="/api/forwarding/")body=forwarding;
  else if(endpoint==="/api/delegations/")body=role==="owner"||role==="admin"?delegations:[];
  else if(endpoint==="/api/workspaces/"+tenant+"/members/")body=members;
  else if(endpoint==="/api/teams/invites/")body=[];
  if(endpoint===failedSource){status=503;body={detail:"Service not available"};}
  await route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
 });
 const page=await context.newPage();
 page.on("pageerror",e=>errors.push(e.message));
 return {context,page,calls,errors};
}
async function open(t,p="/app"){
 await t.page.goto(base+p,{waitUntil:"domcontentloaded",timeout:60000});
 await t.page.locator(".astra-hub").waitFor({state:"visible",timeout:30000});
}
async function search(t,needle){
 await t.page.getByRole("button",{name:"Search workspace"}).click();
 const dialog=t.page.getByRole("dialog",{name:"Search Hub"});
 await dialog.getByRole("textbox",{name:"Search Hub pages and resources"}).fill(needle);
 return dialog;
}
async function snap(t,name){await t.page.screenshot({path:path.join(dir,name+".png"),animations:"disabled",fullPage:false});}
try{
 const t=await fixture();
 await open(t);
 await t.page.keyboard.press("Control+k");
 let dialog=t.page.getByRole("dialog",{name:"Search Hub"});
 await dialog.waitFor({state:"visible"});
 check("Ctrl+K opens scoped global search",await dialog.count()===1);
 const geom=await dialog.boundingBox();
 check("Astra modal remains 512px wide",Math.round(geom?.width||0)===512,{rect:geom});
 await dialog.getByRole("textbox",{name:"Search Hub pages and resources"}).fill("sales@example.test");
 await dialog.getByRole("option",{name:"sales@example.test"}).waitFor({timeout:9000});
 await snap(t,"phase4-01-cross-resource-alias");
 check("Aliases added to Global Search",await dialog.getByRole("option",{name:"sales@example.test"}).count()===1);
 await dialog.getByRole("option",{name:"sales@example.test"}).click();
 await t.page.waitForURL("**/app/aliases?q=*",{timeout:8000});
 await t.page.getByRole("searchbox",{name:"Search aliases"}).waitFor({timeout:8000});
 check("Alias result routes to query-filtered list",await t.page.getByRole("searchbox",{name:"Search aliases"}).inputValue()==="sales@example.test");
 check("Alias list shows matching row only",await t.page.getByText("sales@example.test").count()>0&&await t.page.getByText("billing@example.test").count()===0);
 await open(t,"/app/team");
 dialog=await search(t,"Bob Smith");
 await dialog.getByRole("option",{name:"bob@example.test"}).waitFor({timeout:7000});
 await snap(t,"phase4-02-user-search");
 await dialog.getByRole("option",{name:"bob@example.test"}).click();
 await t.page.waitForURL("**/app/team?q=*",{timeout:8000});
 check("Member full name matches while deep-link filters by email",await t.page.getByRole("searchbox",{name:"Search members"}).inputValue()==="bob@example.test");
 check("Member filtered list excludes unrelated accounts",await t.page.getByText("bob@example.test").count()>0);
 await open(t,"/app/forwarding");
 dialog=await search(t,"supplier@outside.test");
 await dialog.getByRole("option",{name:"alice@example.test"}).waitFor({timeout:8000});
 await dialog.getByRole("option",{name:"alice@example.test"}).click();
 await t.page.waitForURL("**/app/forwarding?q=*",{timeout:8000});
 check("Forwarding destination search resolves to correct source record",await t.page.getByRole("searchbox",{name:"Search forwarding rules"}).inputValue()==="alice@example.test");
 await open(t,"/app/delegation");
 dialog=await search(t,"Bob Smith");
 await dialog.getByRole("option",{name:"alice@example.test"}).waitFor({timeout:8000});
 await dialog.getByRole("option",{name:"alice@example.test"}).click();
 await t.page.waitForURL("**/app/delegation?q=*",{timeout:8000});
 check("Delegation searches delegate and filters by target",await t.page.getByRole("searchbox",{name:"Search delegations"}).inputValue()==="alice@example.test");
 await open(t,"/app/mailboxes");
 dialog=await search(t,"alice@example.test");
 await dialog.getByRole("group",{name:"Mailboxes"}).getByRole("option",{name:"alice@example.test"}).waitFor({timeout:8000});
 await dialog.getByRole("group",{name:"Mailboxes"}).getByRole("option",{name:"alice@example.test"}).click();
 await t.page.waitForURL("**/app/mailboxes/"+mailboxId,{timeout:8000});
 check("Mailbox search navigates to exact detail route",t.page.url().endsWith(mailboxId));
 await open(t);
 dialog=await search(t,"API keys");
 check("Admin-only page shortcut is discoverable to owner",await dialog.getByRole("option",{name:"API keys"}).count()===1);
 await dialog.getByRole("textbox",{name:"Search Hub pages and resources"}).fill("nothing-matches-here");
 check("No results message is shown",await dialog.getByText("No matching results.").count()===1);
 const first=dialog.getByRole("textbox",{name:"Search Hub pages and resources"});
 const last=dialog.getByRole("button",{name:"Close search"});
 await first.focus();await t.page.keyboard.press("Shift+Tab");
 check("Search dialog wraps Shift+Tab to final focusable control",await last.evaluate(el=>el===document.activeElement));
 await t.page.keyboard.press("Tab");
 check("Search dialog wraps Tab back to search input",await first.evaluate(el=>el===document.activeElement));
 await t.page.keyboard.press("Escape");
 check("Escape closes dialog and restores original trigger",await dialog.count()===0&&await t.page.getByRole("button",{name:"Search workspace"}).evaluate(el=>el===document.activeElement));
 check("No desktop JS exceptions",t.errors.length===0,{errors:t.errors});
 await t.context.close();

 const readonly=await fixture("read_only",{width:390,height:844});
 await open(readonly,"/app/team");
 dialog=await search(readonly,"Delegation");
 check("Read-only cannot find Delegation page",await dialog.getByRole("option",{name:"Delegation"}).count()===0);
 await dialog.getByRole("textbox",{name:"Search Hub pages and resources"}).fill("API keys");
 check("Read-only cannot find API keys",await dialog.getByRole("option",{name:"API keys"}).count()===0);
 await dialog.getByRole("textbox",{name:"Search Hub pages and resources"}).fill("alice@example.test");
 await dialog.getByRole("option",{name:"alice@example.test"}).first().waitFor({timeout:8000});
 await snap(readonly,"phase4-03-mobile-search");
 check("Read-only search never calls Delegation API",!readonly.calls.includes("/api/delegations/"));
 check("Read-only mobile has no wide document",await readonly.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 check("Mobile has no JS exceptions",readonly.errors.length===0,{errors:readonly.errors});
 await readonly.context.close();

 const partial=await fixture("owner",{width:1440,height:900},"/api/forwarding/");
 await open(partial);
 dialog=await search(partial,"engineering@example.test");
 await dialog.getByRole("option",{name:"engineering@example.test"}).waitFor({timeout:8000});
 check("Partial API failure retains working resource results",await dialog.getByRole("option",{name:"engineering@example.test"}).count()===1);
 await dialog.getByText("Some resources are unavailable. Page search is still working.").waitFor({timeout:8000});
 check("Partial failure is communicated without leaking technical response",await dialog.getByText("Some resources are unavailable. Page search is still working.").count()===1);
 await snap(partial,"phase4-04-partial-api-outage");
 await partial.context.close();
}catch(e){report.error=e.stack||String(e);process.exitCode=1;}
finally{fs.writeFileSync(path.join(dir,"phase4-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}

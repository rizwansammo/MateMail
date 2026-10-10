/**
 * Phase 2C isolated Playwright workflow tests.
 * Uses synthetic authenticated tenant data only; no live mail/DNS/SMTP.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base=process.env.VISUAL_QA_URL||"http://127.0.0.1:3100";
const out=process.env.VISUAL_QA_OUTPUT||"visual-qa-artifacts";
fs.mkdirSync(out,{recursive:true});
const tid="d9bdde9d-1234-4123-8123-777777777777";
const m1="e1111111-1111-4111-8111-111111111111";
const m2="e2222222-2222-4222-8222-222222222222";
const d1="d1111111-1111-4111-8111-111111111111";
const a1="a1111111-1111-4111-8111-111111111111";
const f1="f1111111-1111-4111-8111-111111111111";
const g1="b1111111-1111-4111-8111-111111111111";
const token=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",Buffer.from(JSON.stringify({tenant_id:tid,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"synthetic-signature"].join(".");
const report={phase:"Phase 2C: aliases, forwarding, delegation",checks:[]};
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
function check(name,pass,details={}){report.checks.push({name,passed:!!pass,details});assert.ok(pass,name+": "+JSON.stringify(details));}
async function make(viewport,role="owner"){
 const ctx=await browser.newContext({viewport,deviceScaleFactor:1,reducedMotion:"reduce"});
 const calls=[];
 const domains=[{id:d1,domain:"example.test",ownership_verified:true,mail_service_ready:true,status:"active"}];
 const mailboxes=[
  {id:m1,email:"amelia@example.test",full_name:"Amelia",kind:"personal",status:"active",mail_service_ready:true},
  {id:m2,email:"oliver@example.test",full_name:"Oliver",kind:"personal",status:"active",mail_service_ready:true}
 ];
 const aliases=[{id:a1,source_address:"sales@example.test",domain:d1,domain_name:"example.test",destination_mailbox:m1,destination_email:"amelia@example.test",status:"active",mail_service_ready:true,created_at:"2026-01-01"}];
 const rules=[{id:f1,source_mailbox:m1,source_mailbox_email:"amelia@example.test",destination_email:"external@example.org",keep_copy:true,status:"active",mail_service_ready:true,created_at:"2026-01-01"}];
 const grants=[{id:g1,target_mailbox_id:m1,target_email:"amelia@example.test",target_name:"Amelia",delegate_mailbox_id:m2,delegate_email:"oliver@example.test",delegate_name:"Oliver",can_read:true,can_manage:false,can_send_as:false,can_send_on_behalf:false,active:true}];
 await ctx.route("**/api/**",async route=>{
   const endpoint=new URL(route.request().url()).pathname,method=route.request().method();
   const payload=route.request().postData()?JSON.parse(route.request().postData()):null;
   if(method!=="GET")calls.push({endpoint,method,payload});
   let response=[],status=200;
   if(endpoint==="/api/auth/refresh/")response={access:token};
   else if(endpoint==="/api/auth/me/")response={id:"bd1a5653-1234-4234-8234-888888888888",email:"qa@example.test",full_name:"QA Owner",email_verified:true,two_factor_enabled:false,is_platform_admin:false};
   else if(endpoint==="/api/workspaces/"+tid+"/")response={id:tid,name:"QA Workspace",slug:"quality-assurance",status:"active",role,my_role:role};
   else if(endpoint==="/api/workspaces/"+tid+"/stats/")response={my_role:role,tenant_status:"active",mailbox_count:2,domain_count:1,member_count:1};
   else if(endpoint==="/api/workspaces/"+tid+"/onboarding/")response={workspace_created:true,domain_added:true,dns_verified:true,first_mailbox_created:true,completed:true,completed_at:"2026-01-01T00:00:00Z"};
   else if(endpoint==="/api/domains/")response=domains;
   else if(endpoint==="/api/mailboxes/")response=mailboxes;
   else if(endpoint==="/api/aliases/"&&method==="GET")response=aliases;
   else if(endpoint==="/api/aliases/"&&method==="POST"){
      const row={...aliases[0],id:"a2222222-2222-4222-8222-222222222222",source_address:payload.source_local_part+"@example.test",destination_mailbox:payload.destination_mailbox_id,destination_email:mailboxes.find(x=>x.id===payload.destination_mailbox_id)?.email||""};
      aliases.push(row);response=row;
   }
   else if(endpoint.startsWith("/api/aliases/")&&endpoint.endsWith("/status/")&&method==="PATCH"){
      const row=aliases.find(x=>endpoint.includes(x.id));
      if(row){row.status=payload.status;response={...row}}else status=404;
   }
   else if(endpoint.startsWith("/api/aliases/")&&method==="DELETE"){const i=aliases.findIndex(x=>endpoint.includes(x.id));if(i>=0)aliases.splice(i,1);response={};status=204;}
   else if(endpoint==="/api/forwarding/"&&method==="GET")response=rules;
   else if(endpoint==="/api/forwarding/"&&method==="POST"){
      const row={...rules[0],id:"f2222222-2222-4222-8222-222222222222",source_mailbox:payload.source_mailbox_id,source_mailbox_email:mailboxes.find(x=>x.id===payload.source_mailbox_id)?.email||"",destination_email:payload.destination_email,keep_copy:payload.keep_copy};
      rules.push(row);response=row;
   }
   else if(endpoint.startsWith("/api/forwarding/")&&endpoint.endsWith("/status/")&&method==="PATCH"){
      const row=rules.find(x=>endpoint.includes(x.id));
      if(row){row.status=payload.status;response={...row}}else status=404;
   }
   else if(endpoint==="/api/delegations/"&&role!=="owner"&&role!=="admin"){status=403;response={detail:"Forbidden"};}
   else if(endpoint==="/api/delegations/"&&method==="GET")response=grants;
   else if(endpoint==="/api/delegations/"&&method==="POST"){
      const row={...grants[0],id:"b2222222-2222-4222-8222-222222222222",target_mailbox_id:payload.target_mailbox_id,delegate_mailbox_id:payload.delegate_mailbox_id,can_read:payload.can_read,can_manage:payload.can_manage,can_send_as:payload.can_send_as,can_send_on_behalf:payload.can_send_on_behalf};
      grants.push(row);response=row;status=201;
   }
   else if(endpoint.startsWith("/api/delegations/")&&method==="PATCH"){
      const row=grants.find(x=>endpoint.includes(x.id));
      if(row){Object.assign(row,payload);response={...row};}else status=404;
   }
   await route.fulfill({status,contentType:"application/json",body:JSON.stringify(response)});
 });
 const page=await ctx.newPage(),errors=[];
 page.on("pageerror",e=>errors.push(e.message));
 return {ctx,page,calls,aliases,rules,grants,errors};
}
async function open(page,part){
 await page.goto(base+"/app/"+part,{waitUntil:"domcontentloaded",timeout:60000});
 await page.locator(".astra-routing-page").waitFor({state:"visible",timeout:30000});
 await page.waitForTimeout(450);
}
async function shot(page,name){await page.screenshot({path:path.join(out,name+".png"),fullPage:false,animations:"disabled"});}
async function measure(page){return page.evaluate(()=>{const card=document.querySelector(".astra-routing-page .portal-card"),th=document.querySelector(".astra-routing-page table th"),modal=document.querySelector("dialog.astra-resource-dialog");return{card:card?getComputedStyle(card).borderRadius:null,head:th?Math.round(th.getBoundingClientRect().height):null,modal:modal?getComputedStyle(modal).borderRadius:null,scroll:document.documentElement.scrollWidth,viewport:innerWidth}})}
try{
 const t=await make({width:1440,height:900});
 await open(t.page,"aliases");
 await shot(t.page,"phase2c-01-aliases");
 let m=await measure(t.page);
 check("Aliases list uses live API and Astra table",await t.page.getByText("sales@example.test").count()>0&&m.card==="4px"&&m.head===41,m);
 const aliasButton=t.page.getByRole("button",{name:"Create alias"}).first();
 await aliasButton.click();
 const am=t.page.getByRole("dialog",{name:"Create an alias"});
 await am.waitFor({state:"visible"});
 await shot(t.page,"phase2c-02-alias-create");
 check("Alias dialog is 4px native dialog",(await measure(t.page)).modal==="4px");
 await t.page.keyboard.press("Escape");
 check("Alias escape closes without mutation",!(await am.isVisible())&&!t.calls.some(x=>x.endpoint.startsWith("/api/aliases/")));
 await aliasButton.click();
 await am.getByLabel("Alias address").fill("accounts");
 await am.getByLabel("Mailbox").selectOption(m2);
 await am.getByRole("button",{name:"Create alias"}).click();
 await t.page.waitForTimeout(450);
 const aliasCall=t.calls.find(x=>x.endpoint==="/api/aliases/"&&x.method==="POST");
 check("Alias POST preserves verified-domain and personal mailbox IDs",aliasCall?.payload.source_local_part==="accounts"&&aliasCall?.payload.domain_id===d1&&aliasCall?.payload.destination_mailbox_id===m2,{call:aliasCall});
 check("Alias list refreshes after create",await t.page.getByText("accounts@example.test").count()>0);
 await t.page.getByRole("button",{name:"Disable sales@example.test"}).click();
 await t.page.waitForTimeout(350);
 const toggled=t.calls.find(x=>x.endpoint==="/api/aliases/"+a1+"/status/"&&x.method==="PATCH");
 check("Alias status PATCH remains functional",toggled?.payload.status==="disabled",{call:toggled});

 await open(t.page,"forwarding");
 await shot(t.page,"phase2c-03-forwarding");
 m=await measure(t.page);
 check("Forwarding list uses actual endpoints and Astra headers",await t.page.getByText("external@example.org").count()>0&&m.card==="4px"&&m.head===41,m);
 await t.page.getByRole("button",{name:"Add forwarding rule"}).first().click();
 const fm=t.page.getByRole("dialog",{name:"Add a forwarding rule"});
 await fm.waitFor({state:"visible"});
 await shot(t.page,"phase2c-04-forwarding-create");
 await fm.getByLabel("Source mailbox").selectOption(m2);
 await fm.getByLabel("Forward to").fill("someone@external.example");
 await fm.getByRole("button",{name:"Keep a local copy"}).click();
 await fm.getByRole("button",{name:"Create rule"}).click();
 await t.page.waitForTimeout(420);
 const fr=t.calls.find(x=>x.endpoint==="/api/forwarding/"&&x.method==="POST");
 check("Forwarding POST preserves source,destination,keep-copy policy",fr?.payload.source_mailbox_id===m2&&fr?.payload.destination_email==="someone@external.example"&&fr?.payload.keep_copy===false,{call:fr});
 check("Forwarding new entry appears in refreshed table",await t.page.getByText("someone@external.example").count()>0);
 await t.page.getByRole("combobox",{name:"Status for forwarding from amelia@example.test"}).selectOption("paused");
 await t.page.waitForTimeout(400);
 const fc=t.calls.find(x=>x.endpoint==="/api/forwarding/"+f1+"/status/"&&x.method==="PATCH");
 check("Forwarding Pause PATCH preserves existing status endpoint",fc?.payload.status==="paused",{call:fc});

 await open(t.page,"delegation");
 await shot(t.page,"phase2c-05-delegation");
 m=await measure(t.page);
 check("Delegation 4px table with existing personal mailbox grants",await t.page.getByText("oliver@example.test").count()>0&&m.card==="4px"&&m.head===41,m);
 await t.page.getByRole("button",{name:"Add delegation"}).click();
 const dm=t.page.getByRole("dialog",{name:"Add mailbox delegation"});
 await dm.waitFor({state:"visible"});
 await shot(t.page,"phase2c-06-delegation-create");
 await dm.getByLabel("Mailbox to delegate").selectOption(m2);
 await dm.getByLabel("Delegate mailbox").selectOption(m1);
 await dm.getByRole("checkbox",{name:/Send As/}).check();
 await dm.getByRole("button",{name:"Add delegation"}).click();
 await t.page.waitForTimeout(400);
 const dc=t.calls.find(x=>x.endpoint==="/api/delegations/"&&x.method==="POST");
 check("Delegation POST preserves personal identities and permission bits",dc?.payload.target_mailbox_id===m2&&dc?.payload.delegate_mailbox_id===m1&&dc?.payload.can_read===true&&dc?.payload.can_send_as===true,{call:dc});
 await t.page.getByRole("checkbox",{name:"Manage for oliver@example.test"}).first().click();
 await t.page.waitForTimeout(400);
 const dp=t.calls.find(x=>x.endpoint==="/api/delegations/"+g1+"/"&&x.method==="PATCH");
 check("Delegation permission PATCH enforces Manage=>Read",dp?.payload.can_manage===true&&dp?.payload.can_read===true,{call:dp});
 check("Desktop no page exceptions",t.errors.length===0,{errors:t.errors});
 await t.ctx.close();

 const limited=await make({width:390,height:844},"read_only");
 await open(limited.page,"aliases");
 await shot(limited.page,"phase2c-07-mobile-aliases-readonly");
 check("Read only cannot create alias",await limited.page.getByRole("button",{name:"Create alias"}).first().isDisabled());
 check("Alias mobile page no wide document",(await measure(limited.page)).scroll<=391,await measure(limited.page));
 await open(limited.page,"forwarding");
 await shot(limited.page,"phase2c-08-mobile-forwarding-readonly");
 check("Read only cannot create forwarding",await limited.page.getByRole("button",{name:"Add forwarding rule"}).first().isDisabled());
 check("Forwarding mobile page no wide document",(await measure(limited.page)).scroll<=391,await measure(limited.page));
 await open(limited.page,"delegation");
 await shot(limited.page,"phase2c-09-mobile-delegation-readonly");
 check("Read only delegation form cannot open",await limited.page.getByRole("button",{name:"Add delegation"}).isDisabled());
 check("Delegation API rejects read only without JS crash",limited.errors.length===0,{errors:limited.errors});
 await limited.ctx.close();
}catch(error){report.error=error.stack||String(error);process.exitCode=1;}
finally{fs.writeFileSync(path.join(out,"phase2c-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}

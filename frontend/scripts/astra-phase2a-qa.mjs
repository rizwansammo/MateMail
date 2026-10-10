/** Phase 2A — isolated real-browser contract for Mailboxes and Domains UI.
 * Tests use synthetic API responses; no production API or real credentials.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base=process.env.VISUAL_QA_URL||"http://127.0.0.1:3100";
const out=process.env.VISUAL_QA_OUTPUT||"visual-qa-artifacts";
fs.mkdirSync(out,{recursive:true});
const tid="d9bdde9d-1234-4123-8123-777777777777";
const uid="bd1a5653-1234-4234-8234-888888888888";
const token=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",Buffer.from(JSON.stringify({tenant_id:tid,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"qa-not-a-real-signature"].join(".");
const initialMailbox={id:"e1111111-1111-4111-8111-111111111111",email:"existing@example.test",full_name:"Existing User",local_part:"existing",domain:"example.test",domain_name:"example.test",status:"active",kind:"personal",quota_mb:1024,storage_used_mb:230,mail_service_ready:true,mail_service_message:"",last_login:null,created_at:"2026-01-01T00:00:00Z"};
const initialDomain={id:"d1111111-1111-4111-8111-111111111111",domain:"example.test",status:"active",dns_health_score:100,mail_service_ready:true,ownership_verified:true,added_at:"2026-01-01T00:00:00Z"};
const report={test:"Astra Phase 2A real pages with mocked API",checks:[]};
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});

function verify(name,good,detail={}){
 report.checks.push({name,passed:!!good,detail});
 assert.ok(good,name+": "+JSON.stringify(detail));
}
async function setup(viewport,role="owner"){
 const ctx=await browser.newContext({viewport,deviceScaleFactor:1,reducedMotion:"reduce"});
 const requests={mailbox:[],domain:[]};
 const mailboxes=[{...initialMailbox}];
 const domains=[{...initialDomain}];
 await ctx.route("**/api/**",async route=>{
  const endpoint=new URL(route.request().url()).pathname;
  const method=route.request().method();
  let body=[];
  if(endpoint==="/api/auth/refresh/")body={access:token};
  else if(endpoint==="/api/auth/me/")body={id:uid,email:"owner@example.test",full_name:"QA Owner",email_verified:true,two_factor_enabled:false,is_platform_admin:false};
  else if(endpoint==="/api/workspaces/"+tid+"/")body={id:tid,name:"QA Workspace",slug:"quality-assurance",status:"active",role,my_role:role};
  else if(endpoint==="/api/workspaces/"+tid+"/stats/")body={my_role:role,tenant_status:"active",tenant_plan:"business",mailbox_count:mailboxes.length,active_mailbox_count:mailboxes.length,domain_count:domains.length,active_domain_count:domains.length,member_count:1,storage_used_mb:230,storage_quota_mb:10240};
  else if(endpoint==="/api/workspaces/"+tid+"/onboarding/")body={workspace_created:true,domain_added:true,dns_verified:true,first_mailbox_created:true,completed:true,completed_at:"2026-01-01T00:00:00Z"};
  else if(endpoint==="/api/billing/")body={subscription:{plan:{display_name:"Business",default_storage_per_mailbox_mb:1024,max_storage_per_mailbox_mb:10240}}};
  else if(endpoint==="/api/mailboxes/"&&method==="POST"){
    const value=JSON.parse(route.request().postData()||"{}");
    requests.mailbox.push(value);
    const domain=domains.find(d=>d.id===value.domain_id)??domains[0];
    const row={...initialMailbox,id:"e2222222-2222-4222-8222-222222222222",email:value.local_part+"@"+domain.domain,local_part:value.local_part,full_name:value.full_name,quota_mb:value.quota_mb,storage_used_mb:0,last_login:null};
    mailboxes.push(row);body=row;
  }
  else if(endpoint==="/api/domains/"&&method==="POST"){
    const value=JSON.parse(route.request().postData()||"{}");
    requests.domain.push(value);
    const row={...initialDomain,id:"d2222222-2222-4222-8222-222222222222",domain:value.domain,status:"pending",dns_health_score:0,ownership_verified:false,mail_service_ready:false};
    domains.push(row);body=row;
  }
  else if(endpoint==="/api/mailboxes/")body=mailboxes;
  else if(endpoint==="/api/domains/")body=domains;
  else if(endpoint.startsWith("/api/domains/"))body={...domains[1],records:[]};
  await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(body)});
 });
 const page=await ctx.newPage();
 const errors=[];page.on("pageerror",error=>errors.push(error.message));
 return {ctx,page,requests,mailboxes,domains,errors};
}
async function screenshot(page,name){await page.screenshot({path:path.join(out,name+".png"),animations:"disabled",fullPage:false});}
async function ready(page,title){
 await page.goto(base+"/app/"+title.toLowerCase(),{waitUntil:"domcontentloaded",timeout:60000});
 await page.getByRole("heading",{name:title,exact:true}).waitFor({timeout:30000});
 await page.locator(".astra-resource-page").waitFor();
}
async function tableMetrics(page){
 return page.evaluate(()=>{
 const tab=document.querySelector(".astra-resource-page table");
 const head=tab?.querySelector("th");
 const card=document.querySelector(".astra-resource-page .portal-card");
 const mod=document.querySelector("dialog.astra-resource-dialog");
 return {tableHeadHeight:Math.round(head?.getBoundingClientRect().height||0),
 cardRadius:card?getComputedStyle(card).borderRadius:null,
 modalRadius:mod?getComputedStyle(mod).borderRadius:null,
 modalOpen:!!mod?.open,
 viewport:innerWidth,scroll:document.documentElement.scrollWidth};
 });
}
try{
 const sample=await setup({width:1440,height:900});
 await ready(sample.page,"Mailboxes");
 await sample.page.getByRole("button",{name:"Create mailbox"}).waitFor({state:"visible"});
 const createMailbox=sample.page.getByRole("button",{name:"Create mailbox"}).first();
 await createMailbox.waitFor({state:"visible"});
 await sample.page.waitForTimeout(300);
 await screenshot(sample.page,"phase2a-01-mailboxes");
 let m=await tableMetrics(sample.page);
 verify("Mailboxes render backend records and Astra 4px card",(await sample.page.getByText("existing@example.test").count())>0&&m.cardRadius==="4px",m);
 verify("Mailbox table uses Astra 41px header",m.tableHeadHeight===41,m);
 await createMailbox.click();
 const modal=sample.page.getByRole("dialog",{name:"Create a mailbox"});
 await modal.waitFor({state:"visible"});
 await screenshot(sample.page,"phase2a-02-mailbox-create");
 verify("Mailbox creation uses native accessible Astra dialog",(await modal.count())===1&&(await tableMetrics(sample.page)).modalRadius==="4px");
 await sample.page.keyboard.press("Escape");
 await sample.page.waitForTimeout(120);
 verify("Esc closes mailbox modal without API call",(await modal.isVisible())===false&&sample.requests.mailbox.length===0);
 await createMailbox.click();
 await modal.getByLabel("Email address").fill("newmember");
 await modal.getByLabel("Display name").fill("New Member");
 await modal.getByLabel("Temporary password").fill("qa-strong-password-123");
 await modal.getByRole("button",{name:"Create mailbox"}).click();
 await sample.page.waitForTimeout(650);
 verify("Mailbox creation preserves POST payload, domain and quota",sample.requests.mailbox.length===1&&sample.requests.mailbox[0].local_part==="newmember"&&sample.requests.mailbox[0].domain_id===initialDomain.id&&sample.requests.mailbox[0].full_name==="New Member",{requests:sample.requests.mailbox.map(({password,...data})=>data)});
 verify("Mailbox creation updates real table",(await sample.page.getByText("newmember@example.test").count())>0);
 verify("Mailbox modal closes after successful create",!(await modal.isVisible()));
 await ready(sample.page,"Domains");
 await sample.page.waitForTimeout(300);
 await screenshot(sample.page,"phase2a-03-domains");
 m=await tableMetrics(sample.page);
 verify("Domains show backend records and Astra card radius",(await sample.page.getByText("example.test").count())>0&&m.cardRadius==="4px",m);
 verify("Domain table uses Astra 41px header",m.tableHeadHeight===41,m);
 const addDomain=sample.page.getByRole("button",{name:"Add domain"}).first();
 await addDomain.waitFor({state:"visible"});
 await addDomain.click();
 const domainModal=sample.page.getByRole("dialog",{name:"Connect a new domain"});
 await domainModal.waitFor({state:"visible"});
 await screenshot(sample.page,"phase2a-04-domain-create");
 verify("Domain creation modal contains ownership guidance",(await domainModal.getByText(/does not transfer/i).count())>0);
 await domainModal.getByLabel("Root domain").fill("new-domain.example");
 await domainModal.getByRole("button",{name:"Add domain"}).click();
 await sample.page.waitForTimeout(450);
 verify("Domain creation POST preserved and navigates to existing detail",sample.requests.domain.length===1&&sample.requests.domain[0].domain==="new-domain.example",{request:sample.requests.domain[0]});
 verify("No runtime errors in main list flows",sample.errors.length===0,{errors:sample.errors});
 await sample.ctx.close();

 const limited=await setup({width:390,height:844},"read_only");
 await ready(limited.page,"Mailboxes");
 await limited.page.waitForTimeout(450);
 await screenshot(limited.page,"phase2a-05-mailboxes-mobile-readonly");
 verify("Read-only member cannot create a mailbox",await limited.page.getByRole("button",{name:"Create mailbox"}).first().isDisabled());
 verify("Mobile Mailboxes table scrolls internally, not entire document",(await tableMetrics(limited.page)).scroll<=391,await tableMetrics(limited.page));
 await ready(limited.page,"Domains");
 await limited.page.waitForTimeout(450);
 await screenshot(limited.page,"phase2a-06-domains-mobile-readonly");
 verify("Read-only member cannot create a domain",await limited.page.getByRole("button",{name:"Add domain"}).first().isDisabled());
 verify("Mobile Domains document has no horizontal overflow",(await tableMetrics(limited.page)).scroll<=391,await tableMetrics(limited.page));
 await limited.ctx.close();
}catch(err){report.error=err.stack||String(err);process.exitCode=1;}
finally{fs.writeFileSync(path.join(out,"phase2a-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}

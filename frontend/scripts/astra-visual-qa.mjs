/** Phase 1 browser-based visual/interaction QA. Ephemeral fake API only. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {chromium} from "playwright";
const base=process.env.VISUAL_QA_URL||"http://127.0.0.1:3100";
const out=process.env.VISUAL_QA_OUTPUT||"visual-qa-artifacts";
fs.mkdirSync(out,{recursive:true});
const tid="d9bdde9d-1234-4123-8123-777777777777";
const user={id:"bd1a5653-1234-4234-8234-888888888888",email:"qa-owner@example.test",full_name:"QA Account",email_verified:true,two_factor_enabled:false,is_platform_admin:false};
const tenant={id:tid,name:"Quality Assurance Workspace",slug:"quality-assurance",status:"active",role:"owner"};
const token=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",Buffer.from(JSON.stringify({tenant_id:tid,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"qa-not-a-real-signature"].join(".");
const report={description:"Phase 1 Astra Hub browser QA with synthetic tenant only",results:[]};
let completed=false;
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
function check(name,condition,metrics={}){
  report.results.push({name,passed:!!condition,metrics});
  assert.ok(condition,name+": "+JSON.stringify(metrics));
}
async function setup(viewport){
  const context=await browser.newContext({viewport,deviceScaleFactor:1,reducedMotion:"reduce"});
  await context.route("**/api/**",async route=>{
    const endpoint=new URL(route.request().url()).pathname;
    let response=[];
    if(endpoint==="/api/auth/refresh/")response={access:token};
    else if(endpoint==="/api/auth/me/")response=user;
    else if(endpoint==="/api/workspaces/"+tid+"/")response={...tenant,my_role:"owner",plan:"business",domain_count:1,mailbox_count:1,member_count:1,approved_at:"2026-01-01T00:00:00Z",review_reason:"",outbound_disabled:false,created_at:"2026-01-01T00:00:00Z"};
    else if(endpoint==="/api/workspaces/"+tid+"/onboarding/")response={workspace_created:true,domain_added:completed,dns_verified:completed,first_mailbox_created:completed,completed,completed_at:completed?"2026-01-01T00:00:00Z":null};
    else if(endpoint==="/api/workspaces/"+tid+"/stats/")response={my_role:"owner",tenant_status:"active",mailbox_count:1,domain_count:1,member_count:1};
    else if(endpoint==="/api/domains/")response=[{id:"cccccccc-cccc-4ccc-8ccc-cccccccccccc",domain:"example.test",status:"active",ownership_verified:true,dns_health_score:100}];
    else if(endpoint==="/api/mailboxes/")response=[{id:"eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",email:"qa@example.test",full_name:"QA Inbox",kind:"personal",status:"active",quota_mb:10240,storage_used_mb:0}];
    else if(endpoint==="/api/billing/")response={subscription:null};
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(response)});
  });
  const page=await context.newPage();
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto(base+"/app",{waitUntil:"domcontentloaded",timeout:60000});
  await page.locator(".astra-sidebar").waitFor({state:"visible",timeout:30000});
  await page.waitForTimeout(650);
  return {context,page,errors};
}
async function metrics(page){
  return page.evaluate(()=>{
    const element=sel=>document.querySelector(sel);
    const rect=sel=>{const el=element(sel);if(!el)return null;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height),background:s.backgroundColor,radius:s.borderRadius,transform:s.textTransform,boxShadow:s.boxShadow};};
    return {sidebar:rect(".astra-sidebar"),topbar:rect(".astra-topbar"),search:rect(".astra-search-dialog"),searchInput:rect(".astra-search-inputrow"),groupLabel:rect("[data-slot=sidebar-group-label]"),input:rect(".astra-search-inputrow input"),wordmarkFont:getComputedStyle(element(".wordmark")||document.body).fontFamily,gettingStarted:Array.from(document.querySelectorAll(".astra-sidebar a")).some(a=>a.textContent?.includes("Getting started")),duplicateOrg:!!element(".org-sidebar"),scroll:document.documentElement.scrollWidth,viewport:innerWidth};
  });
}
async function snap(page,name){await page.screenshot({path:path.join(out,name+".png"),fullPage:false,animations:"disabled"});}
try{
  const desktop=await setup({width:1440,height:900});
  let m=await metrics(desktop.page);
  await snap(desktop.page,"01-new-org-desktop");
  check("Astra desktop sidebar 244px and header 60px",m.sidebar?.width===244&&m.topbar?.height===60,m);
  check("Organization appears in topbar only",!m.duplicateOrg&&(await desktop.page.locator(".astra-org-name").innerText())===tenant.name);
  check("New organization shows Getting Started",m.gettingStarted);
  check("Sidebar group headings match Astra uppercase style",m.groupLabel?.transform==="uppercase",{label:m.groupLabel});
  check("HemiHead brand font loaded",m.wordmarkFont.includes("--font-matemail-hub")||m.wordmarkFont.toLowerCase().includes("hemihead"),{font:m.wordmarkFont});
  await desktop.page.getByRole("button",{name:"Collapse sidebar"}).click();
  await desktop.page.waitForTimeout(260);
  m=await metrics(desktop.page);
  await snap(desktop.page,"02-sidebar-collapsed");
  check("Astra compact rail is 60px",m.sidebar?.width===60,{sidebar:m.sidebar});
  await desktop.page.getByRole("button",{name:"Expand sidebar"}).click();
  await desktop.page.getByRole("button",{name:/Search workspace/}).click();
  await desktop.page.getByRole("dialog",{name:"Search Hub"}).waitFor();
  await snap(desktop.page,"03a-global-search-unfiltered");
  await desktop.page.getByRole("textbox",{name:"Search Hub pages and resources"}).fill("mailbox");
  m=await metrics(desktop.page);
  await snap(desktop.page,"03-global-search");
  check("Search dialog original compact geometry",m.search?.width===512&&m.searchInput?.height===40&&m.search.radius==="4px",{dialog:m.search,input:m.searchInput});
  check("Search field has no extra blue focus ring",m.input?.boxShadow==="none",{focus:m.input});
  await desktop.page.keyboard.press("Escape");
  check("Escape closes Global Search",await desktop.page.getByRole("dialog",{name:"Search Hub"}).count()===0);
  await desktop.page.keyboard.press("Control+k");
  check("Ctrl+K opens Global Search",await desktop.page.getByRole("dialog",{name:"Search Hub"}).count()===1);
  await desktop.page.keyboard.press("Escape");
  await desktop.page.getByRole("button",{name:"Switch to dark theme"}).click();
  await desktop.page.waitForTimeout(200);
  await snap(desktop.page,"04-dark");
  check("Dark and light tokens differ",(await metrics(desktop.page)).sidebar?.background!==m.sidebar?.background);
  check("Desktop has no JS exception",desktop.errors.length===0,{errors:desktop.errors});
  await desktop.context.close();

  completed=true;
  const done=await setup({width:1440,height:900});
  await snap(done.page,"05-completed-org");
  check("Persisted-complete organization hides Getting Started",!(await metrics(done.page)).gettingStarted);
  await done.context.close();

  completed=false;
  const laptop=await setup({width:1024,height:768});
  await snap(laptop.page,"06-laptop");
  check("Laptop has no page-level overflow",(await metrics(laptop.page)).scroll<=1025);
  await laptop.context.close();

  const phone=await setup({width:390,height:844});
  await snap(phone.page,"07-phone-closed");
  const sidebar=phone.page.locator(".astra-sidebar");
  check("Phone drawer initially closed",(await sidebar.getAttribute("data-mobile-open"))==="false");
  await phone.page.getByRole("button",{name:"Collapse sidebar"}).click();
  await snap(phone.page,"08-phone-open");
  check("Phone drawer opens",(await sidebar.getAttribute("data-mobile-open"))==="true");
  await phone.page.keyboard.press("Escape");
  check("Phone drawer closes on Escape",(await sidebar.getAttribute("data-mobile-open"))==="false");
  check("Phone no horizontal document overflow",(await metrics(phone.page)).scroll<=391);
  check("Phone has no JS exception",phone.errors.length===0,{errors:phone.errors});
  await phone.context.close();
}catch(e){
  report.error=e.stack||String(e);
  process.exitCode=1;
}finally{
  fs.writeFileSync(path.join(out,"report.json"),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
  await browser.close();
}

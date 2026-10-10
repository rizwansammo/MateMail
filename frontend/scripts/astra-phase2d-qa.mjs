/** Phase 2D — Users & Access synthetic browser/API contract. Never uses live tenants. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
const base=process.env.VISUAL_QA_URL||"http://127.0.0.1:3100";
const out=process.env.VISUAL_QA_OUTPUT||"visual-qa-artifacts"; fs.mkdirSync(out,{recursive:true});
const tid="d9bdde9d-1234-4123-8123-777777777777";
const ownerId="bd1a5653-1234-4234-8234-888888888888";
const memberId="be222222-2222-4222-8222-222222222222";
const removableId="be333333-3333-4333-8333-333333333333";
const pendingId="be444444-4444-4444-8444-444444444444";
const token=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",Buffer.from(JSON.stringify({tenant_id:tid,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"synthetic"].join(".");
const now="2026-01-01T00:00:00Z",expiry="2027-01-01T00:00:00Z";
const report={phase:"Astra Phase 2D — real member/invitation API contracts",checks:[]};
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
function check(name,good,detail={}){report.checks.push({name,passed:!!good,detail});assert.ok(good,name+": "+JSON.stringify(detail));}
async function make(viewport,role="owner"){
 const ctx=await browser.newContext({viewport,deviceScaleFactor:1,reducedMotion:"reduce"});
 const members=[
  {id:ownerId,email:"owner@example.test",full_name:"QA Owner",role:"owner",status:"active",created_at:now},
  {id:memberId,email:"member@example.test",full_name:"QA Member",role:"support",status:"active",created_at:now},
  {id:removableId,email:"viewer@example.test",full_name:"QA Viewer",role:"read_only",status:"active",created_at:now}
 ];
 const invites=[{id:pendingId,email:"pending@example.test",role:"read_only",invited_by_email:"owner@example.test",created_at:now,expires_at:expiry,accepted_at:null,is_revoked:false,is_pending:true}];
 const calls=[];
 await ctx.route("**/api/**",async route=>{
  const endpoint=new URL(route.request().url()).pathname;
  const method=route.request().method(),payload=route.request().postData()?JSON.parse(route.request().postData()):null;
  if(!["GET"].includes(method)&&!endpoint.startsWith("/api/auth/")) calls.push({endpoint,method,payload});
  let response=[],status=200;
  if(endpoint==="/api/auth/refresh/")response={access:token};
  else if(endpoint==="/api/auth/me/")response={id:ownerId,email:"owner@example.test",full_name:"QA Owner",email_verified:true,two_factor_enabled:false,is_platform_admin:false};
  else if(endpoint==="/api/workspaces/"+tid+"/")response={id:tid,name:"QA Workspace",slug:"qa-workspace",status:"active",role,my_role:role};
  else if(endpoint==="/api/workspaces/"+tid+"/stats/")response={my_role:role,tenant_status:"active",member_count:members.length};
  else if(endpoint==="/api/workspaces/"+tid+"/onboarding/")response={workspace_created:true,domain_added:true,dns_verified:true,first_mailbox_created:true,completed:true,completed_at:now};
  else if(endpoint==="/api/workspaces/"+tid+"/members/"&&method==="GET")response=members;
  else if(endpoint==="/api/workspaces/"+tid+"/members/"+memberId+"/"&&method==="PATCH"){
    const row=members.find(m=>m.id===memberId);if(role==="owner"&&row){row.role=payload.role;response={...row}}else{status=403;response={detail:"Only owner can change roles"}}
  }
  else if(endpoint==="/api/workspaces/"+tid+"/members/"+removableId+"/"&&method==="DELETE"){
    if(role==="owner"){const i=members.findIndex(m=>m.id===removableId);if(i>=0)members.splice(i,1);status=204;response={};}
    else{status=403;response={detail:"Permission denied"}}
  }
  else if(endpoint==="/api/teams/invites/"&&method==="GET"){
    if(role==="owner"||role==="admin")response=invites;
    else{status=403;response={detail:"Not allowed"}}
  }
  else if(endpoint==="/api/teams/invites/"&&method==="POST"){
    if(role!=="owner"&&role!=="admin"){status=403;response={detail:"Not allowed"}}
    else if(!payload.email.endsWith("@example.test")){status=400;response={detail:"Only verified workspace domains can be invited."}}
    else if(invites.some(x=>x.email===payload.email)){status=400;response={detail:"A pending invite already exists for that email."}}
    else{
      const row={id:"be555555-5555-4555-8555-555555555555",email:payload.email,role:payload.role,invited_by_email:"owner@example.test",created_at:now,expires_at:expiry,accepted_at:null,is_revoked:false,is_pending:true,email_delivered:true};
      invites.push(row);status=201;response=row;
    }
  }
  else if(endpoint==="/api/teams/invites/"+pendingId+"/"&&method==="DELETE"){
    const i=invites.findIndex(x=>x.id===pendingId);
    if(i>=0)invites.splice(i,1);status=204;response={};
  }
  await route.fulfill({status,contentType:"application/json",body:JSON.stringify(response)});
 });
 const page=await ctx.newPage(),errors=[]; page.on("pageerror",e=>errors.push(e.message));
 return {ctx,page,members,invites,calls,errors};
}
async function go(page){
 await page.goto(base+"/app/team",{waitUntil:"domcontentloaded",timeout:60000});
 await page.locator(".astra-users-page").waitFor({state:"visible",timeout:30000});
 await page.getByRole("heading",{name:"Users & access"}).waitFor({timeout:30000});
 await page.waitForTimeout(550);
}
async function screenshot(page,name){await page.screenshot({path:path.join(out,name+".png"),animations:"disabled",fullPage:false});}
async function geometry(page){
 return page.evaluate(()=>{
  const card=document.querySelector(".astra-users-page .portal-card");
  const th=document.querySelector(".astra-users-page table th");
  const dlg=document.querySelector("dialog.astra-resource-dialog");
  return{card:card?getComputedStyle(card).borderRadius:null,header:th?Math.round(th.getBoundingClientRect().height):0,dialog:dlg?getComputedStyle(dlg).borderRadius:null,viewport:innerWidth,scroll:document.documentElement.scrollWidth};
 });
}
try{
 const owner=await make({width:1440,height:900});
 await go(owner.page);
 await screenshot(owner.page,"phase2d-01-users-owner");
 let m=await geometry(owner.page);
 check("Owner member list uses real membership API and Astra card/table",(await owner.page.getByText("member@example.test").count())>0&&m.card==="4px"&&m.header===41,m);
 check("Owner sees invitations tab and live metrics",await owner.page.getByRole("button",{name:/Invitations/}).count()===1&&await owner.page.getByText("Active members").count()===1);
 const inviteButton=owner.page.getByRole("button",{name:"Invite member"}).first();
 await inviteButton.click();
 const dlg=owner.page.getByRole("dialog",{name:"Invite member"});
 await dlg.waitFor({state:"visible"});
 await screenshot(owner.page,"phase2d-02-invite-dialog");
 check("Invite is native Astra 4px dialog and explains no mailbox",m.dialog==="4px"|| (await geometry(owner.page)).dialog==="4px");
 check("Invite clearly does not provision mailbox",await dlg.getByText(/does not create or assign a mailbox/i).count()===1);
 await owner.page.keyboard.press("Escape");
 check("Escape dismisses invitation without POST",!(await dlg.isVisible())&&!owner.calls.some(x=>x.endpoint==="/api/teams/invites/"));
 await inviteButton.click();
 await dlg.getByLabel("Email address").fill("outsider@external.test");
 await dlg.getByLabel("Workspace role").selectOption("support");
 await dlg.getByRole("button",{name:"Send invitation"}).click();
 await dlg.getByText("Only verified workspace domains can be invited.").waitFor({timeout:8000});
 check("Server domain restrictions visible to user",owner.calls.some(x=>x.endpoint==="/api/teams/invites/"&&x.payload.email==="outsider@external.test"));
 await dlg.getByLabel("Email address").fill("colleague@example.test");
 await dlg.getByRole("button",{name:"Send invitation"}).click();
 await owner.page.getByText("colleague@example.test").waitFor({timeout:8000});
 const created=owner.calls.find(x=>x.endpoint==="/api/teams/invites/"&&x.method==="POST"&&x.payload.email==="colleague@example.test");
 check("Real invitation POST sends only email and chosen Hub role",created?.payload.email==="colleague@example.test"&&created?.payload.role==="support"&&Object.keys(created?.payload||{}).length===2,{payload:created?.payload});
 check("Post-invite UI switches to invitations and refreshes records",await owner.page.getByText("colleague@example.test").count()>0);
 await screenshot(owner.page,"phase2d-03-invitations");
 await owner.page.getByRole("button",{name:"Revoke invitation for pending@example.test"}).click();
 await owner.page.getByRole("button",{name:"Revoke",exact:true}).click();
 await owner.page.waitForTimeout(450);
 check("Invitation revocation hits existing DELETE endpoint",owner.calls.some(x=>x.endpoint==="/api/teams/invites/"+pendingId+"/"&&x.method==="DELETE"));
 await owner.page.getByRole("button",{name:/Members/}).click();
 await owner.page.getByRole("combobox",{name:"Role for member@example.test"}).selectOption("read_only");
 await owner.page.waitForTimeout(400);
 const roleChanged=owner.calls.find(x=>x.endpoint==="/api/workspaces/"+tid+"/members/"+memberId+"/"&&x.method==="PATCH");
 check("Only-owner member role change preserves PATCH shape",roleChanged?.payload.role==="read_only",{payload:roleChanged?.payload});
 await owner.page.getByRole("button",{name:"Remove viewer@example.test"}).click();
 await owner.page.getByRole("button",{name:"Remove",exact:true}).click();
 await owner.page.waitForTimeout(400);
 check("Owner member removal sends tenant-scoped DELETE",owner.calls.some(x=>x.endpoint==="/api/workspaces/"+tid+"/members/"+removableId+"/"&&x.method==="DELETE"));
 check("Owner member removed from list",await owner.page.getByText("viewer@example.test").count()===0);
 check("Owner has no JavaScript errors",owner.errors.length===0,{errors:owner.errors});
 await owner.ctx.close();

 const admin=await make({width:1440,height:900},"admin");
 await go(admin.page); await screenshot(admin.page,"phase2d-04-users-admin");
 check("Admin may invite members",await admin.page.getByRole("button",{name:"Invite member"}).isEnabled());
 check("Admin cannot change member role",await admin.page.getByRole("combobox",{name:"Role for member@example.test"}).count()===0);
 check("Admin cannot remove other members",await admin.page.getByRole("button",{name:"Remove viewer@example.test"}).count()===0);
 check("Admin sees invitations",await admin.page.getByRole("button",{name:/Invitations/}).count()===1);
 await admin.ctx.close();

 const viewer=await make({width:390,height:844},"read_only");
 await go(viewer.page); await screenshot(viewer.page,"phase2d-05-users-mobile-readonly");
 check("Read-only cannot invite",await viewer.page.getByRole("button",{name:"Invite member"}).count()===0);
 check("Read-only sees no restricted invitation tab",await viewer.page.getByRole("button",{name:/Invitations/}).count()===0);
 check("Read-only cannot change member role",await viewer.page.getByRole("combobox",{name:"Role for member@example.test"}).count()===0);
 m=await geometry(viewer.page);
 check("Mobile Users & Access avoids page-wide overflow",m.scroll<=391,m);
 check("Read-only receives no page JS errors",viewer.errors.length===0,{errors:viewer.errors});
 await viewer.ctx.close();
}catch(e){report.error=e.stack||String(e);process.exitCode=1;}
finally{fs.writeFileSync(path.join(out,"phase2d-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}

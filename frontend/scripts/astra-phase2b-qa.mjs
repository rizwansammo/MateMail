/** Phase 2B: synthetic-browser contract for TeamBox/Forward Group workflows.
 * No production API or mailbox/SMTP traffic. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {chromium} from "playwright";

const base=process.env.VISUAL_QA_URL||"http://127.0.0.1:3100";
const output=process.env.VISUAL_QA_OUTPUT||"visual-qa-artifacts";
fs.mkdirSync(output,{recursive:true});
const tid="d9bdde9d-1234-4123-8123-777777777777";
const boxId="e5555555-5555-4555-8555-555555555555";
const groupId="f5555555-5555-4555-8555-555555555555";
const memberId="e1111111-1111-4111-8111-111111111111";
const domainId="d1111111-1111-4111-8111-111111111111";
const user={id:"bd1a5653-1234-4234-8234-888888888888",email:"owner@example.test",full_name:"QA Owner",email_verified:true,two_factor_enabled:false,is_platform_admin:false};
const token=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",Buffer.from(JSON.stringify({tenant_id:tid,exp:Math.floor(Date.now()/1000)+3600})).toString("base64url"),"qa-not-real"].join(".");
const report={name:"MateMail Astra Phase 2B / synthetic API",assertions:[]};
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
function check(label,good,detail={}){
  report.assertions.push({label,passed:!!good,detail});
  assert.ok(good,label+": "+JSON.stringify(detail));
}
async function setup(viewport,role="owner"){
 const ctx=await browser.newContext({viewport,reducedMotion:"reduce",deviceScaleFactor:1});
 const records={
  teamBoxes:[],groups:[],
  mailboxes:[
   {id:memberId,email:"amelia@example.test",full_name:"Amelia",kind:"personal",status:"active"},
   {id:"e2222222-2222-4222-8222-222222222222",email:"oliver@example.test",full_name:"Oliver",kind:"personal",status:"active"}
  ],
  domains:[{id:domainId,domain:"example.test",ownership_verified:true}],
  calls:[]
 };
 const box={id:boxId,email:"support@example.test",full_name:"Customer Support",local_part:"support",domain:domainId,domain_name:"example.test",kind:"team_box",status:"active",quota_mb:1024,storage_used_mb:5,member_count:0,mail_service_ready:true,mail_service_message:"",created_at:"2026-01-01T00:00:00Z",members:[]};
 const group={id:groupId,address:"engineering@example.test",display_name:"Engineering",sender_policy:"anyone",status:"active",member_count:1,sender_count:0,mail_service_ready:true,mail_service_message:"",members:[{id:"g1111111-1111-4111-8111-111111111111",mailbox_id:memberId,email:"amelia@example.test",full_name:"Amelia",kind:"personal",role:"member"}],allowed_senders:[]};
 records.teamBoxes.push(box);records.groups.push(group);
 await ctx.route("**/api/**",async route=>{
  const pathname=new URL(route.request().url()).pathname,method=route.request().method();
  const value=route.request().postData()?JSON.parse(route.request().postData()):null;
  if(method!=="GET")records.calls.push({pathname,method,value});
  let response=[];
  if(pathname==="/api/auth/refresh/")response={access:token};
  else if(pathname==="/api/auth/me/")response=user;
  else if(pathname==="/api/workspaces/"+tid+"/")response={id:tid,name:"QA Workspace",status:"active",role,my_role:role,slug:"quality-assurance"};
  else if(pathname==="/api/workspaces/"+tid+"/stats/")response={my_role:role,tenant_status:"active"};
  else if(pathname==="/api/workspaces/"+tid+"/onboarding/")response={workspace_created:true,domain_added:true,dns_verified:true,first_mailbox_created:true,completed:true,completed_at:"2026-01-01T00:00:00Z"};
  else if(pathname==="/api/domains/")response=records.domains;
  else if(pathname==="/api/billing/")response={subscription:{plan:{display_name:"Business",default_storage_per_mailbox_mb:1024,max_storage_per_mailbox_mb:10240}}};
  else if(pathname==="/api/mailboxes/")response=records.mailboxes;
  else if(pathname==="/api/team-boxes/"&&method==="GET")response=records.teamBoxes;
  else if(pathname==="/api/team-boxes/"&&method==="POST"){
   const created={...box,id:"e6666666-6666-4666-8666-666666666666",email:value.local_part+"@example.test",full_name:value.display_name,local_part:value.local_part,quota_mb:value.quota_mb,members:[],member_count:0};
   records.teamBoxes.push(created);response=created;
  }
  else if(pathname==="/api/team-boxes/"+boxId+"/"&&method==="GET")response=box;
  else if(pathname==="/api/team-boxes/"+boxId+"/members/"&&method==="POST"){
   box.members.push({id:"m1111111-1111-4111-8111-111111111111",mailbox_id:value.mailbox_id,email:"amelia@example.test",full_name:"Amelia",active:true,...value});
   box.member_count=box.members.length;
   response=box.members[0];
  }
  else if(pathname.startsWith("/api/team-boxes/")&&pathname.endsWith("/members/m1111111-1111-4111-8111-111111111111/")&&method==="PATCH"){
   Object.assign(box.members[0],value);response={...box.members[0]};
  }
  else if(pathname==="/api/forward-groups/"&&method==="GET")response=records.groups;
  else if(pathname==="/api/forward-groups/"&&method==="POST"){
   const created={...group,id:"f6666666-6666-4666-8666-666666666666",address:value.local_part+"@example.test",display_name:value.display_name,sender_policy:value.sender_policy,members:value.member_mailbox_ids.map((id,i)=>({id:"g2222222-2222-4222-8222-222222222222",mailbox_id:id,email:records.mailboxes.find(m=>m.id===id)?.email||"",kind:"personal",role:"member"})),allowed_senders:[]};
   records.groups.push(created);response=created;
  }
  else if(pathname==="/api/forward-groups/"+groupId+"/"&&method==="GET")response=group;
  else if(pathname==="/api/forward-groups/"+groupId+"/policy/"&&method==="PATCH"){
   group.sender_policy=value.sender_policy;response={...group};
  }
  else if(pathname==="/api/forward-groups/"+groupId+"/members/"&&method==="POST"){
   group.members.push({id:"g2222222-2222-4222-8222-222222222222",mailbox_id:value.mailbox_id,email:"oliver@example.test",full_name:"Oliver",kind:"personal",role:value.role});
   group.member_count=group.members.length;response=group.members[group.members.length-1];
  }
  else if(pathname==="/api/forward-groups/"+groupId+"/senders/"&&method==="POST"){
   group.allowed_senders.push({id:"s1111111-1111-4111-8111-111111111111",mailbox_id:value.mailbox_id,email:"amelia@example.test",full_name:"Amelia"});
   group.sender_count=group.allowed_senders.length;response=group.allowed_senders[0];
  }
  else if(pathname==="/api/team-boxes/e6666666-6666-4666-8666-666666666666/")response=records.teamBoxes[records.teamBoxes.length-1];
  else if(pathname==="/api/forward-groups/f6666666-6666-4666-8666-666666666666/")response=records.groups[records.groups.length-1];
  await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(response)});
 });
 const page=await ctx.newPage(),errors=[];
 page.on("pageerror",e=>errors.push(e.message));
 return {ctx,page,records,errors};
}
async function screen(page,name){await page.screenshot({path:path.join(output,name+".png"),animations:"disabled",fullPage:false});}
async function go(page,name,url){
 await page.goto(base+url,{waitUntil:"domcontentloaded",timeout:60000});
 await page.locator(".astra-collaboration-page").waitFor({state:"visible",timeout:30000});
 await page.waitForTimeout(450);
}
async function checkGeometry(page){
 return page.evaluate(()=>{
   const c=document.querySelector(".astra-collaboration-page .portal-card");
   const tr=document.querySelector(".astra-collaboration-page table th");
   const d=document.querySelector("dialog.astra-resource-dialog");
   return {card:c?getComputedStyle(c).borderRadius:null,header:tr?Math.round(tr.getBoundingClientRect().height):null,dialog:d?getComputedStyle(d).borderRadius:null,overflow:document.documentElement.scrollWidth,width:innerWidth};
 });
}
try{
 const env=await setup({width:1440,height:900});
 await go(env.page,"TeamBoxes","/app/team-boxes");
 await screen(env.page,"phase2b-01-teambox-list");
 let m=await checkGeometry(env.page);
 check("TeamBox list real API and Astra card",await env.page.getByText("support@example.test").count()>0&&m.card==="4px",m);
 check("TeamBox list Astra table 41px header",m.header===41,m);
 await env.page.getByRole("button",{name:"Create TeamBox"}).first().click();
 const modal=env.page.getByRole("dialog",{name:"Create a TeamBox"});
 await modal.waitFor({state:"visible"});
 await screen(env.page,"phase2b-02-teambox-create");
 check("TeamBox native modal 4px", (await checkGeometry(env.page)).dialog==="4px");
 await env.page.keyboard.press("Escape");
 check("Escape dismisses TeamBox modal",!(await modal.isVisible()));
 await env.page.getByRole("button",{name:"Create TeamBox"}).first().click();
 await modal.getByLabel("TeamBox address").fill("accounts");
 await modal.getByLabel("Display name").fill("Accounts");
 await modal.getByRole("button",{name:"Create TeamBox"}).click();
 await env.page.waitForTimeout(500);
 const tb=env.records.calls.find(c=>c.pathname==="/api/team-boxes/"&&c.method==="POST");
 check("TeamBox POST keeps quota/verified domain/no shared password",tb?.value.local_part==="accounts"&&tb?.value.domain_id===domainId&&tb?.value.display_name==="Accounts"&&!Object.hasOwn(tb?.value||{},"password"),{payload:tb?.value});
 await go(env.page,"TeamBox detail","/app/team-boxes/"+boxId);
 await screen(env.page,"phase2b-03-teambox-details");
 check("TeamBox detail retains member permission matrix",await env.page.getByText("Members & permissions").count()>0);
 await env.page.getByRole("button",{name:"Add member"}).click();
 await env.page.waitForTimeout(550);
 const tm=env.records.calls.find(c=>c.pathname.endsWith("/members/")&&c.pathname.includes("team-boxes")&&c.method==="POST");
 check("TeamBox permissions POST correctly uses personal mailbox",tm?.value.mailbox_id===memberId&&tm.value.can_read===true&&tm.value.can_manage===true&&tm.value.can_send_as===true,{payload:tm?.value});
 await env.page.getByRole("checkbox",{name:"Send on behalf for amelia@example.test"}).click();
 await env.page.waitForFunction(() => true);
 await env.page.waitForTimeout(500);
 const pm=env.records.calls.find(c=>c.pathname.endsWith("/members/m1111111-1111-4111-8111-111111111111/")&&c.method==="PATCH");
 check("TeamBox permissions PATCH preserved",pm?.value.can_send_on_behalf===true,{payload:pm?.value});

 await go(env.page,"Forward Groups","/app/forward-groups");
 await screen(env.page,"phase2b-04-forward-groups-list");
 m=await checkGeometry(env.page);
 check("Forward Group list real API and Astra 4px card",await env.page.getByText("engineering@example.test").count()>0&&m.card==="4px",m);
 check("Forward Group list Astra table 41px header",m.header===41,m);
 await env.page.getByRole("button",{name:"Create Forward Group"}).first().click();
 const gm=env.page.getByRole("dialog",{name:"Create a Forward Group"});
 await gm.waitFor({state:"visible"});
 await screen(env.page,"phase2b-05-group-create");
 check("Group dialog uses 4px radius",(await checkGeometry(env.page)).dialog==="4px");
 await gm.getByLabel("Group address").fill("everyone");
 await gm.getByLabel("Display name").fill("Everyone");
 await gm.getByText("Amelia",{exact:true}).first().click();
 await gm.getByLabel("Who can send to this group?").selectOption("selected");
 await gm.getByText("Oliver",{exact:true}).last().click();
 await gm.getByRole("button",{name:"Create Forward Group"}).click();
 await env.page.waitForTimeout(550);
 const fg=env.records.calls.find(c=>c.pathname==="/api/forward-groups/"&&c.method==="POST");
 check("Forward Group POST includes recipients and selected sender policy",fg?.value.local_part==="everyone"&&fg?.value.member_mailbox_ids.includes(memberId)&&fg?.value.sender_policy==="selected"&&fg?.value.allowed_sender_mailbox_ids.includes("e2222222-2222-4222-8222-222222222222"),{payload:fg?.value});
 await go(env.page,"Group detail","/app/forward-groups/"+groupId);
 await screen(env.page,"phase2b-06-group-details");
 check("Group detail retains member and sender policy panels",await env.page.getByText("Sender policy",{exact:true}).count()>0&&await env.page.getByText("Members",{exact:true}).count()>0);
 const policy=env.page.getByLabel("Who can send to this group?");
 await policy.selectOption("members");
 await env.page.waitForTimeout(400);
 const pp=env.records.calls.find(c=>c.pathname.endsWith("/policy/")&&c.method==="PATCH");
 check("Forward Group PATCH sender-policy persists",pp?.value.sender_policy==="members",{payload:pp?.value});
 check("Desktop no JavaScript exception",env.errors.length===0,{errors:env.errors});
 await env.ctx.close();

 const limited=await setup({width:390,height:844},"read_only");
 await go(limited.page,"TeamBoxes mobile","/app/team-boxes");
 await screen(limited.page,"phase2b-07-teambox-mobile");
 check("Read-only TeamBox create disabled",await limited.page.getByRole("button",{name:"Create TeamBox"}).first().isDisabled());
 check("TeamBox mobile no page-wide overflow",(await checkGeometry(limited.page)).overflow<=391,await checkGeometry(limited.page));
 await go(limited.page,"Groups mobile","/app/forward-groups");
 await screen(limited.page,"phase2b-08-group-mobile");
 check("Read-only Group create disabled",await limited.page.getByRole("button",{name:"Create Forward Group"}).first().isDisabled());
 check("Group mobile no page-wide overflow",(await checkGeometry(limited.page)).overflow<=391,await checkGeometry(limited.page));
 check("Mobile no JS crash",limited.errors.length===0,{errors:limited.errors});
 await limited.ctx.close();
}catch(e){report.error=e.stack||String(e);process.exitCode=1;}
finally{fs.writeFileSync(path.join(output,"phase2b-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}

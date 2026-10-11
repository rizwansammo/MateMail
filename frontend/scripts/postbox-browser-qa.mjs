/**
 * PostBox B6: isolated Playwright review against production Next.js build.
 * Every /api/postbox/* response is synthetic. Never use real mail or sessions.
 * Screenshots are human-review evidence, NOT automatic Astra parity proof.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {chromium} from 'playwright';

const base=process.env.POSTBOX_QA_URL||'http://127.0.0.1:3101';
const out=process.env.POSTBOX_QA_OUTPUT||'postbox-qa-artifacts';
fs.mkdirSync(out,{recursive:true});
const report={baseline:'Astra approved 9bab1302ff92',synthetic:true,results:[],screenshots:[],errors:[]};
const person={id:'p-alex',email:'alex@northstar.example',full_name:'Alex Morgan',quota_mb:10240,kind:'personal',organization:'Northstar Studio',domain:'northstar.example'};
const robin={...person,id:'p-robin',email:'robin@northstar.example',full_name:'Robin Ellis'};
const team={...person,id:'p-team',kind:'team_box',email:'support@northstar.example',full_name:'Customer Care'};
const delegate={...person,id:'p-delegate',email:'jamie@northstar.example',full_name:'Jamie Chen'};
const all={can_read:true,can_manage:true,can_send_as:true,can_send_on_behalf:false};
const granted=[{mailbox:person,permissions:all,is_personal:true,access_type:'personal'},
 {mailbox:team,permissions:{...all,can_send_on_behalf:true},is_personal:false,access_type:'team_box'},
 {mailbox:delegate,permissions:{...all,can_send_as:false,can_send_on_behalf:true},is_personal:false,access_type:'delegation'}];
const mailClient={username:person.email,imap:{server:'mx.qa.invalid',port:993,encryption:'SSL/TLS'},
 smtp:{server:'mx.qa.invalid',port:587,encryption:'STARTTLS',auth_required:true}};
const folders=[
 {name:'INBOX',role:'inbox',messages:3,unseen:1,color:null},
 {name:'Drafts',role:'drafts',messages:0,unseen:0,color:null},
 {name:'Sent',role:'sent',messages:1,unseen:0,color:null},
 {name:'Scheduled',role:'scheduled',messages:0,unseen:0,color:null},
 {name:'Archive',role:'archive',messages:0,unseen:0,color:null},
 {name:'Junk',role:'junk',messages:0,unseen:0,color:null},
 {name:'Trash',role:'trash',messages:0,unseen:0,color:null},
];
const msg=(uid,subject,from,date)=>({
 uid,uid_validity:1001,folder:'INBOX',message_id:'<qa-'+uid+'@northstar.example>',
 subject,from:{name:from,address:from.toLowerCase().replace(/\s+/g,'.')+'@example.test'},
 to:[person.email],cc:[],date,size:2048,seen:uid===2,flagged:uid===1,
 answered:false,draft:false,has_attachments:false,in_reply_to:'',references:[],
 preview:uid===1?'Here is the updated roadmap and our next delivery milestones.':'Project documentation and next steps.',
 labels:uid===1?[{id:'label-1',name:'Priority',color:'#3560a3'}]:[]
});
const messages=[
 msg(1,'Quarterly roadmap and delivery milestones','Jamie Chen','2026-10-11T09:18:00Z'),
 msg(2,'Project review and next steps','Robin Ellis','2026-10-10T15:32:00Z'),
 msg(3,'Updated service documentation','Taylor Brooks','2026-10-09T11:12:00Z')
];
const threadMember=(m)=>({...m,copies:[{folder:m.folder,uid:m.uid,uid_validity:m.uid_validity,seen:m.seen,flagged:m.flagged}]});
const conversation={id:'thread-qa-1',subject:messages[0].subject,latest:threadMember(messages[0]),
 latest_date:messages[0].date,message_count:2,unread_count:1,flagged:true,
 participants:['Jamie Chen','Alex Morgan'],has_inbox:true};
const detail=(m)=>({...m,reply_to:'',bcc:[],text:'Hi Alex,\n\nHere is the updated roadmap and our next delivery milestones.\n\nBest regards,\n'+m.from.name,
 html:'',remote_images_blocked:false,attachments:[]});
const defaults={theme:'light',density:'extra_compact',reading_pane:'off',list_view:'messages',
 reader_view:'single',load_remote_images:false,messages_per_page:50,timezone_name:'UTC',
 notify_in_app:true,notify_sound:false,default_identity:''};
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
let failed=false;

function check(name,ok,context={}){
 const result={name,passed:Boolean(ok),...context};
 report.results.push(result);
 if(!ok)throw new Error(name+': '+JSON.stringify(context));
}
async function shot(page,name){
 const filename=name+'.png';
 await page.screenshot({path:path.join(out,filename),animations:'disabled',fullPage:false});
 report.screenshots.push(filename);
}
async function prepare({viewport={width:1440,height:900},density='extra_compact',theme='light',readerView='single',listView='messages'}={}){
 const state={preferences:{...defaults,density,theme,reader_view:readerView,list_view:listView},mailbox:person,auth:person,switches:[]};
 const context=await browser.newContext({viewport,deviceScaleFactor:1,reducedMotion:'reduce',colorScheme:theme==='dark'?'dark':'light'});
 await context.route('**/api/postbox/**',async route=>{
  const req=route.request();const url=new URL(req.url()),endpoint=url.pathname.slice('/api/postbox'.length),method=req.method();
  if(endpoint==='/events/'){await route.fulfill({status:204,body:''});return;}
  let data={results:[]},status=200;
  if(endpoint==='/auth/me/')data={mailbox:state.mailbox,authenticated_mailbox:state.auth,
    available_mailboxes:granted,permissions:all,preferences:state.preferences,mail_client:mailClient};
  else if(endpoint==='/auth/accounts/')data={results:[
    {session_id:'session-alex',mailbox:person,expires_at:'2026-12-31T00:00:00Z',remembered:true,current:true},
    {session_id:'session-robin',mailbox:robin,expires_at:'2026-12-31T00:00:00Z',remembered:true,current:false}
  ],current_session_id:'session-alex'};
  else if(endpoint==='/auth/mailbox-switch/'&&method==='POST'){
   const id=JSON.parse(req.postData()||'{}').mailbox_id;
   state.switches.push({type:'mailbox',id});state.mailbox=id==='p-team'?team:id==='p-delegate'?delegate:person;
   data={mailbox:state.mailbox,authenticated_mailbox:state.auth,available_mailboxes:granted,permissions:all,preferences:state.preferences};
  }
  else if(endpoint==='/auth/switch/'&&method==='POST'){
   state.switches.push({type:'account',id:JSON.parse(req.postData()||'{}').session_id});
   data={mailbox:robin};
  }
  else if(endpoint==='/folders/')data={results:folders};
  else if(endpoint==='/labels/')data={results:[{id:'label-1',name:'Priority',count:1,color:'#3560a3'}]};
  else if(endpoint==='/identities/')data={results:[{address:person.email,name:'Alex Morgan',is_primary:true,kind:'personal'}]};
  else if(endpoint==='/signatures/')data={results:[]};
  else if(endpoint==='/contacts/')data={results:[]};
  else if(endpoint==='/scheduled/')data={results:[]};
  else if(endpoint==='/messages/')data={folder:'INBOX',scope:'folder',uid_validity:1001,page:1,page_size:50,total:messages.length,has_next:false,results:messages};
  else if(endpoint==='/conversations/')data={scope:'inbox',page:1,page_size:50,total:1,has_next:false,results:[conversation]};
  else if(endpoint==='/conversations/for-message/')data={...conversation,messages:[threadMember(messages[1]),threadMember(messages[0])]};
  else if(/^\/messages\/[^/]+\/\d+\/$/.test(endpoint))data=detail(messages.find(m=>endpoint.includes('/'+m.uid+'/'))||messages[0]);
  else if(endpoint==='/preferences/'){
   if(method==='PATCH')Object.assign(state.preferences,JSON.parse(req.postData()||'{}'));
   data=state.preferences;
  }
  else if(endpoint==='/account/')data={email:person.email,full_name:person.full_name,organization:person.organization,
   storage:{available:true,quota_mb:10240,used_mb:256,percent:2.5}};
  else if(endpoint==='/forwarding/')data={manageable_here:false,detail:'Managed by organization',results:[]};
  else if(endpoint==='/security/sessions/')data={results:[]};
  else if(endpoint==='/rules/'||endpoint==='/vacation/'||endpoint==='/notifications/')data={results:[]};
  else if(method==='POST'||method==='PATCH'||method==='DELETE'){data={detail:'Synthetic QA only'};}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 });
 const page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 return {context,page,state,errors};
}
async function visit(p,url='/postbox?folder=INBOX&view=messages'){
 await p.goto(base+url,{waitUntil:'domcontentloaded',timeout:60000});
 const selector=url.startsWith('/postbox/settings')?'.pb-premium-settings-nav':'.pb-astra-app-rail';
 await p.locator(selector).waitFor({state:'visible',timeout:30000});
}
async function scenario(name,fn){
 let context;
 try{
  const test=await prepare(name.options||{});
  context=test.context;
  await fn(test);
 }catch(e){
  failed=true;report.errors.push({scenario:name.label,error:e.stack||String(e)});
 }finally{if(context)await context.close();}
}
try{
 await scenario({label:'desktop Inbox, account and compose',options:{density:'extra_compact'}},async({page,errors})=>{
  await visit(page);
  await page.locator('.pb-premium-message-open').first().waitFor({timeout:15000});
  await shot(page,'01-inbox-extra-compact');
  check('three application rail routes',await page.locator('.pb-astra-app-rail a').count()===3);
  check('mail list visible',await page.locator('.pb-premium-message-row').count()>=2);
  check('bounded API preview text rendered in Inbox row',
    await page.locator('.pb-premium-message-row .pb-astra-row-preview').first().isVisible());
  check('prototype-style label badge renders real label metadata',
    await page.locator('.pb-premium-message-row .pb-message-label').first().isVisible());
  const badge=await page.locator('.pb-premium-message-row .pb-message-label').first().evaluate(el=>{
    const style=getComputedStyle(el);return {background:style.backgroundColor,color:style.color,radius:style.borderRadius};
  });
  check('badge uses visible color and sharp corners',badge.background!=='rgba(0, 0, 0, 0)'&&badge.radius==='3px',badge);
  const layout=await page.evaluate(()=>({width:document.documentElement.scrollWidth,inner:innerWidth,rail:Math.round(document.querySelector('.pb-astra-app-rail')?.getBoundingClientRect().width||0)}));
  check('desktop rail is compact',layout.rail>=45&&layout.rail<=70,layout);
  const railStart=await page.locator('.pb-astra-app-rail').evaluate(el=>{
    const first=el.querySelector('.pb-astra-rail-item');
    return {railTop:el.getBoundingClientRect().top,
      firstItemTop:first?.getBoundingClientRect().top ?? -1,
      inset:Math.round((first?.getBoundingClientRect().top ?? 0)-el.getBoundingClientRect().top)};
  });
  check('desktop Mail app icon starts at top without blank logo slot',
    railStart.inset>=0&&railStart.inset<=16,railStart);
  await page.locator('details.pb-premium-account summary').click();
  await page.locator('.pb-premium-account-panel').waitFor({state:'visible'});
  await shot(page,'02-account-popover');
  check('accounts, TeamBox and delegation are distinct',await page.getByText('Accounts on this device').count()>=1&&await page.getByText('TeamBoxes',{exact:true}).count()>=1&&await page.getByText('Delegated mailboxes',{exact:true}).count()>=1);
  check('Contacts is not inside account menu',await page.locator('.pb-premium-account-contacts').count()===0);
  await page.locator('details.pb-premium-account summary').click();
  await page.goto(base+'/postbox?compose=new',{waitUntil:'domcontentloaded'});
  await page.locator('.pb-premium-compose-shell').waitFor({state:'visible',timeout:15000});
  await shot(page,'03-compose');
  check('compose is real production component',await page.locator('.pb-compose-fields').count()>=1);
  check('no desktop JS exception',errors.length===0,{errors});
 });
 for(const density of ['comfortable','compact','extra_compact']){
  await scenario({label:'reader '+density,options:{density,readerView:'single'}},async({page,errors})=>{
   await visit(page);
   await page.locator('.pb-premium-message-open').first().click();
   await page.locator('.pb-premium-reader-heading').waitFor({state:'visible',timeout:15000});
   await shot(page,'04-reader-'+density);
   const fontsize=await page.locator('.pb-premium-reader-heading h1').evaluate(el=>parseFloat(getComputedStyle(el).fontSize));
   check('single reader heading scales for '+density,density==='extra_compact'?fontsize<=22:density==='compact'?fontsize<=25:fontsize>=24,{fontsize});
   check('reader has Back control',await page.getByRole('button',{name:'Back to mailbox'}).count()===1);
   check('no reader JS exception '+density,errors.length===0,{errors});
  });
 }
 for(const density of ['comfortable','compact','extra_compact']){
  await scenario({label:'thread '+density,options:{density,readerView:'thread'}},async({page,errors})=>{
   await visit(page);
   await page.locator('.pb-premium-message-open').first().click();
   await page.locator('.pb-thread-card').first().waitFor({state:'visible',timeout:16000});
   await shot(page,'05-conversation-'+density);
   const radius=await page.locator('.pb-thread-card').first().evaluate(el=>parseFloat(getComputedStyle(el).borderTopLeftRadius));
   const target={comfortable:10,compact:6,extra_compact:3}[density];
   check('thread card radius '+density,radius===target,{radius,target});
   check('no conversation JS exception '+density,errors.length===0,{errors});
  });
 }
 await scenario({label:'settings centered and live-API shape',options:{}},async({page,errors})=>{
  await visit(page,'/postbox/settings?section=clients');
  await page.getByText('Your sign-in details').first().waitFor({state:'visible',timeout:15000});
  await shot(page,'06-settings-client-setup');
  check('IMAP and SMTP use mocked API hostname',await page.getByText('mx.qa.invalid').count()>=2);
  const m=await page.locator('.pb-settings-content').evaluate(el=>{
   const a=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();
   return {left:Math.round(a.left-b.left),right:Math.round(b.right-a.right)};
  });
  check('settings cards centered',Math.abs(m.left-m.right)<=5,m);
  const settingNav=await page.locator('.pb-premium-settings-nav').evaluate(el=>{
    const rect=el.getBoundingClientRect();return {x:Math.round(rect.x),width:Math.round(rect.width)};
  });
  check('Settings begins with its own left navigation',settingNav.x<=2,settingNav);
  check('Settings hides Mail and Folder navigation',!(await page.locator('.pb-astra-app-rail').isVisible())
    &&!(await page.locator('.pb-premium-sidebar').isVisible()));
  check('settings page without JS exception',errors.length===0,{errors});
 });
 await scenario({label:'dark theme and mobile layout',options:{viewport:{width:390,height:844},theme:'dark',density:'extra_compact'}},async({page,errors})=>{
  await visit(page);
  await shot(page,'07-phone-closed-dark');
  const w=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth}));
  check('no mobile document horizontal overflow',w.document<=w.viewport+2,w);
  const mobileRailStart=await page.locator('.pb-astra-app-rail').evaluate(el=>{
    const first=el.querySelector('.pb-astra-rail-item');
    return {inset:Math.round((first?.getBoundingClientRect().top ?? 0)-el.getBoundingClientRect().top)};
  });
  check('mobile Mail app icon starts at top without blank logo slot',
    mobileRailStart.inset>=0&&mobileRailStart.inset<=16,mobileRailStart);
  await page.getByRole('button',{name:'Open folders'}).click();
  await shot(page,'08-phone-sidebar-open');
  check('mobile folder drawer opens',await page.locator('.pb-premium-sidebar[data-open=true]').count()===1);
  check('mobile no JS exception',errors.length===0,{errors});
 });
}catch(e){failed=true;report.errors.push({scenario:'runner',error:e.stack||String(e)})}
finally{
 report.summary={passed:report.results.filter(x=>x.passed).length,total:report.results.length,scenariosFailed:report.errors.length};
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
 await browser.close();
 console.log('PostBox B6 QA:',JSON.stringify(report.summary));
 for(const x of report.errors)console.error(x.scenario+': '+x.error.slice(0,1400));
 if(failed)process.exitCode=1;
}

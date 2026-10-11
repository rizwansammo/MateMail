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
 labels:uid===1?[{id:'label-1',name:'Priority',color:'#3560a3'}]:uid===2?[{id:'label-2',name:'Projects',color:'#21886d'}]:[]
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
async function prepare({viewport={width:1440,height:900},density='extra_compact',theme='light',readerView='single',listView='messages',readingPane='off'}={}){
 const state={preferences:{...defaults,density,theme,reader_view:readerView,list_view:listView,reading_pane:readingPane},mailbox:person,auth:person,switches:[]};
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
  const rows=await page.locator('.pb-premium-message-row').evaluateAll(items=>items.slice(0,2).map(el=>{
    const rect=node=>{const r=node?.getBoundingClientRect();return r?{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),right:Math.round(r.right),height:Math.round(r.height)}:null};
    return {row:rect(el),sender:rect(el.querySelector('.pb-row-from')),
      content:rect(el.querySelector('.pb-astra-row-content')),preview:rect(el.querySelector('.pb-astra-row-preview')),
      label:rect(el.querySelector('.pb-astra-row-label-cell')),badge:rect(el.querySelector('.pb-message-label')),
      attachment:rect(el.querySelector('.pb-premium-row-indicators')),time:rect(el.querySelector('time'))};
  }));
  check('Inbox rows match approved Extra Compact 44px height',rows.length===2&&rows.every(r=>r.row?.height===44),rows);
  check('Label is a separate sibling of preview',await page.locator('.pb-premium-message-row .pb-astra-row-content .pb-message-label').count()===0);
  check('Labels share one fixed right-side column regardless of preview length',
    rows.length===2&&rows.every(r=>r.label&&r.badge&&r.preview)&&
    rows[0].label.x===rows[1].label.x&&rows[0].label.width===80&&
    rows[0].label.x>rows[0].content.right-2&&
    rows.every(r=>r.label.right<=r.attachment.x&&r.attachment.right<=r.time.x),rows);
  check('Subject and sender columns align across rows',rows.length===2&&
    rows[0].sender.x===rows[1].sender.x&&rows[0].content.x===rows[1].content.x,rows);
  check('Subject starts close to approved prototype x-position',
    rows[0].content.x>500&&rows[0].content.x<600,rows);
  check('Message footer is visible with real count and selected density',
    await page.locator('.pb-astra-list-footer').isVisible()&&
    (await page.locator('.pb-astra-list-footer').innerText()).includes('extra compact'));
  check('Prototype bottom status strip shows real mailbox context',
    await page.locator('.pb-astra-app-status').isVisible()&&
    (await page.locator('.pb-astra-app-status').innerText()).includes('Personal mailbox'));
  const strip=await page.locator('.pb-astra-app-status').evaluate(el=>Math.round(el.getBoundingClientRect().height));
  check('Prototype application status strip height is 27px',strip===27,{strip});
  check('Toolbar has real reading pane control rather than a More icon',
    await page.getByRole('button',{name:'Reading pane and mailbox actions'}).count()===0&&
    await page.locator('.pb-astra-pane-menu summary').count()===1);
  check('Sidebar keeps real management but uses prototype presentation',
    await page.getByText('MY FOLDERS',{exact:true}).count()===1&&
    await page.locator('.pb-astra-nav-label-swatch').count()>=1);
  const layout=await page.evaluate(()=>({width:document.documentElement.scrollWidth,inner:innerWidth,rail:Math.round(document.querySelector('.pb-astra-app-rail')?.getBoundingClientRect().width||0)}));
  check('desktop rail matches approved 62px',layout.rail===62,layout);
  const geometry=await page.evaluate(()=>{
    const rect=s=>{const n=document.querySelector(s);if(!n)return null;const x=n.getBoundingClientRect();return {width:Math.round(x.width),height:Math.round(x.height),left:Math.round(x.left),top:Math.round(x.top)}};
    return {rail:rect('.pb-astra-app-rail'),sidebar:rect('.pb-premium-sidebar'),brand:rect('.pb-premium-brand'),
      topbar:rect('.pb-premium-topbar'),compose:rect('.pb-premium-compose'),toolbar:rect('.pb-premium-mail-toolbar'),
      firstFolder:rect('.pb-premium-nav-link')};
  });
  check('sidebar width matches approved source 227px',geometry.sidebar?.width===227,geometry);
  check('brand height matches approved source 78px',geometry.brand?.height===78,geometry);
  check('topbar height matches approved 65px',geometry.topbar?.height===65,geometry);
  check('compose launcher matches approved 40px',geometry.compose?.height===40,geometry);
  check('Inbox toolbar matches approved 44px',geometry.toolbar?.height===44,geometry);
  check('folder rows match approved 34px',geometry.firstFolder?.height===34,geometry);
  const sidebarGeometry=await page.evaluate(()=>{
    const get=selector=>document.querySelector(selector);
    const r=selector=>{const rect=get(selector)?.getBoundingClientRect();
      return rect?{x:Math.round(rect.x),y:Math.round(rect.y),width:Math.round(rect.width),
      height:Math.round(rect.height),bottom:Math.round(rect.bottom)}:null};
    const side=get('.pb-premium-sidebar');
    const nav=get('.pb-premium-nav');
    const pinned=[...document.querySelectorAll('.pb-premium-nav > .pb-premium-nav-link[data-role]')];
    const headers=[...document.querySelectorAll('.pb-organize-section-header')];
    const firstLabel=get('.pb-organize-nav-row .pb-premium-nav-link');
    return {sidebar:r('.pb-premium-sidebar'),nav:r('.pb-premium-nav'),
      sidebarPaddingLeft:parseFloat(getComputedStyle(side).paddingLeft),
      sidebarPaddingRight:parseFloat(getComputedStyle(side).paddingRight),
      navPaddingBottom:parseFloat(getComputedStyle(nav).paddingBottom),
      pinned:pinned.map(n=>({role:n.getAttribute('data-role'),
        y:Math.round(n.getBoundingClientRect().y),height:Math.round(n.getBoundingClientRect().height)})),
      headers:headers.map(n=>({y:Math.round(n.getBoundingClientRect().y),
        height:Math.round(n.getBoundingClientRect().height),
        marginTop:parseFloat(getComputedStyle(n).marginTop)})),
      firstCustom:firstLabel?{height:Math.round(firstLabel.getBoundingClientRect().height)}:null
    };
  });
  check('sidebar fills viewport height with 13px side padding',
    sidebarGeometry.sidebar?.height===900&&sidebarGeometry.sidebarPaddingLeft===13&&
    sidebarGeometry.sidebarPaddingRight===13,sidebarGeometry);
  check('sidebar scroll area preserves prototype 18px bottom padding',
    sidebarGeometry.nav?.height>200&&sidebarGeometry.navPaddingBottom===18,sidebarGeometry);
  check('standard folder rows have 2px gaps with source order',
    sidebarGeometry.pinned.length>=6&&sidebarGeometry.pinned.every((row,i,arr)=>
      row.height===34&&(i===0||row.y-arr[i-1].y===36)),sidebarGeometry);
  check('folder/label headers use 22px group gap and 30px heading',
    sidebarGeometry.headers.length===2&&sidebarGeometry.headers.every(h=>
      h.marginTop===22&&h.height===30),sidebarGeometry);
  check('first section starts after pinned folders and 22px margin',
    sidebarGeometry.headers.length===2&&sidebarGeometry.pinned.length>=6&&
    sidebarGeometry.headers[0].y - 
    (sidebarGeometry.pinned[sidebarGeometry.pinned.length-1].y+34)===22,
    sidebarGeometry);

  const alignment=await page.evaluate(()=>{
    const get=selector=>document.querySelector(selector);
    const box=node=>node?.getBoundingClientRect()||null;
    const left=node=>{const r=box(node);return r?Math.round(r.left):null};
    const top=node=>{const r=box(node);return r?Math.round(r.top):null};
    const brand=get('.pb-premium-brand-mark');
    const wordmark=get('.pb-premium-wordmark');
    const button=get('.pb-premium-compose');
    const inbox=get('.pb-premium-nav-link[data-role="inbox"]');
    const composeIcon=button?.querySelector('svg');
    const inboxIcon=inbox?.querySelector('svg');
    const inboxText=inbox?.querySelector('span');
    const textNode=Array.from(button?.childNodes||[]).find(n=>n.nodeType===Node.TEXT_NODE&&n.textContent?.trim());
    const range=document.createRange();
    if(textNode)range.selectNodeContents(textNode);
    const railItem=get('.pb-astra-rail-item');
    return {
      brandIconX:left(brand),composeBoxX:left(button),inboxBoxX:left(inbox),
      composeIconX:left(composeIcon),inboxIconX:left(inboxIcon),
      wordmarkTextX:left(wordmark),composeTextX:textNode?Math.round(range.getBoundingClientRect().left):null,
      inboxTextX:left(inboxText),
      railFirstY:top(railItem),brandIconY:top(brand),
      composeY:top(button),inboxY:top(inbox),
      railIconY:top(railItem?.querySelector('svg'))
    };
  });
  const near=(a,b,tolerance=1)=>a!==null&&b!==null&&Math.abs(a-b)<=tolerance;
  check('brand 4px inset and Compose/Inbox box alignment match source',
    alignment.brandIconX===alignment.composeBoxX+4&&
    near(alignment.composeBoxX,alignment.inboxBoxX),alignment);
  check('Compose icon has 4px offset including 1px button border',
    alignment.composeIconX===alignment.inboxIconX+4,alignment);
  check('sidebar wordmark, Compose and Inbox label offsets match source',
    near(alignment.wordmarkTextX,alignment.composeTextX,3)&&
    near(alignment.composeTextX,alignment.inboxTextX,4),alignment);
  check('rail and 78px branded header align in approved composition',
    alignment.railFirstY===14&&alignment.brandIconY>=24&&alignment.brandIconY<=25&&
    Math.abs(alignment.railIconY-alignment.brandIconY)<=7,alignment);
  check('New Message and Inbox follow approved 78px header and 18px spacer',
    alignment.composeY===78&&alignment.inboxY===136,alignment);
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
  await page.locator('.pb-astra-composer-window').waitFor({state:'visible',timeout:15000});
  await shot(page,'03-compose');
  check('compose is real production component',await page.locator('.pb-compose-fields').count()>=1);
  const composeGeometry=await page.locator('.pb-astra-composer-window').evaluate(el=>{
    const head=el.querySelector('.pb-compose-head'),format=el.querySelector('.pb-astra-compose-format');
    const rect=n=>Math.round(n?.getBoundingClientRect().height||0);
    return {width:Math.round(el.getBoundingClientRect().width),height:rect(el),
      header:rect(head),format:rect(format),bottom:Math.round(innerHeight-el.getBoundingClientRect().bottom)};
  });
  check('Compose width matches approved 575px',composeGeometry.width===575,composeGeometry);
  check('Compose header matches approved 44px',composeGeometry.header===44,composeGeometry);
  check('Compose toolbar matches approved 42px',composeGeometry.format===42,composeGeometry);
  check('Compose is a floating bottom-right window',composeGeometry.bottom>=20&&composeGeometry.bottom<=32,composeGeometry);
  check('Prototype rich editing tools are present',
    await page.getByRole('button',{name:'Bold'}).count()===1&&
    await page.getByRole('button',{name:'Italic'}).count()===1&&
    await page.getByRole('button',{name:'Underline'}).count()===1&&
    await page.getByRole('button',{name:'Schedule send'}).count()===1);
  const editor=page.getByRole('textbox',{name:'Message body'});
  await editor.fill('This draft must survive minimizing.');
  await page.getByRole('button',{name:'Minimize composer'}).click();
  const minimizedHeight=await page.locator('.pb-astra-composer-window').evaluate(el=>Math.round(el.getBoundingClientRect().height));
  check('minimized Compose matches approved 45px',minimizedHeight===45,{minimizedHeight});
  await page.getByRole('button',{name:'Restore composer'}).click();
  check('Compose draft text survives minimize/restore',(await editor.innerText()).includes('This draft must survive minimizing.'));
  await editor.click();
  await page.keyboard.press('ControlOrMeta+A');
  await page.getByRole('button',{name:'Bold'}).click();
  const richHtml=await editor.innerHTML();
  check('Bold toolbar produces real outgoing HTML',/<(b|strong)(\s|>)/i.test(richHtml),{html:richHtml.slice(0,240)});
  await shot(page,'03-compose-richtext');
  check('no desktop JS exception',errors.length===0,{errors});
 });
 await scenario({label:'Conversation list has same single-line geometry',options:{listView:'conversations'}},async({page,errors})=>{
   await visit(page,'/postbox?folder=INBOX&view=conversations');
   await page.locator('.pb-astra-conversation-row').first().waitFor({state:'visible',timeout:15000});
   await shot(page,'01-conversations-list');
   const row=await page.locator('.pb-astra-conversation-row').first().evaluate(el=>{
     const r=el.getBoundingClientRect(),label=el.querySelector('.pb-astra-row-label-cell')?.getBoundingClientRect();
     return {height:Math.round(r.height),labelWidth:Math.round(label?.width||0)};
   });
   check('Conversation row uses approved 44px list and fixed label column',row.height===44&&row.labelWidth===80,row);
   check('Conversation rows retain real click-to-open action',
     await page.locator('.pb-astra-conversation-open').first().isVisible());
   check('No conversation list JS errors',errors.length===0,{errors});
 });
 await scenario({label:'Full HD 1920px Inbox visual geometry',options:{viewport:{width:1920,height:900}}},async({page,errors})=>{
   await visit(page);
   await page.locator('.pb-premium-message-row').first().waitFor({state:'visible'});
   await shot(page,'01-inbox-reference-1920');
   const size=await page.evaluate(()=>{
     const label=document.querySelector('.pb-premium-message-row .pb-astra-row-label-cell')?.getBoundingClientRect();
     const row=document.querySelector('.pb-premium-message-row')?.getBoundingClientRect();
     return {labelX:Math.round(label?.x||0),rowWidth:Math.round(row?.width||0),viewport:innerWidth,scroll:document.documentElement.scrollWidth};
   });
   check('Label fixed near right edge on Full HD screenshot',
     size.viewport===1920&&size.labelX>1550&&size.labelX<1810&&size.scroll<=size.viewport+2,size);
   const sidebarEdge=await page.locator('.pb-premium-sidebar').evaluate(el=>Math.round(el.getBoundingClientRect().right));
   check('Full HD mail content follows original 62px rail and 227px sidebar',
     sidebarEdge===289,{sidebarEdge});
   check('Full HD Inbox has no JS errors',errors.length===0,{errors});
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
   const fullPane=await page.locator('.pb-mail-layout').evaluate(el=>{
     const list=el.querySelector('.pb-mail-list-pane'),reader=el.querySelector('.pb-mail-reader-pane');
     const rect=x=>x?.getBoundingClientRect();
     return {layout:el.className,listVisible:getComputedStyle(list).display!=='none',
       readerVisible:getComputedStyle(reader).display!=='none',
       readerHeight:Math.round(rect(reader)?.height||0),
       layoutHeight:Math.round(rect(el)?.height||0),
       readerTop:Math.round(rect(reader)?.top||0),layoutTop:Math.round(rect(el)?.top||0)};
   });
   check('Full message hides Inbox list instead of rendering bottom pane '+density,
     fullPane.layout.includes('pb-layout-full')&&
     fullPane.listVisible===false&&fullPane.readerVisible===true&&
     fullPane.readerHeight>=fullPane.layoutHeight-2&&
     Math.abs(fullPane.readerTop-fullPane.layoutTop)<=2,fullPane);
   await page.getByRole('button',{name:'Back to mailbox'}).click();
   check('Back restores list and hides full reader '+density,
     await page.locator('.pb-mail-list-pane').isVisible()&&
     !await page.locator('.pb-mail-reader-pane').isVisible());
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
 for(const readingPane of ['right','bottom']){
  await scenario({label:'reader preserves '+readingPane+' reading pane',options:{readingPane}},async({page,errors})=>{
   await visit(page);
   await page.locator('.pb-premium-message-open').first().click();
   await page.locator('.pb-premium-reader-heading').waitFor({state:'visible',timeout:15000});
   const panes=await page.locator('.pb-mail-layout').evaluate(el=>{
     const list=el.querySelector('.pb-mail-list-pane'),reader=el.querySelector('.pb-mail-reader-pane');
     const a=list.getBoundingClientRect(),b=reader.getBoundingClientRect();
     return {cls:el.className,listVisible:getComputedStyle(list).display!=='none',
       readerVisible:getComputedStyle(reader).display!=='none',
       list:{x:Math.round(a.x),y:Math.round(a.y),width:Math.round(a.width),height:Math.round(a.height)},
       reader:{x:Math.round(b.x),y:Math.round(b.y),width:Math.round(b.width),height:Math.round(b.height)}};
   });
   const correct=readingPane==='right'
     ? panes.cls.includes('pb-layout-right')&&panes.reader.x>panes.list.x&&
       Math.abs(panes.reader.y-panes.list.y)<=2
     : panes.cls.includes('pb-layout-bottom')&&panes.reader.y>panes.list.y&&
       Math.abs(panes.reader.x-panes.list.x)<=2;
   check('Reading pane '+readingPane+' keeps two separate visible panes',
     panes.listVisible&&panes.readerVisible&&correct,panes);
   await shot(page,'04-reader-layout-'+readingPane);
   check('No reader layout JS errors '+readingPane,errors.length===0,{errors});
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
  const mobileAlignment=await page.evaluate(()=>{
    const box=selector=>document.querySelector(selector)?.getBoundingClientRect();
    const brand=box('.pb-premium-brand-mark'),compose=box('.pb-premium-compose'),inbox=box('.pb-premium-nav-link[data-role="inbox"]');
    const rail=box('.pb-astra-rail-item');
    return {brandLeft:Math.round(brand?.left??-1),composeLeft:Math.round(compose?.left??-1),
      inboxLeft:Math.round(inbox?.left??-1),brandTop:Math.round(brand?.top??-1),
      composeTop:Math.round(compose?.top??-1),inboxTop:Math.round(inbox?.top??-1),
      railFirstTop:Math.round(rail?.top??-1)};
  });
  check('mobile brand 4px inset and compose/Inbox box alignment',
    mobileAlignment.brandLeft===mobileAlignment.composeLeft+4&&
    mobileAlignment.composeLeft===mobileAlignment.inboxLeft,mobileAlignment);
  check('mobile sidebar matches approved 78px brand and 18px spacer',
    mobileAlignment.railFirstTop===14&&mobileAlignment.composeTop===78&&mobileAlignment.inboxTop===136,
    mobileAlignment);
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

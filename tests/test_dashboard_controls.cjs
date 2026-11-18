// Exercise the actual UI refresh function without requiring a browser or VM.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/dist/app.js'),'utf8');
const refresh=source.slice(source.indexOf('async function refreshLive()'),source.indexOf('async function liveAction('));
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{replaceChildren(){}});return elements.get(id);};
const old={id:'old',status:'complete',system:'static',created_at:1,message:'Complete'};
const active={id:'new',status:'running',system:'predictive-ml',created_at:2,message:'Running'};
const context=vm.createContext({
  $:element, Option:class {}, generation:0, liveRefreshing:false, liveState:null,
  liveId:'old',activeLiveId:'',liveActionPending:false,viewingLive:false,state:{labels:{}},
  liveLabels:{},
  api:async()=>({sessions:[active,old],busy:true,available:true}),error:message=>{throw Error(message);}
});
vm.runInContext(refresh,context);
(async()=>{
  await context.refreshLive();
  assert.equal(element('#live-start').disabled,true);
  assert.equal(element('#live-stop').disabled,false);
  assert.equal(element('#live-follow').hidden,false);
  assert.match(element('#live-status').textContent,/older session/);
  let resolve;
  context.api=()=>new Promise(done=>{resolve=done;});
  const pending=context.refreshLive();
  context.generation++;
  context.liveId='just-started';
  resolve({sessions:[old],busy:false,available:true});
  await pending;
  assert.equal(context.liveId,'just-started','A stale refresh must not replace the newly started session');
  assert.equal(context.liveRefreshing,false);
  console.log('PASS: active-run controls and stale-refresh race');
})().catch(error=>{console.error(error);process.exitCode=1;});

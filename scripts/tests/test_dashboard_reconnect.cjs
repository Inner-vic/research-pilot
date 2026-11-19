"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../../skills/sdm-module/sdm-lifecycle/references/dashboard/dashboard.js"),"utf8");
const load=source.slice(source.indexOf("let loadRetry="),source.indexOf("async function stream"));
async function scenario(status){
 let calls=0,applied=null,streams=0,next=1;const timers=new Map(),refresh={disabled:false};
 const context={state:{stream:null},$:()=>refresh,query:x=>x,api:async()=>{calls++;if(calls===1)throw Object.assign(new Error("unavailable"),{status});return {revision:"actual-second-read"};},
  apply:x=>{applied=x;},stream:()=>{streams++;},connection:()=>{},banner:()=>{},
  setTimeout:(fn,ms)=>{assert.equal(ms,3000);const id=next++;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)};
 vm.createContext(context);vm.runInContext(load+";this.reload=load;",context);
 await context.reload(true);assert.equal(calls,1);assert.equal(applied,null);assert.equal(refresh.disabled,false);
 if(status===403){assert.equal(timers.size,0);assert.equal(streams,0);return;}
 assert.equal(timers.size,1);await [...timers.values()][0]();
 assert.equal(calls,2);assert.equal(applied.revision,"actual-second-read");assert.equal(streams,1);assert.equal(timers.size,0);
}
(async()=>{await scenario(409);await scenario(undefined);await scenario(403);console.log("First-read transient and network failures reconnect; authentication rejection does not loop.");})().catch(e=>{console.error(e);process.exitCode=1;});

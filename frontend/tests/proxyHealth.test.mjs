import test from 'node:test';
import assert from 'node:assert/strict';
import {currentHealth} from '../.test-build/proxyHealthState.js';
const now=Date.parse('2026-10-09T12:00:00Z');
const up={backend:'backend_app',state:'up',reason:'Available',available:1,total:1,targets:[]};
const data={online:true,captured_at:new Date(now).toISOString(),document_version:7,hosts:{app:up},routes:{legacy:{...up,state:'down'}}};
test('native and imported host health remain separate',()=>{
  assert.equal(currentHealth(data,'hosts','app',7,now).state,'up');
  assert.equal(currentHealth(data,'routes','legacy',7,now).state,'down');
  assert.equal(currentHealth(data,'hosts','legacy',7,now).state,'unknown');
});
test('changed draft cannot borrow previous draft success',()=>{
  assert.equal(currentHealth(data,'hosts','app',8,now).state,'loading');
});
test('stale, invalid and implausible future observations never remain green',()=>{
  for(const captured_at of [new Date(now-45001).toISOString(),'invalid',new Date(now+10001).toISOString()]){
    const result=currentHealth({...data,captured_at},'hosts','app',7,now);
    assert.equal(result.state,'stale');assert.equal(result.available,0);assert.deepEqual(result.targets,[]);
  }
  assert.equal(currentHealth(data,'hosts','app',7,now+45000).state,'up');
});
test('failed poll suppresses previous successful data',()=>{
  const result=currentHealth(data,'hosts','app',7,now,'Abfrage fehlgeschlagen.');
  assert.equal(result.state,'unknown');assert.equal(result.reason,'Abfrage fehlgeschlagen.');assert.equal(result.available,0);
});
test('first poll has its own loading state',()=>assert.equal(currentHealth(null,'hosts','app',7,now).state,'loading'));

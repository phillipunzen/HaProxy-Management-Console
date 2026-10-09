import test from 'node:test';
import assert from 'node:assert/strict';
import {addOptions,forwardedOptions} from '../.test-build/proxyOptionTemplates.js';
test('header preset preserves existing custom values and does not duplicate them',()=>{
  const existing=['http-request set-path /reset-password%[path]','http-request set-header x-forwarded-proto https','option forwardfor header X-Client-IP'];
  const result=addOptions(existing,forwardedOptions);
  assert.deepEqual(result.slice(0,3),existing);
  assert.equal(result.filter(line=>line.toLowerCase().includes('set-header x-forwarded-proto')).length,1);
  assert.equal(result.filter(line=>line.startsWith('option forwardfor')).length,1);
  assert.deepEqual(addOptions(result,forwardedOptions),result);
});
test('path preset does not append a second rewrite or treat a comment as a rule',()=>{
  assert.deepEqual(addOptions(['http-request set-path /custom%[path]'],['http-request set-path /reset-password%[path]']),['http-request set-path /custom%[path]']);
  assert.deepEqual(addOptions(['# http-request set-path /comment'],['http-request set-path /real']),['# http-request set-path /comment','http-request set-path /real']);
});

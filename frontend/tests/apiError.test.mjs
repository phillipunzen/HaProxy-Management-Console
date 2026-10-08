import test from 'node:test';
import assert from 'node:assert/strict';
import { apiError } from '../.test-build/apiError.js';

const message = 'Der Agent stellt den Datei-Import nicht bereit (/config-bundle fehlt).';
const code = 'agent_update_required';
const cases = [
  ['textual errors', {detail: 'Dateipfad prüfen.'}, 'Dateipfad prüfen.'],
  ['new response format', {detail: message, code}, message, code],
  ['previous structured format', {detail: {message, code}}, message, code],
  ['nested agent errors', {detail: {detail: {message: 'Datei nicht lesbar.'}}}, 'Datei nicht lesbar.'],
  ['validation errors', {detail: [{loc:['body','config'],msg:'Field required'},{loc:['body','maps',0,'path'],msg:'Invalid path'}]}, 'config: Field required\nmaps.0.path: Invalid path'],
  ['validation errors without location', {detail: [{msg:'Invalid request'},'Retry later']}, 'Invalid request\nRetry later'],
  ['empty response', null, 'Anfrage fehlgeschlagen.'],
  ['unknown object response', {detail: {code, input:'must not be exposed',context:{token:'must not be exposed'}}}, 'Anfrage fehlgeschlagen.', code],
  ['invalid message object', {detail: {message: {unexpected: true}}}, 'Anfrage fehlgeschlagen.'],
  ['empty detail with textual message', {detail: [],message:'Fehler beim Agenten.'}, 'Fehler beim Agenten.'],
  ['invalid code type', {detail:message,code:{unexpected:true}}, message],
];
for (const [name,body,expected,expectedCode] of cases) {
  test(name, () => {
    const result = apiError(body);
    assert(result instanceof Error);
    assert.equal(result.message, expected);
    assert.equal(result.code, expectedCode);
    assert(!result.message.includes('[object Object]'));
    assert(!result.message.includes('must not be exposed'));
  });
}

test('new errors remain readable to the previously released browser parser', () => {
  const body = {detail: message, code};
  // The old frontend passes data.detail directly to the Error constructor.
  assert.equal(new Error(body.detail || 'Anfrage fehlgeschlagen.').message, message);
  assert.equal(new Error({message,code}).message, '[object Object]');
});

test('top-level code takes precedence over legacy code', () => {
  assert.equal(apiError({code:'new',detail:{message,code:'old'}}).code,'new');
});

test('unknown validation fields are not displayed', () => {
  assert.equal(apiError({detail:[{loc:[{},'maps',null,0],msg:'Invalid path',input:'secret'}]}).message,'maps.0: Invalid path');
});

test('unexpected recursive objects remain bounded', () => {
  const detail = {};
  detail.message = detail;
  assert.equal(apiError({detail}).message, 'Anfrage fehlgeschlagen.');
});

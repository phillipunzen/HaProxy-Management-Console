import test from 'node:test';
import assert from 'node:assert/strict';
import {localNetwork,summarizeAccess,manualAccessSections} from '../.test-build/accessVisibility.js';
const profiles=[{id:'lan',name:'Nur lokaler Zugriff',networks:['192.168.10.0/24','fd12:3456::/48']},{id:'office',name:'Office',networks:['198.51.100.7']}];

test('private/loopback/link-local networks must be fully contained, IPv4 and IPv6',()=>{
 for(const value of ['10.0.0.0/8','10.8.1.2','172.16.0.0/12','172.31.255.1','192.168.0.0/16','127.0.0.0/8','169.254.0.0/16','fc00::/7','fd12:3456::/48','FDFF::1','fe80::/10','::1','0:0:0:0:0:0:0:1'])assert.equal(localNetwork(value),true,value);
 for(const value of ['0.0.0.0/0','10.0.0.0/7','172.16.0.0/8','172.32.0.1','192.168.0.0/8','198.51.100.7','::/0','::/127','2001:db8::/32','fc00::/6','fe80::/9','ff00::/8','::ffff:192.168.1.1','::ffff:192.168.0.0/112'])assert.equal(localNetwork(value),false,value);
});
test('invalid address inputs are unknown, never classified internal',()=>{
 for(const value of ['','bad','10.0.0.256','10.0.0.1/33','10.0.0.1/','10.0.0.1/-1','10.0.0.1/24/1','::1%lo','::1/129','1::2::3','1:2:3:4:5:6:7:8:9','::ffff:192.168.1.256',':::1'])assert.equal(localNetwork(value),null,value);
});
test('open entry is external possible even with Basic Auth, backend IP is not used',()=>{
 const result=summarizeAccess({basic_auth_group:1,servers:[{address:'192.168.10.20'}]});assert.equal(result.kind,'external');assert.match(result.detail,/Basic Auth/);
});
test('profile name and own list are resolved from actual networks',()=>{
 assert.equal(summarizeAccess({access_profile:'lan'},profiles).kind,'internal');
 assert.equal(summarizeAccess({access_profile:'office'},profiles).kind,'restricted');
 assert.equal(summarizeAccess({access_policy:{networks:['10.8.0.0/24'],paths:[]}}).kind,'internal');
 assert.equal(summarizeAccess({access_policy:{networks:['10.8.0.0/24','198.51.100.7'],paths:[]}}).kind,'restricted');
 const misleading=[{id:'misleading',name:'Nur intern',networks:['0.0.0.0/0']}];assert.equal(summarizeAccess({access_profile:'misleading'},misleading).kind,'restricted');
});
test('selected path areas never imply whole-entry private access',()=>{
 assert.equal(summarizeAccess({access_profile:'lan',access_paths:['/admin']},profiles).kind,'partial');
 assert.equal(summarizeAccess({access_profile:'lan',access_paths:['/']},profiles).kind,'internal');
 assert.equal(summarizeAccess({path:'/api',access_profile:'lan',access_paths:['/api']},profiles).kind,'internal');
 assert.equal(summarizeAccess({path:'/api',access_profile:'lan',access_paths:['/api/admin']},profiles).kind,'partial');
 assert.equal(summarizeAccess({path:'/api',access_profile:'lan',access_paths:['/admin']},profiles).kind,'review');
 assert.equal(summarizeAccess({access_policy:{networks:['10.8.0.0/24'],paths:['/private']}}).kind,'partial');
});
test('incomplete and conflicting drafts do not receive a reassuring label',()=>{
 for(const assignment of [{access_profile:'missing'},{access_profile:'lan',access_policy:{networks:['10.0.0.0/8'],paths:[]}},{access_paths:['/admin']},{access_policy:{networks:[],paths:[]}},{access_policy:{networks:['invalid'],paths:[]}}])assert.equal(summarizeAccess(assignment,profiles).kind,'review');
});
test('manual restrictions flag only related sections and inherited defaults',()=>{
 const config=`defaults shared
 http-request deny if { path_beg /blocked }
frontend "internal"
 bind :80
 acl local src 192.168.0.0/16
 use_backend protected if local
frontend public
 bind :8080
backend protected
 http-request deny unless local
backend plain
 server origin 192.168.1.10:80 check
frontend stream
 tcp-request content reject if { req.ssl_sni -m end .example.com }
`;
 assert.deepEqual([...manualAccessSections(config)],['*','internal','protected','stream']);
 assert.equal(summarizeAccess({},[],true).kind,'review');
 assert.equal(summarizeAccess({access_profile:'lan'},profiles,true).kind,'internal');
});
test('managed blocks are ignored when a new draft clears or replaces central restrictions',()=>{
 const config=`frontend edge
# haproxy-control-access BEGIN edge
 acl local fc_src 192.168.0.0/16
 http-request deny unless local
# haproxy-control-access END edge
# haproxy-control-basic-auth BEGIN frontend edge
 http-request auth realm Members
# haproxy-control-basic-auth END frontend edge
# haproxy-control-rules BEGIN edge
 http-request deny
# haproxy-control-rules END edge
 http-request set-header X-Forwarded-Proto https
frontend other
 http-request deny if { src 198.51.100.7 }
`;
 assert.deepEqual([...manualAccessSections(config)],['other']);assert.equal(summarizeAccess({},[],manualAccessSections(config).has('edge')).kind,'external');
});

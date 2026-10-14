import test from 'node:test';
import assert from 'node:assert/strict';
import {composeDirectoryPaths,dockerMounts,prepareComposeDirectory,recreateComposeCommands} from '../.test-build/agentSetupGuidance.js';
test('existing compose keeps certificate and vhost paths with directory mounts',()=>{
 const p=composeDirectoryPaths('/srv/haproxy/');
 assert.equal(p.config_path,'/srv/haproxy/config/haproxy.cfg');
 assert.equal(p.cert_dir_config,'/usr/local/etc/haproxy/certs');
 const mounts=dockerMounts(p,'/srv/haproxy/');
 assert.match(mounts,/\/srv\/haproxy\/config:\/usr\/local\/etc\/haproxy:ro/);
 assert.match(mounts,/\/srv\/haproxy\/vhosts:\/usr\/local\/etc\/haproxy\/vhosts:ro/);
 assert.match(mounts,/\/srv\/haproxy\/run:\/run\/haproxy:rw/);
 assert.doesNotMatch(mounts,/haproxy\.cfg:/);
 assert.doesNotMatch(mounts,/\/srv\/haproxy:\/usr/);
});
test('preparation preserves originals and refuses different target copies',()=>{
 const commands=prepareComposeDirectory('/srv/haproxy','haproxy');
 assert.match(commands,/sudo cmp --/);
 assert.match(commands,/sudo cp -p --/);
 assert.match(commands,/docker exec 'haproxy' id -u/);
 assert.match(commands,/docker inspect --format '\{\{\.Image\}\}' 'haproxy'/);
 assert.match(commands,/docker cp .*\/usr\/local\/etc\/haproxy\/errors\/\./);
 assert.match(commands,/\[ ! -e "config\/errors\/\$haproxy_error_name" \]/);
 assert.doesNotMatch(commands,/rm .*config|mv /);
 const recreate=recreateComposeCommands(composeDirectoryPaths('/srv/haproxy'),'haproxy');
 assert.match(recreate,/-f '\/usr\/local\/etc\/haproxy\/vhosts'/);
 assert.match(recreate,/compose -f docker-compose.yml run --rm --no-deps haproxy haproxy -c/);
 assert.doesNotMatch(recreate,/docker exec/);
});
test('shell examples quote user-provided container names',()=>{
 const result=prepareComposeDirectory('/srv/haproxy',"h';touch /tmp/injection;'");
 assert.ok(result.includes("'h'\\'';touch /tmp/injection;'\\'''"));
});

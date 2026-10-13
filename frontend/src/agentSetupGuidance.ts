export type DockerPaths = {config_path:string;container_config_dir:string;runtime_socket:string;runtime_socket_config:string;cert_dir:string;cert_dir_config:string};
const parent=(path:string)=>path.slice(0,path.lastIndexOf('/'));
const q=(value:string)=>"'"+value.replace(/'/g,"'\\''")+"'";
export function composeDirectoryPaths(root:string):DockerPaths {
  root=root.replace(/\/$/,'');
  return {config_path:root+'/config/haproxy.cfg',container_config_dir:'/usr/local/etc/haproxy',runtime_socket:root+'/run/admin.sock',runtime_socket_config:'/run/haproxy/admin.sock',cert_dir:root+'/certs',cert_dir_config:'/usr/local/etc/haproxy/certs'};
}
export function dockerMounts(value:DockerPaths,existingDirectory?:string) {
  const mounts=[`${parent(value.config_path)}:${value.container_config_dir}:ro`,`${parent(value.runtime_socket)}:${parent(value.runtime_socket_config)}:rw`,`${value.cert_dir}:${value.cert_dir_config}:ro`];
  if(existingDirectory)mounts.push(`${existingDirectory.replace(/\/$/,'')}/vhosts:${value.container_config_dir}/vhosts:ro`);
  return 'volumes:\n'+mounts.map(m=>'  - '+JSON.stringify(m)).join('\n');
}
export function prepareComposeDirectory(root:string,container:string) {
  root=root.replace(/\/$/,'');
  return `(
set -e
cd ${q(root)}\n# Vorhandene Compose-Datei und Verzeichnisse vor Änderungen sichern.\nsudo install -d -m 755 ${q(root+'/config')} ${q(root+'/config/certs')} ${q(root+'/config/vhosts')}\n# Vorhandene Dateien behalten; abweichende Kopien zuerst vergleichen.\nfor file in haproxy.cfg dnsmapping.cfg; do\n  if [ -f "$file" ]; then\n    if [ -e "config/$file" ]; then sudo cmp -- "$file" "config/$file" || exit 1;\n    else sudo cp -p -- "$file" "config/$file"; fi\n  fi\ndone\n# UID/GID des laufenden HAProxy-Containers verwenden.\nhaproxy_uid=$(sudo docker exec ${q(container)} id -u)\nhaproxy_gid=$(sudo docker exec ${q(container)} id -g)\nsudo install -d -m 750 -o "$haproxy_uid" -g "$haproxy_gid" ${q(root+'/run')}
)`;
}

export function recreateComposeCommands(value:DockerPaths,container:string) {
  return `(
set -e
# Compose-Dateinamen und Service-Namen ggf. anpassen.\nsudo docker compose -f docker-compose.yml config --quiet\nsudo docker exec ${q(container)} haproxy -c -f ${q(value.container_config_dir+'/haproxy.cfg')} -f ${q(value.container_config_dir+'/vhosts')}\nsudo docker compose -f docker-compose.yml up -d --force-recreate haproxy
)`;
}

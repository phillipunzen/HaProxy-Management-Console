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
export function prepareComposeErrors(container:string) {
  return `# Der Verzeichnis-Mount verdeckt die Fehlerseiten aus dem Image.
# Exakt das bereits verwendete Image ohne dessen Bind-Mounts auslesen.
haproxy_errors_tmp=$(mktemp -d)
haproxy_errors_container=''
trap 'if [ -n "$haproxy_errors_container" ]; then sudo docker rm "$haproxy_errors_container" >/dev/null; fi; sudo rm -rf -- "$haproxy_errors_tmp"' EXIT
haproxy_errors_image=$(sudo docker inspect --format '{{.Image}}' ${q(container)})
haproxy_errors_container=$(sudo docker create "$haproxy_errors_image")
sudo docker cp "$haproxy_errors_container:/usr/local/etc/haproxy/errors/." "$haproxy_errors_tmp/"
sudo install -d -m 755 config/errors
# Eigene Fehlerseiten bevorzugen; bereits vorhandene Ziel-Dateien behalten.
for haproxy_errors_source in errors "$haproxy_errors_tmp"; do
  [ -d "$haproxy_errors_source" ] || continue
  for haproxy_error_file in "$haproxy_errors_source"/*; do
    [ -f "$haproxy_error_file" ] || continue
    haproxy_error_name=$(basename "$haproxy_error_file")
    if [ ! -e "config/errors/$haproxy_error_name" ] && [ ! -L "config/errors/$haproxy_error_name" ]; then
      sudo cp -p -- "$haproxy_error_file" "config/errors/$haproxy_error_name"
    fi
  done
done`;
}
export function prepareComposeDirectory(root:string,container:string) {
  root=root.replace(/\/$/,'');
  return `(
set -e
cd ${q(root)}
# Vorhandene Compose-Datei und Verzeichnisse vor Änderungen sichern.
sudo install -d -m 755 ${q(root+'/config')} ${q(root+'/config/certs')} ${q(root+'/config/vhosts')}
# Vorhandene Dateien behalten; abweichende Kopien zuerst vergleichen.
for file in haproxy.cfg dnsmapping.cfg; do
  if [ -f "$file" ]; then
    if [ -e "config/$file" ]; then sudo cmp -- "$file" "config/$file" || exit 1;
    else sudo cp -p -- "$file" "config/$file"; fi
  fi
done
${prepareComposeErrors(container)}
# UID/GID des laufenden HAProxy-Containers verwenden.
haproxy_uid=$(sudo docker exec ${q(container)} id -u)
haproxy_gid=$(sudo docker exec ${q(container)} id -g)
sudo install -d -m 750 -o "$haproxy_uid" -g "$haproxy_gid" ${q(root+'/run')}
)`;
}

export function recreateComposeCommands(value:DockerPaths,_container:string) {
  const composeRoot=parent(parent(value.config_path));
  return `(
set -e
cd ${q(composeRoot)}
# Compose-Dateinamen und Service-Namen ggf. anpassen.
sudo docker compose -f docker-compose.yml config --quiet
# Neue Mounts in einem Prüfcontainer testen, bevor der laufende Proxy ersetzt wird.
sudo docker compose -f docker-compose.yml run --rm --no-deps haproxy haproxy -c -f ${q(value.container_config_dir+'/haproxy.cfg')} -f ${q(value.container_config_dir+'/vhosts')}
sudo docker compose -f docker-compose.yml up -d --force-recreate haproxy
)`;
}

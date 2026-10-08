"""Read the selected HAProxy process's loaded files and referenced maps."""
import json
from pathlib import Path

from fastapi import HTTPException
from backend.haproxy_config import map_references, migration_context


def read_bundle(p,run,sha):
    mounts=[];warnings=[]
    if p['kind']=='docker':
        mounts=json.loads(run(['docker','inspect','--format','{{json .Mounts}}',p['container']]))
    def host_path(value):
        path=Path(value)
        if not path.is_absolute() or '..' in path.parts:raise HTTPException(422,'Nur absolute HAProxy-Dateipfade werden unterstützt.')
        if p['kind']!='docker':return path
        for mount in sorted(mounts,key=lambda m:len(m['Destination']),reverse=True):
            try:return Path(mount['Source'])/path.relative_to(mount['Destination'])
            except ValueError:pass
        raise HTTPException(422,f'Datei {value} liegt außerhalb der Docker-Mounts.')
    def container_path(value):
        path=Path(value)
        if p['kind']!='docker':return str(path)
        for mount in sorted(mounts,key=lambda m:len(m['Source']),reverse=True):
            try:return str(Path(mount['Destination'])/path.relative_to(mount['Source']))
            except ValueError:pass
        raise HTTPException(422,'Map liegt außerhalb der Docker-Mounts.')
    primary=Path(p['config_path']);paths=[]
    if p.get('config_sources'):
        paths=[Path(value) for value in p['config_sources']]
    else:
        try:
            if p['kind']=='docker':
                command=json.loads(run(['docker','inspect','--format','{{json .Config.Cmd}}',p['container']])) or []
            else:
                pid=run(['systemctl','show','--property=MainPID','--value',p.get('service','haproxy')])
                if not pid.isdigit() or pid=='0':raise ValueError('Kein laufender HAProxy-Master')
                command=Path('/proc',pid,'cmdline').read_bytes().decode().split('\x00')
            for index,arg in enumerate(command):
                if arg=='--':
                    paths.extend(host_path(value) for value in command[index+1:] if value);break
                if arg=='-f' and index+1<len(command):paths.append(host_path(command[index+1]))
                elif arg.startswith('-f') and len(arg)>2:paths.append(host_path(arg[2:]))
            if paths:p['_detected_sources']=[str(path) for path in paths]
        except (OSError,ValueError,HTTPException):
            warnings.append('Geladene Dateiliste nicht automatisch erkannt. config_sources im Agent-Profil bei mehreren Dateien explizit setzen.')
            paths=[Path(value) for value in p.get('_detected_sources',[])]
    files=[]
    for path in paths or [primary]:
        if path.is_dir():files.extend(sorted(f for f in path.glob('*.cfg') if not f.name.startswith('.')))
        else:files.append(path)
    files=list(dict.fromkeys(files))
    if primary not in files:raise HTTPException(422,'config_path gehört nicht zu den geladenen Dateien. Agent-Profil korrigieren.')
    if len(files)>200:raise HTTPException(422,'Maximal 200 Konfigurationsdateien unterstützt.')
    sources=[]
    for path in files:
        if not path.is_file():raise HTTPException(422,f'HAProxy-Datei nicht vorhanden: {path}')
        content=path.read_bytes().decode()
        if len(content)>1024*1024:raise HTTPException(422,'Konfigurationsdatei zu groß.')
        sources.append({'path':str(path),'container_path':container_path(path),'content':content,'hash':sha(content)})
    config=''.join(source['content'].rstrip('\r\n')+'\n\n' for source in sources) if len(sources)>1 else sources[0]['content']
    if len(config)>1024*1024:raise HTTPException(422,'Zusammengeführte Konfiguration zu groß.')
    map_paths={value:host_path(value) for value in map_references(config)}
    try:context=migration_context(primary.read_bytes().decode())
    except ValueError:context=None
    if context:
        for item in context['maps']:
            path=Path(item['path'])
            roots=[Path(m['Source']) for m in mounts if Path(m['Source']).is_dir()] if mounts else [f.parent for f in files]+[Path(v) for v in p.get('map_dirs',[])]
            if not any(path.is_relative_to(root) for root in roots):
                raise HTTPException(422,'Map-Metadaten liegen außerhalb der Konfigurationsverzeichnisse.')
            map_paths.setdefault(container_path(path),path)
    maps=[]
    if len(map_paths)>100:raise HTTPException(422,'Zu viele Map-Dateien.')
    for reference,path in map_paths.items():
        if not mounts and not any(path.is_relative_to(root) for root in [f.parent for f in files]+[Path(v) for v in p.get('map_dirs',[])]):
            warnings.append(f'Map {reference} liegt außerhalb der Konfigurationsverzeichnisse. map_dirs im Agent-Profil ergänzen; Zuordnung bleibt im Texteditor.');continue
        if not path.is_file():
            warnings.append(f'Map-Datei fehlt: {reference}');continue
        if path.stat().st_size>256*1024:raise HTTPException(422,'Map-Datei zu groß.')
        content=path.read_bytes().decode()
        maps.append({'path':reference,'host_path':str(path),'content':content,'hash':sha(content)})
    return {'config':config,'hash':sha(primary.read_bytes().decode()),'sources':sources,'maps':maps,'warnings':warnings,'complete':bool(paths)}

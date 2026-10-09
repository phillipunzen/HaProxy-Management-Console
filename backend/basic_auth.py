"""Central Basic Auth directory and narrowly scoped, tagged HAProxy rules."""
import base64
import ctypes
import ctypes.util
import hashlib
import json
import re
import secrets
import shlex
import threading

from sqlalchemy import select
from backend.haproxy_config import parse_sections,map_routes
from backend.schemas import DOMAIN,Host

PREFIX='# haproxy-control-basic-auth '
META=PREFIX+'v1 '
crypt_lock=threading.Lock()


def lock_directory(db):
    from backend.db import BasicAuthDirectory
    # Authentication has already opened a repeatable-read snapshot. End that
    # read-only transaction before waiting for the directory mutex: otherwise
    # MariaDB can reject a newly inserted secondary-index row with error 1020.
    # Callers must acquire this mutex before making any persistent changes.
    if db.new or db.dirty or db.deleted:raise ValueError('Basic-Auth-Sperre vor Änderungen anfordern.')
    db.rollback()
    state=db.scalar(select(BasicAuthDirectory).where(BasicAuthDirectory.id==1).with_for_update().execution_options(populate_existing=True))
    if state is None:
        state=BasicAuthDirectory(id=1);db.add(state);db.flush()
    return state


def hash_password(password):
    # Python 3.13 removed crypt. Use the same system libcrypt as HAProxy without
    # invoking a shell or placing a password in argv. crypt() is serialized.
    library=ctypes.util.find_library('crypt')
    if not library:raise ValueError('Systembibliothek libcrypt für Basic Auth fehlt.')
    salt='$6$rounds=50000$'+''.join(secrets.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789./') for _ in range(16))+'$'
    with crypt_lock:
        crypt=ctypes.CDLL(library).crypt;crypt.argtypes=[ctypes.c_char_p,ctypes.c_char_p];crypt.restype=ctypes.c_char_p
        result=crypt(password.encode(),salt.encode())
        if not result or not result.startswith(b'$6$'):raise ValueError('Basic-Auth-Passwort konnte nicht gehasht werden.')
        return result.decode()


def bindings(doc):
    result=[{'kind':'host','frontend':h.frontend,'backend':'backend_'+h.id,'domain':name,'path':h.path,'group':h.basic_auth_group,'forward':h.basic_auth_forward} for h in doc.hosts if h.enabled and h.basic_auth_group for name in h.hostnames]
    result += [{'kind':'route','domain':name,'path':'/','frontend':r.frontend,'backend':r.backend,'group':r.basic_auth_group,'forward':r.basic_auth_forward,**({'replace_existing':True} if r.basic_auth_replace_existing else {})} for r in doc.imported_routes if r.basic_auth_group for name in r.hostnames]
    if doc.imported_config:
        old=read_metadata(doc.imported_config)
        if old:
            _,editable,_=map_routes(doc.imported_config,[m.model_dump() for m in doc.imported_maps])
            editable={(r.frontend,name) for r in editable for name in r.hostnames}
            # Uneditable pre-existing managed sites remain protected, including
            # native path hosts that the conservative import cannot yet edit.
            result += [s for s in old['sites'] if (s['kind']!='route' or (s['frontend'],s['domain']) not in editable) and not any(h.frontend==s['frontend'] and s['domain'] in h.hostnames and h.path==s['path'] for h in doc.hosts)]
    return result


def ids(doc):return {s['group'] for s in bindings(doc)}


def snapshots(db,group_ids):
    from backend.db import BasicAuthGroup,BasicAuthUser,BasicAuthMembership
    groups={g.id:{'id':g.id,'realm':g.realm,'users':[]} for g in db.scalars(select(BasicAuthGroup).where(BasicAuthGroup.id.in_(group_ids)))}
    if set(groups)!=set(group_ids):raise ValueError('Eine zugewiesene Basic-Auth-Gruppe fehlt. Entwurf und Gruppen prüfen.')
    query=select(BasicAuthMembership.group_id,BasicAuthUser).join(BasicAuthUser,BasicAuthUser.id==BasicAuthMembership.user_id).where(BasicAuthMembership.group_id.in_(group_ids),BasicAuthUser.enabled.is_(True)).order_by(BasicAuthUser.username)
    for group_id,user in db.execute(query):groups[group_id]['users'].append({'username':user.username,'hash':user.password_hash})
    return groups


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def metadata(doc,groups):
    sites=bindings(doc)
    return {'version':1,'groups':{str(id):digest(groups[id]) for id in sorted(ids(doc))},'bindings':digest(sites),'sites':sites}


def read_metadata(config):
    values=[line[len(META):] for line in config.splitlines() if line.startswith(META)]
    if not values:return None
    if len(values)!=1:raise ValueError('Mehrdeutige Basic-Auth-Metadaten. Konfiguration erneut erzeugen.')
    try:
        data=json.loads(base64.b64decode(values[0],validate=True))
        if set(data)!={'version','groups','bindings','sites'} or data['version']!=1 or not isinstance(data['groups'],dict) or not re.fullmatch(r'[a-f0-9]{64}',data['bindings']):raise ValueError()
        if any(not re.fullmatch(r'[1-9][0-9]*',key) or not re.fullmatch(r'[a-f0-9]{64}',value) for key,value in data['groups'].items()):raise ValueError()
        if not isinstance(data['sites'],list) or len(data['sites'])>2200 or digest(data['sites'])!=data['bindings']:raise ValueError()
        for site in data['sites']:
            if set(site)-{'replace_existing'}!={'kind','frontend','backend','domain','path','group','forward'} or site['kind'] not in ('host','route'):raise ValueError()
            if 'replace_existing' in site and (site['kind']!='route' or site['replace_existing'] is not True):raise ValueError()
            if not isinstance(site['group'],int) or isinstance(site['group'],bool) or site['group']<1 or not isinstance(site['forward'],bool):raise ValueError()
            if not DOMAIN.fullmatch(site['domain']) or any(not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',site[k]) for k in ('frontend','backend')):raise ValueError()
            Host.path_ok(site['path'])
        if {str(s['group']) for s in data['sites']}!=set(data['groups']):raise ValueError()
        return data
    except Exception as error:raise ValueError('Ungültige Basic-Auth-Metadaten. Konfiguration erneut erzeugen.') from error


def assert_current(db,config):
    data=read_metadata(config)
    if not data:return None
    groups=snapshots(db,{int(id) for id in data['groups']})
    if any(digest(groups[int(id)])!=value for id,value in data['groups'].items()):raise ValueError('Zentrale Basic-Auth-Zugänge wurden geändert. Konfiguration neu erzeugen und prüfen.')
    return data


def strip_managed(config):
    result=[];block=None
    for line in config.splitlines(keepends=True):
        text=line.strip()
        if text.startswith(PREFIX+'BEGIN '):
            if block:raise ValueError('Verschachtelte Basic-Auth-Blöcke. Konfiguration im Texteditor prüfen.')
            block=text[len(PREFIX+'BEGIN '):]
            # Legacy challenges are retained verbatim inside reversible blocks.
            # Restore them before regeneration, including when a group is removed.
            if block.startswith('legacy '):
                try:
                    original=base64.b64decode(block[7:],validate=True).decode()
                    if '\n' in original.rstrip('\r\n') or '\r' in original.rstrip('\r\n') or shlex.split(original,comments=True)[:2]!=['http-request','auth']:raise ValueError()
                except Exception as error:raise ValueError('Ungültige ursprüngliche Basic-Auth-Regel. Konfiguration erneut einlesen.') from error
                result.append(original)
            continue
        if text.startswith(PREFIX+'END '):
            if not block or text[len(PREFIX+'END '):]!=block:raise ValueError('Unvollständiger Basic-Auth-Block. Konfiguration im Texteditor prüfen.')
            block=None;continue
        if not block and not line.startswith(META):result.append(line)
    if block:raise ValueError('Unvollständiger Basic-Auth-Block. Konfiguration im Texteditor prüfen.')
    return ''.join(result)


def existing_rules(config):
    lines,sections=parse_sections(strip_managed(config or ''))
    return [{'kind':s.kind,'name':s.name,'rules':[lines[i].strip() for i,t in s.lines if t[:2]==['http-request','auth']]} for s in sections if s.kind in ('frontend','backend','listen') and any(t[:2]==['http-request','auth'] for _,t in s.lines)]


def legacy_challenge(line,skip,index):
    # Keep the original if/unless expression intact, including OR expressions.
    # Evaluate it at its original position and add the site gate separately.
    match=re.match(r'''^(\s*http-request\s+auth(?:\s+realm\s+(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s#]+))?)(?:\s+(if|unless)\s+(.+))?\s*(?:#.*)?$''',line.rstrip('\r\n'))
    if not match:raise ValueError('Die vorhandene Authentifizierungsregel kann nicht sicher umgestellt werden. Regel in der importierten Konfiguration prüfen und erneut einlesen.')
    action,operator,condition=match.groups();variable='txn.mgmt_legacy_'+hashlib.sha256((skip+str(index)).encode()).hexdigest()[:16]
    original=base64.b64encode(line.encode()).decode();block='legacy '+original
    return '\n'.join([PREFIX+'BEGIN '+block,
        '    http-request set-var('+variable+') bool(false)',
        '    http-request set-var('+variable+') bool(true)'+(' '+operator+' '+condition if operator else ''),
        action+' if { var('+variable+') -m bool } !{ var('+skip+') -m bool }',
        PREFIX+'END '+block])+'\n'


def inject(config,doc,groups=None):
    config=strip_managed(config);required=ids(doc)
    if not required:return config
    if groups is None or not required.issubset(groups):raise ValueError('Basic-Auth-Gruppen vor dem Erzeugen auflösen.')
    lines,sections=parse_sections(config);targets={s.name:s for s in sections if s.kind in ('backend','listen')}
    fronts={s.name:s for s in sections if s.kind in ('frontend','listen')};rules={}
    for site in bindings(doc):
        if site['frontend'] not in fronts or fronts[site['frontend']].mode!='http':raise ValueError('Basic Auth benötigt ein HTTP-Frontend; TCP-Passthrough kann nicht geschützt werden.')
        if any(t[:2]==['http-request','auth'] for _,t in fronts[site['frontend']].lines):raise ValueError('Im Frontend '+site['frontend']+' besteht bereits eine eigene Authentifizierungsregel. Diese in der ursprünglichen Konfiguration abstimmen und erneut einlesen; die Umstellung im Domain-Dialog gilt für Backend-Regeln.')
        condition=f"{{ fe_name -m str {site['frontend']} }} {{ hdr(host) -i {site['domain']} }}" if site['kind']=='route' else ''
        entry=(site['group'],condition,site['forward'],site.get('replace_existing',False))
        entries=rules.setdefault(site['backend'],[])
        if entry not in entries:entries.append(entry)
    userlists={s.name for s in sections if s.kind=='userlist'}
    output=[PREFIX+'BEGIN users']
    for id in sorted(required):
        name='mgmt_basic_g'+str(id);group=groups[id]
        if name in userlists:raise ValueError('Userlist-Namenskonflikt: '+name+'. Bestehende Userlist im Texteditor umbenennen.')
        if not group['users']:continue
        output += ['userlist '+name]+[f"    user {u['username']} password '{u['hash']}'" for u in group['users']]
    output.append(PREFIX+'END users')
    patch={}
    for name,entries in rules.items():
        section=targets.get(name)
        if not section or section.mode!='http':raise ValueError('Basic Auth benötigt einen vorhandenen HTTP-Backend-Pool.')
        legacy=[i for i,t in section.lines if t[:2]==['http-request','auth']]
        if legacy and not all(replace for _,_,_,replace in entries):raise ValueError('Im Backend '+name+' besteht bereits eine eigene Authentifizierungsregel. Unter Proxy Hosts die betroffenen Domain-Zuordnungen bearbeiten, eine zentrale Gruppe wählen und „Vorhandene Backend-Anmeldung für diese Domain ersetzen“ bestätigen.')
        # Terminating HTTP actions inherited from defaults execute before local
        # rules; do not claim protection when they could bypass the challenge.
        inherited=[s for s in sections if s.kind=='defaults' and s.start<section.start]
        tokens=shlex.split(lines[section.start],comments=True)
        selected_default=next((s for s in sections if s.kind=='defaults' and 'from' in tokens and s.name==tokens[tokens.index('from')+1]),None) if 'from' in tokens else inherited[-1] if inherited else None
        checked=set()
        while selected_default and selected_default.name not in checked:
            checked.add(selected_default.name)
            conditional=any(condition for _,condition,_,_ in entries)
            if any(t[0]=='http-request' and (conditional or t[1:2] in (['allow'],['return'])) for _,t in selected_default.lines):
                raise ValueError('HTTP-Regeln in den geerbten defaults können lokale Authentifizierung umgehen. Diese Regeln zuerst in den jeweiligen Proxy-Abschnitt verschieben.')
            header=shlex.split(lines[selected_default.start],comments=True)
            selected_default=next((s for s in sections if s.kind=='defaults' and s.name==header[header.index('from')+1]),None) if 'from' in header else None
        block=[PREFIX+'BEGIN backend '+name]
        if legacy:
            skip='txn.mgmt_skip_'+hashlib.sha256(name.encode()).hexdigest()[:16]
            if re.search(r'\btxn\.mgmt_(?:skip|legacy)_',config):raise ValueError('Namenskonflikt mit internen Basic-Auth-Variablen. Ursprüngliche Konfiguration prüfen.')
            block.append('    http-request set-var('+skip+') bool(false)')
            for _,condition,_,_ in entries:block.append('    http-request set-var('+skip+') bool(true)'+(' if '+condition if condition else ''))
            for index in legacy:patch[index]=legacy_challenge(lines[index],skip,index)
        for id,condition,forward,_ in entries:
            group=groups[id];checks=condition
            if group['users']:checks+=' !{ http_auth(mgmt_basic_g'+str(id)+') }'
            block.append(f"    http-request auth realm '{group['realm']}'"+(' if '+checks.strip() if checks.strip() else ''))
            if not forward:block.append('    http-request del-header Authorization'+(' if '+condition if condition else ''))
        block.append(PREFIX+'END backend '+name)
        ending='\r\n' if lines[section.start].endswith('\r\n') else '\n'
        patch[section.start]=lines[section.start].rstrip('\r\n')+ending+ending.join(block)+ending
    result='\n'.join(output)+'\n'+''.join(patch.get(index,line) for index,line in enumerate(lines))
    return result.rstrip('\r\n')+'\n'+META+base64.b64encode(json.dumps(metadata(doc,groups),sort_keys=True,separators=(',',':')).encode()).decode()+'\n'


def assignments(db,lock=False):
    from backend.db import Instance
    from backend.schemas import Document
    result=[]
    query=select(Instance).with_for_update().execution_options(populate_existing=True) if lock else select(Instance)
    for i in db.scalars(query):
        for key in ('hosts','imported_routes'):
            for h in i.document.get(key,[]):
                if h.get('basic_auth_group'):
                    result += [{'group_id':h['basic_auth_group'],'instance_id':i.id,'instance_name':i.name,'domain':name,'path':h.get('path','/'),'enabled':h.get('enabled',True)} for name in [h['domain']]+h.get('aliases',[])]
        try:
            doc=Document.model_validate(i.document)
            for s in bindings(doc):
                if not any(r['instance_id']==i.id and r['domain']==s['domain'] and r['group_id']==s['group'] for r in result):result.append({'group_id':s['group'],'instance_id':i.id,'instance_name':i.name,'domain':s['domain'],'path':s['path'],'enabled':True})
        except ValueError:pass
    return result


def validate_document(db,doc):
    from backend.db import BasicAuthGroup
    required=ids(doc)|{h.basic_auth_group for h in doc.hosts if h.basic_auth_group}
    actual=set(db.scalars(select(BasicAuthGroup.id).where(BasicAuthGroup.id.in_(required)).with_for_update()))
    if required!=actual:raise ValueError('Eine zugewiesene Basic-Auth-Gruppe existiert nicht.')
    if doc.imported_config and required:
        _,sections=parse_sections(doc.imported_config)
        fronts={s.name:s.mode for s in sections if s.kind in ('frontend','listen')}
        backs={s.name:s.mode for s in sections if s.kind in ('backend','listen')}
        fronts.update({f.name:f.mode for f in doc.frontends})
        backs.update({b.name:b.mode for b in doc.backends})
        backs.update({'backend_'+h.id:'http' for h in doc.hosts if h.enabled})
        if any(fronts.get(s['frontend'])!='http' or backs.get(s['backend'])!='http' for s in bindings(doc)):raise ValueError('Basic Auth ist nur für HTTP-Sites verfügbar, nicht für TCP-Passthrough.')


def restore_routes(doc):
    old=read_metadata(doc.imported_config or '')
    if old:
        for route in doc.imported_routes:
            match=next((s for s in old['sites'] if s['kind']=='route' and s['frontend']==route.frontend and s['domain']==route.domain and s['backend']==route.backend),None)
            if match:route.basic_auth_group=match['group'];route.basic_auth_forward=match['forward'];route.basic_auth_replace_existing=match.get('replace_existing',False)
    return doc

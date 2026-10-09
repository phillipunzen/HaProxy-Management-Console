"""Remove pools in the draft, retaining the imported baseline and its file hashes."""
import re

from backend.haproxy_config import parse_sections, map_routes, remove_original_tool_hosts
from backend.schemas import Document


def dependencies(config,names,maps=()):
    """Static routing can be removed. Opaque expressions must be resolved first."""
    from backend.basic_auth import strip_managed
    # Managed authentication is rebuilt from the remaining sites, so its old
    # fe_name conditions are not external references to a deleted listen block.
    config=strip_managed(config)
    _,sections=parse_sections(config)
    rules=map_routes(config,maps)[0]
    managed={r['index'] for r in rules}
    result=[f"{r['frontend']}: Map-Fallback {r['default']}" for r in rules if r['default'] in names and r['frontend'] not in names]
    for section in sections:
        if section.kind in ('backend','listen') and section.name in names:continue
        for index,tokens in section.lines:
            if index in managed:continue
            if tokens[0] in ('default_backend','use_backend') and len(tokens)>1:
                if tokens[1] in names:
                    result.append(f'{section.name or section.kind}: {tokens[0]} {tokens[1]}');continue
                if tokens[0]=='use_backend' and '%[' in tokens[1]:
                    raise ValueError(f'{section.name}: dynamische Backend-Auswahl ist nicht vollständig auflösbar. Diese Regel zuerst im Texteditor anpassen oder als Host-Map importieren.')
            # Includes backend sample fetches, server tracking and Lua references.
            # Fail closed rather than rewriting expressions/conditions blindly.
            text=' '.join(tokens)
            if any(re.search(r'(?<![a-zA-Z0-9_.-])'+re.escape(name)+r'(?![a-zA-Z0-9_.-])',text) for name in names):
                raise ValueError(f'{section.name or section.kind}: weitere Regel verweist auf den zu löschenden Pool. Diese Regel zuerst im Texteditor anpassen.')
    return list(dict.fromkeys(result))


def remove_sections(config,names):
    if not names:return config
    from backend import tls_bindings
    # Routing maps have already been expanded by generate_imported.
    dependencies(config,names)
    lines,sections=parse_sections(config);removed=set();fronts=set()
    for section in sections:
        if section.kind in ('backend','listen') and section.name in names:
            removed.update(range(section.start,section.end))
            if section.kind=='listen':fronts.add(section.name)
        else:
            removed.update(i for i,t in section.lines if t[0] in ('default_backend','use_backend') and len(t)>1 and t[1] in names)
    plans=tls_bindings.read(config)
    if plans:
        removed.update(i for i,line in enumerate(lines) if line.startswith(tls_bindings.PREFIX))
    result=''.join(line for i,line in enumerate(lines) if i not in removed)
    return tls_bindings.append_metadata(result,[p for p in plans if p['frontend'] not in fronts])


def plan(doc,name):
    original=remove_original_tool_hosts(doc.imported_config or '',doc.imported_managed_hosts,[m.model_dump() for m in doc.imported_maps])
    raw=[s for s in parse_sections(original)[1] if s.kind in ('backend','listen') and s.name==name and name not in doc.removed_backends]
    if len(raw)>1:raise ValueError('Mehrere gleichnamige Pools: Namen zuerst im Texteditor bereinigen.')
    own=next((b for b in doc.backends if b.name==name),None)
    host=next((h for h in doc.hosts if 'backend_'+h.id==name),None)
    if not raw and not own and not host:raise ValueError('Backend-Pool wurde nicht gefunden oder wird automatisch als System-Pool verwaltet.')
    refs=dependencies(original,{name},[m.model_dump() for m in doc.imported_maps]) if original else []
    listen=bool(raw and raw[0].kind=='listen')
    fronts={name} if listen else set()
    fronts.update(f.name for f in doc.frontends if f.backend==name and f.mode=='tcp')
    routes=[r for r in doc.imported_routes if r.backend==name or r.frontend in fronts]
    hosts=[h for h in doc.hosts if 'backend_'+h.id==name or h.frontend in fronts]
    for f in doc.frontends:
        if f.backend==name:refs.append(f'{f.name}: default_backend {name}')
    value=doc.model_dump()
    value['hosts']=[h.model_dump() for h in doc.hosts if h not in hosts]
    value['backends']=[b.model_dump() for b in doc.backends if b.name!=name]
    value['frontends']=[f.model_dump()|({'backend':None} if f.backend==name else {}) for f in doc.frontends if f.name not in fronts]
    value['imported_backends']=[b.model_dump() for b in doc.imported_backends if b.name!=name]
    value['imported_routes']=[r.model_dump() for r in doc.imported_routes if r not in routes]
    value['imported_route_frontends']=[f for f in doc.imported_route_frontends if f not in fronts]
    value['frontend_certificates']={f:certs for f,certs in doc.frontend_certificates.items() if f not in fronts}
    if original:value['removed_backends']=list(dict.fromkeys(doc.removed_backends+[name]))
    changed=Document.model_validate(value)
    if changed.imported_config:
        from backend.haproxy_config import generate_imported
        generate_imported(changed)  # Validate all pending removals before persisting.
    return changed,{'name':name,'domains':list(dict.fromkeys(n for item in hosts+routes for n in item.hostnames)),
                    'listeners':sorted(fronts),'references':list(dict.fromkeys(refs))}

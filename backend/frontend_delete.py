"""Delete draft listeners while retaining imported files and shared backend pools."""
import re

from backend.haproxy_config import parse_sections,remove_original_tool_hosts
from backend.schemas import Document


def require_listener(config):
    if not any(s.kind in ('frontend','listen') for s in parse_sections(config)[1]):
        raise ValueError('Kein Frontend im Entwurf vorhanden. Vor Prüfen & anwenden ein neues Frontend anlegen; zum Abschalten des Dienstes die Dienststeuerung verwenden.')


def dependencies(config,names):
    """Require explicit editing of references to another listener's identity."""
    _,sections=parse_sections(config)
    listens={s.name for s in sections if s.kind=='listen' and s.name in names}
    for section in sections:
        if section.kind in ('frontend','listen') and section.name in names:continue
        for _,tokens in section.lines:
            # Combined listen blocks also act as backends. The backend deletion
            # workflow removes these static routes and rejects opaque ones.
            if tokens[0] in ('default_backend','use_backend') and len(tokens)>1 and tokens[1] in listens:continue
            text=' '.join(tokens)
            if any(re.search(r'(?<![a-zA-Z0-9_.-])'+re.escape(name)+r'(?![a-zA-Z0-9_.-])',text) for name in names):
                raise ValueError(f'{section.name or section.kind}: eine weitere Regel verweist auf das zu löschende Frontend. Diese Regel zuerst im Texteditor anpassen.')


def clean(config):
    from backend.access_control import strip_managed as strip_access
    from backend.basic_auth import strip_managed as strip_auth
    from backend.managed_rules import strip as strip_rules
    return strip_rules(strip_access(strip_auth(config)))


def remove_sections(config,names):
    if not names:return config
    from backend import tls_bindings
    config=clean(config)
    dependencies(config,set(names))
    lines,sections=parse_sections(config);removed=set()
    for section in sections:
        if section.kind in ('frontend','listen') and section.name in names:
            removed.update(range(section.start,section.end))
    plans=tls_bindings.read(config)
    result=''.join(line for i,line in enumerate(lines) if i not in removed and not line.startswith(tls_bindings.PREFIX))
    return tls_bindings.append_metadata(result,[p for p in plans if p['frontend'] not in names])


def plan(doc,name):
    original=remove_original_tool_hosts(clean(doc.imported_config or ''),doc.imported_managed_hosts,[m.model_dump() for m in doc.imported_maps])
    original=remove_sections(original,doc.removed_frontends)
    raw=[s for s in parse_sections(original)[1] if s.kind in ('frontend','listen') and s.name==name and not (s.kind=='listen' and name in doc.removed_backends)]
    own=next((f for f in doc.frontends if f.name==name),None)
    shared=doc.imported_config is None and name=='public_http' and name not in doc.removed_frontends
    if len(raw)>1 or raw and own:raise ValueError('Mehrere gleichnamige Frontends: Namen zuerst im Texteditor bereinigen.')
    if not raw and not own and not shared:raise ValueError('Frontend wurde nicht gefunden oder bereits gelöscht.')
    dependencies(original,{name})
    references=[]
    # A listen block is both listener and pool; existing backend safeguards
    # handle its incoming static/dynamic references and shared metadata.
    if raw and raw[0].kind=='listen':
        from backend.backend_delete import plan as delete_pool
        changed,summary=delete_pool(doc,name);references=summary['references']
    else:changed=doc
    hosts=[h for h in doc.hosts if h.frontend==name]
    routes=[r for r in doc.imported_routes if r.frontend==name]
    rules=[r for r in doc.rules if (r.frontend or 'public_http')==name or r not in changed.rules]
    value=changed.model_dump()
    value['hosts']=[h.model_dump() for h in changed.hosts if h.frontend!=name]
    value['rules']=[r.model_dump() for r in changed.rules if (r.frontend or 'public_http')!=name]
    value['frontends']=[f.model_dump() for f in changed.frontends if f.name!=name]
    value['imported_routes']=[r.model_dump() for r in changed.imported_routes if r.frontend!=name]
    value['imported_route_frontends']=[f for f in changed.imported_route_frontends if f!=name]
    value['frontend_certificates']={f:certs for f,certs in changed.frontend_certificates.items() if f!=name}
    if raw or shared:value['removed_frontends']=list(dict.fromkeys(changed.removed_frontends+[name]))
    if shared:value.update(tls_enabled=False,acme_enabled=False)
    changed=Document.model_validate(value)
    if changed.imported_config:
        from backend.haproxy_config import generate_imported
        generate_imported(changed)
    remaining={s.name for s in parse_sections(original)[1] if s.kind in ('frontend','listen') and s.name not in set(changed.removed_frontends)|set(changed.removed_backends)}
    remaining.update(f.name for f in changed.frontends)
    if changed.imported_config is None and 'public_http' not in changed.removed_frontends:remaining.add('public_http')
    domains=[n for item in hosts+routes for n in item.hostnames]
    if raw and raw[0].kind=='listen':domains+=summary['domains']
    return changed,{'name':name,'kind':raw[0].kind if raw else 'frontend','domains':list(dict.fromkeys(domains)),
                    'remaining_listeners':sorted(remaining),'rules':[r.name for r in rules],'listeners':list(dict.fromkeys([name]+[f.name for f in doc.frontends if f not in changed.frontends])), 'references':references,'backend_removed':bool(raw and raw[0].kind=='listen')}

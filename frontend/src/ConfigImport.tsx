import {AccessPolicyEditor,type AccessPolicy} from './AccessPolicy';
import {ProxyOptions} from './ProxyOptions';
import {ProxyHealthCell,type HealthView} from './ProxyHealth';
import {BackendTLSOptions} from './BackendTLSOptions';
import {HostnameAliases,HostnameList,hostnames} from './Hostnames';
import { CertificateSelector, type Certificate } from './Certificates';
import { useEffect, useRef, useState } from 'react';
import { BasicAuthSelector, type BasicGroup } from './BasicAuth';
import { FileCode2, Loader2, Upload, Check, Settings2, Plus, Trash2, Terminal, Copy, RefreshCw } from 'lucide-react';

export type ImportedBackend={proxy_options?:string[]|null;name:string;mode:string;balance:string|null;servers:{name:string;address:string;port:number;weight:number;tls:boolean;tls_verify?:boolean|null}[]};
export type ImportedRoute={access_policy?:AccessPolicy|null;id:string;frontend:string;domain:string;aliases?:string[];backend:string;basic_auth_group?:number|null;basic_auth_forward?:boolean;basic_auth_replace_existing?:boolean;certificate?:string|null};
export type ExistingAuth={kind:string;name:string;rules:string[]};
export type ImportedFields={removed_backends?:string[];imported_config?:string|null;imported_backends?:ImportedBackend[];imported_routes?:ImportedRoute[];imported_route_frontends?:string[];imported_sources?:{path:string;hash:string}[];basic_auth_existing?:ExistingAuth[]};
type Request=(path:string,method?:string,body?:unknown)=>Promise<any>;

export function ConfigImport({id,request,onDone,onBusy,canUpdateAgent=false}:{canUpdateAgent?:boolean;onBusy?:(value:boolean)=>void;id:number;request:Request;onDone:(doc:any)=>void}){
  const [source,setSource]=useState('agent'),[config,setConfig]=useState(''),[extra,setExtra]=useState<{name:string;content:string}[]>([]),[maps,setMaps]=useState<{path:string;content:string}[]>([]);
  const [preview,setPreview]=useState<any>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[needsUpdate,setNeedsUpdate]=useState(false),[updateCommand,setUpdateCommand]=useState(''),[updateError,setUpdateError]=useState('');
  const payload=source==='agent'?{}:{config:config+'\n'+extra.map(f=>f.content).join('\n\n'),maps};
  function report(error:unknown){setError((error as Error).message);setNeedsUpdate((error as Error&{code?:string}).code==='agent_update_required');}
  async function load(){onBusy?.(true);setBusy(true);setError('');setNeedsUpdate(false);try{const value=await request(`/instances/${id}/import-preview`,'POST',payload);setPreview(value);setNeedsUpdate(!!value.agent_update_required);}catch(e){report(e);}finally{setBusy(false);onBusy?.(false);}}
  async function commit(){onBusy?.(true);setBusy(true);setError('');try{onDone(await request(`/instances/${id}/import`,'POST',{...payload,active_hash:preview.active_hash,document_version:preview.document_version,preview_hash:preview.preview_hash}));}catch(e){report(e);}finally{setBusy(false);onBusy?.(false);}}
  async function prepareUpdate(){onBusy?.(true);setBusy(true);setUpdateError('');try{setUpdateCommand((await request(`/instances/${id}/agent-update`,'POST')).command);}catch(e){setUpdateError((e as Error).message);}finally{setBusy(false);onBusy?.(false);}}
  async function copyCommand(){setUpdateError('');try{if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(updateCommand);else{const field=document.getElementById('import-update-command') as HTMLTextAreaElement;field.focus();field.select();if(!document.execCommand('copy'))throw new Error('Bitte den markierten Befehl manuell kopieren.');}}catch(e){setUpdateError((e as Error).message);}}
  async function readFiles(files:FileList|null,kind:'config'|'extra'|'maps'){
    if(!files)return;setError('');setPreview(null);
    try{
      if(Array.from(files).reduce((sum,f)=>sum+f.size,0)>1024*1024)throw new Error('Dateien zusammen höchstens 1 MB groß.');
      const data=await Promise.all(Array.from(files).map(async f=>({name:f.name,content:await f.text()})));
      if(kind==='config')setConfig(data[0]?.content||'');
      if(kind==='extra')setExtra(data.sort((a,b)=>a.name.localeCompare(b.name,'en')));
      if(kind==='maps')setMaps(data.map(f=>({path:'/etc/haproxy/maps/'+f.name,content:f.content})));
    }catch(e){setError((e as Error).message);}
  }
  return <div className="form-body">
    <div className="notice"><FileCode2 size={18}/><span>Importiert in einen grafischen Entwurf. HAProxy bleibt unverändert, bis du eine erzeugte Konfiguration prüfst und anwendest. Vorhandene Einstellungen werden erhalten. <a href="/api/import-guide">Import-Anleitung herunterladen</a></span></div>
    {error&&<div role="alert" className="notice error">{error}</div>}
    {needsUpdate&&<section className="import-agent-update"><h3><Terminal size={18}/>Agent für Datei-Import aktualisieren</h3><p>Den Befehl per SSH auf dem oben angezeigten HAProxy-Zielserver ausführen. Er aktualisiert den Agenten und startet dessen Dienst neu; vorhandene Profile, Tokens und HAProxy-Konfigurationen bleiben erhalten.</p>{canUpdateAgent?<>{!updateCommand?<button type="button" className="button secondary" disabled={busy} onClick={()=>void prepareUpdate()}><Terminal size={16}/>Update-Befehl anzeigen</button>:<><label htmlFor="import-update-command">Befehl auf dem HAProxy-Host</label><textarea id="import-update-command" className="wizard-command" readOnly rows={5} value={updateCommand} onFocus={e=>e.currentTarget.select()}/><button type="button" className="button secondary" disabled={busy} onClick={()=>void copyCommand()}><Copy size={16}/>Befehl kopieren</button></>}</>:<p>Ein Administrator kann hier den Update-Befehl anzeigen oder unter Server den Agenten aktualisieren.</p>}{updateError&&<div role="alert" className="notice error">{updateError}</div>}<button type="button" className="button" disabled={busy} onClick={()=>void load()}><RefreshCw size={16}/>Nach Update erneut einlesen</button></section>}
    {!preview?<>
      <div className="field"><label htmlFor="import-source">Quelle</label><select id="import-source" value={source} disabled={busy} onChange={e=>{setSource(e.target.value);setError('');setNeedsUpdate(false);setPreview(null);}}><option value="agent">Aktive Dateien vom HAProxy-Agenten einlesen</option><option value="files">Konfiguration und Maps hochladen / einfügen</option></select></div>
      {source==='agent'?<p className="wizard-intro">Der Agent erkennt die geladenen -f-Dateien und Verzeichnisse sowie referenzierte Host-Maps. Dafür muss der Agent diese Funktion unterstützen. Unter Server findest du den Befehl zur Aktualisierung.</p>:<>
        <div className="field"><label htmlFor="import-main-file">Hauptkonfiguration (.cfg)</label><input id="import-main-file" type="file" accept=".cfg,.conf,.txt" onChange={e=>void readFiles(e.target.files,'config')}/></div>
        <textarea className="wizard-command" aria-label="Konfiguration für Import" rows={8} placeholder="global … defaults … frontend …" value={config} onChange={e=>setConfig(e.target.value)}/>
        <div className="field"><label htmlFor="import-extra-files">Weitere Konfigurationsdateien (.cfg, alphabetische Reihenfolge)</label><input id="import-extra-files" type="file" multiple accept=".cfg,.conf,.txt" onChange={e=>void readFiles(e.target.files,'extra')}/><small>{extra.map(f=>f.name).join(', ')}</small></div>
        <div className="field"><label htmlFor="import-map-files">Host-Maps (.map)</label><input id="import-map-files" type="file" multiple accept=".map,.txt" onChange={e=>void readFiles(e.target.files,'maps')}/></div>
        {maps.map((map,i)=><div className="field" key={i}><label htmlFor={'map-path-'+i}>Map-Pfad aus der HAProxy-Konfiguration</label><input id={'map-path-'+i} value={map.path} onChange={e=>setMaps(old=>old.map((m,j)=>i===j?{...m,path:e.target.value}:m))}/></div>)}
      </>}
      <div className="modal-actions"><button type="button" className="button" disabled={busy||source==='files'&&!config.trim()} onClick={()=>void load()}>{busy?<Loader2 size={16} className="spin"/>:<Upload size={16}/>}Einlesen & Vorschau</button></div>
    </>:<>
      {(preview.summary.managed_hosts>0||preview.summary.restored_document)&&<div className="notice"><span><strong>{preview.summary.managed_hosts||0} Tool-Proxy-Hosts wiedererkannt.</strong> Diese bleiben reguläre Proxy Hosts mit ihren Hostnamen, Pfaden, Zielservern und zentralen Zuordnungen.{preview.summary.restored_document?' Der gespeicherte grafische Aufbau wird vollständig wiederhergestellt.':''}</span></div>}
      <div className="proxy-summary"><div><strong>{preview.summary.frontends}</strong><span>Frontends</span></div><div><strong>{preview.summary.editable_backends}</strong><span>Bearbeitbare Pools</span></div><div><strong>{preview.summary.routes}</strong><span>Domain-Zuordnungen</span></div></div>
      <p className="wizard-intro">{preview.summary.editable_servers} Zielserver · {preview.summary.tcp} TCP-Abschnitte · {preview.summary.converted_maps} Host-Maps werden in explizite Domain-Routen überführt. Globale Einstellungen, Header, Authentifizierung und übrige Regeln bleiben im Konfigurationstext erhalten.</p>
      {preview.source_files?.length>1&&<div className="notice"><span>Beim späteren Anwenden werden {preview.source_files.length} geladene Dateien in der Hauptkonfiguration zusammengeführt. Die übrigen Dateien bleiben als Kommentar-Dateien vorhanden, damit dieselben Abschnitte nicht doppelt geladen werden. Alle Originaldateien werden auf dem Agenten gesichert.</span></div>}
      <div className="table-wrap"><table><thead><tr><th>ABSCHNITT</th><th>MODUS</th><th>ERKANNT ALS</th></tr></thead><tbody>{preview.proxies.map((p:any,i:number)=><tr key={i}><td><strong>{p.name}</strong><small>{p.kind}</small></td><td><span className="badge">{p.mode.toUpperCase()}</span></td><td>{p.service}</td></tr>)}</tbody></table></div>
      {preview.warnings.length>0&&<div className="notice"><div><strong>Hinweise zur Übernahme</strong><ul>{preview.warnings.map((w:string,i:number)=><li key={i}>{w}</li>)}</ul></div></div>}
      <div className="modal-actions"><button type="button" className="button secondary" disabled={busy} onClick={()=>setPreview(null)}>Zurück</button><button type="button" className="button" disabled={busy||preview.can_import===false||needsUpdate} onClick={()=>void commit()}>{busy?<Loader2 size={16} className="spin"/>:<Check size={16}/>}Als grafischen Entwurf übernehmen</button></div>
    </>}
  </div>;
}

export function ImportedConfigPanel({health,groups,doc,onSave,onImport,onGenerate,onApply,onEditBackend,onEditRoute,onRemoveBackend,busy}:{health:HealthView;groups:BasicGroup[];doc:ImportedFields;onSave:(changes:ImportedFields)=>Promise<void>;onImport:()=>void;onGenerate:()=>void;onApply:()=>void;onEditBackend:(backend:ImportedBackend)=>void;onRemoveBackend:(name:string)=>void;onEditRoute:(route:ImportedRoute,index:number|null)=>void;busy:boolean}){
  const [error,setError]=useState(''),[saving,setSaving]=useState(false);
  const backends=doc.imported_backends||[],routes=doc.imported_routes||[];
  async function save(changes:ImportedFields){setSaving(true);setError('');try{await onSave(changes);}catch(e){setError((e as Error).message);}finally{setSaving(false);}}
  const fronts=doc.imported_route_frontends||Array.from(new Set(routes.map(r=>r.frontend))),locked=busy||saving;
  return <>
    <div className="draft-banner"><FileCode2 size={20}/><div><strong>Übernommene Konfiguration</strong><span>Domain-Routen und Backend-Ziele bearbeiten. Übrige Einstellungen bleiben im Text erhalten.</span></div><div className="row-actions"><button className="button secondary" disabled={locked} onClick={onGenerate}>Konfiguration erzeugen</button><button className="button" disabled={locked} onClick={onApply}><Check size={16}/>Prüfen & anwenden</button></div><button className="text-button" disabled={locked} onClick={onImport}>Erneut einlesen</button></div>
    {error&&<div role="alert" className="notice error">{error}</div>}
    <section className="panel"><div className="panel-header"><div><h2>Übernommene Domain-Zuordnungen</h2><p>Domain bearbeiten und unter Website-Zugang eine zentrale Basic-Auth-Gruppe zuweisen.</p></div>{fronts.length>0&&<button className="button" disabled={locked} onClick={()=>onEditRoute({id:'route_'+(crypto.randomUUID?crypto.randomUUID().replaceAll('-','').slice(0,16):Math.random().toString(36).slice(2)),frontend:fronts[0],domain:'',backend:backends[0]?.name||''},null)}><Plus size={16}/>Domain</button>}</div>
      {routes.length?<div className="table-wrap"><table><thead><tr><th>DOMAIN</th><th>FRONTEND</th><th>BACKEND</th><th>WEBSITE-ZUGANG</th><th>HEALTHCHECK</th><th></th></tr></thead><tbody>{routes.map((r,i)=><tr key={r.id}><td><strong>{r.domain}</strong><HostnameList entry={r}/></td><td>{r.frontend}</td><td>{r.backend}</td><td><span className={'badge '+(r.basic_auth_group?'blue':'neutral')}>{r.basic_auth_group?groups.find(g=>g.id===r.basic_auth_group)?.name||"Gruppe #"+r.basic_auth_group:(doc.basic_auth_existing||[]).some(s=>s.name===r.backend&&['backend','listen'].includes(s.kind)||s.name===r.frontend&&['frontend','listen'].includes(s.kind))?'Vorhandene Anmeldung':'Keine zentrale Gruppe'}</span>{r.access_policy&&<small>IP-Zugriff beschränkt</small>}</td><td><ProxyHealthCell view={health} kind="routes" id={r.id}/></td><td><button className="icon-button" disabled={locked} title="Domain und Website-Zugang bearbeiten" aria-label={'Domain '+r.domain+' bearbeiten'} onClick={()=>onEditRoute({...r},i)}><Settings2 size={16}/></button><button className="icon-button red-text" aria-label={'Domain '+r.domain+' entfernen'} disabled={locked} onClick={()=>void save({imported_routes:routes.filter((_,j)=>j!==i)})}><Trash2 size={16}/></button></td></tr>)}</tbody></table></div>:<p className="stats-empty">Keine einfachen Host-Map-Routen erkannt. Bestehende ACLs und dynamische Regeln bleiben im Texteditor erhalten.</p>}
    </section>
    <section className="panel"><div className="panel-header"><div><h2>Übernommene Backend-Pools</h2><p>HTTP und TCP getrennt gekennzeichnet. Healthchecks und zusätzliche Serveroptionen bleiben erhalten.</p></div><span className="badge">{backends.length}</span></div><div className="table-wrap"><table><thead><tr><th>POOL</th><th>MODUS</th><th>ZIELSERVER</th><th>VERTEILUNG</th><th></th></tr></thead><tbody>{backends.map(b=><tr key={b.name}><td><strong>{b.name}</strong></td><td><span className={'badge '+(b.mode==='tcp'?'amber':'neutral')}>{b.mode.toUpperCase()}</span></td><td>{b.servers.length?<details className="backend-targets"><summary>{b.servers.length} Zielserver</summary><div>{b.servers.map(s=><small key={s.name} className="mono">{s.name} · {s.address}:{s.port}{s.tls?' · TLS':''}</small>)}</div></details>:<small>Keine Zielserver</small>}</td><td>{b.balance||'Eigener Algorithmus'}</td><td><button className="icon-button" disabled={locked} aria-label={'Backend '+b.name+' bearbeiten'} onClick={()=>onEditBackend(structuredClone(b))}><Settings2 size={16}/></button><button className="icon-button red-text" disabled={locked} aria-label={'Backend '+b.name+' löschen'} title="Backend löschen" onClick={()=>onRemoveBackend(b.name)}><Trash2 size={16}/></button></td></tr>)}</tbody></table></div></section>
    <p className="stats-note">Neue Listener und Pools legst du unter Frontends & Backends an. Zusätzliche Reverseproxys erzeugen ihren Backend-Pool automatisch. Komplexe ACLs und weitere TLS-Parameter bearbeitest du im Konfigurationseditor. Zentrale Basic-Auth-Gruppen wählst du bei der Domain-Zuordnung. Änderungen werden erst nach Prüfung und Anwenden wirksam.</p>
  </>;
}

function useImportedFormSave<T>(onSave:(value:T)=>Promise<void>){
  const [error,setError]=useState(''),errorRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(error)errorRef.current?.scrollIntoView({block:'nearest'});},[error]);
  async function save(value:T){setError('');try{await onSave(value);}catch(e){setError((e as Error).message);}}
  return {save,error,errorRef};
}

export function ImportedBackendForm({initial,busy,onClose,onSave}:{initial:ImportedBackend;busy:boolean;onClose:()=>void;onSave:(value:ImportedBackend)=>Promise<void>}){
  const [backend,setBackend]=useState<ImportedBackend>(()=>structuredClone(initial));
  const {save,error,errorRef}=useImportedFormSave(onSave);
  return <form className="form-body" onSubmit={e=>{e.preventDefault();if(!busy)void save(backend);}}>
    {error&&<div ref={errorRef} role="alert" className="notice error">{error}</div>}
    <fieldset className="imported-form-fields" disabled={busy}>
      <div className="field"><label htmlFor="imported-balance">Verteilung</label><select id="imported-balance" value={backend.balance||''} onChange={e=>setBackend({...backend,balance:e.target.value||null})}>
        {!backend.balance&&<option value="">Vorhandenen Algorithmus behalten</option>}<option value="roundrobin">Round Robin</option><option value="leastconn">Least Connections</option><option value="source">Source</option><option value="first">First</option>
      </select>{backend.balance==='first'&&<small>First bevorzugt verfügbare Server nach aufsteigender Server-ID (standardmäßig Listenreihenfolge). Gewichte werden ignoriert. Mit maxconn pro Zielserver wechselt HAProxy bei voller Auslastung zum nächsten Server; bestehende Werte bleiben erhalten. maxconn im Konfigurationseditor setzen.</small>}</div>
      {backend.servers.map((s,i)=><div className="form-grid imported-server-edit" key={s.name}>
        <strong>{s.name}{s.tls?' · TLS':''}</strong>
        {(['address','port','weight'] as const).map(key=><div className="field" key={key}><label htmlFor={`import-${i}-${key}`}>{key==='address'?'Zieladresse':key==='port'?'Port':'Gewicht'}</label><input id={`import-${i}-${key}`} required type={key==='address'?'text':'number'} min={key==='port'?1:0} max={key==='port'?65535:256} value={s[key]} onChange={e=>setBackend({...backend,servers:backend.servers.map((sv,j)=>i===j?{...sv,[key]:key==='address'?e.target.value:Number(e.target.value)}:sv)})}/></div>)}
      {s.tls&&<BackendTLSOptions tls verify={s.tls_verify} onVerify={tls_verify=>setBackend({...backend,servers:backend.servers.map((sv,j)=>i===j?{...sv,tls_verify}:sv)})}/>}
      </div>)}
      {backend.mode==='http'&&<ProxyOptions value={backend.proxy_options} disabled={busy} onChange={proxy_options=>setBackend({...backend,proxy_options})}/>}
      <div className="modal-actions"><button type="button" className="button secondary" onClick={onClose}>Abbrechen</button><button type="submit" className="button">{busy&&<Loader2 size={16} className="spin"/>}Im Entwurf speichern</button></div>
    </fieldset>
  </form>;
}

export function ImportedRouteForm({initial,groups,fronts,backends,certs=[],existing=[],sharedRoutes=[],busy,onClose,onSave}:{sharedRoutes?:ImportedRoute[];initial:ImportedRoute;certs?:Certificate[];existing?:ExistingAuth[];groups:BasicGroup[];fronts:string[];backends:ImportedBackend[];busy:boolean;onClose:()=>void;onSave:(value:ImportedRoute,options?:string[])=>Promise<void>}){
  const [route,setRoute]=useState<ImportedRoute>(()=>({...initial}));
  const [options,setOptions]=useState<string[]|undefined>(undefined);
  const pool=backends.find(b=>b.name===route.backend);
  const {save,error,errorRef}=useImportedFormSave(async(value:ImportedRoute)=>onSave(value,options));
  const tcp=backends.find(b=>b.name===route.backend)?.mode==='tcp';
  const legacy=existing.filter(s=>['backend','listen'].includes(s.kind)&&s.name===route.backend).flatMap(s=>s.rules);
  const frontendAuth=existing.filter(s=>['frontend','listen'].includes(s.kind)&&s.name===route.frontend).flatMap(s=>s.rules);
  const conflict=!!route.basic_auth_group&&(frontendAuth.length>0||legacy.length>0&&!route.basic_auth_replace_existing);
  const migrationSaved=!!initial.basic_auth_group&&!!initial.basic_auth_replace_existing&&!!route.basic_auth_group&&!!route.basic_auth_replace_existing&&route.domain===initial.domain&&JSON.stringify(route.aliases||[])===JSON.stringify(initial.aliases||[])&&route.frontend===initial.frontend&&route.backend===initial.backend;
  function chooseBackend(name:string){setOptions(undefined);const tcp=backends.find(b=>b.name===name)?.mode==='tcp';setRoute({...route,backend:name,basic_auth_replace_existing:false,...(tcp?{basic_auth_group:null,basic_auth_forward:false,access_policy:null}:{})});}
  return <form className="form-body" onSubmit={e=>{e.preventDefault();if(!busy&&!conflict)void save(route);}}>
    {error&&<div ref={errorRef} role="alert" className="notice error">{error}</div>}
    <fieldset className="imported-form-fields" disabled={busy}>
      <div className="field"><label htmlFor="imported-domain">Domain</label><input id="imported-domain" required value={route.domain} onChange={e=>setRoute({...route,domain:e.target.value,basic_auth_replace_existing:false})}/></div>
      <HostnameAliases value={route.aliases} onChange={aliases=>setRoute({...route,aliases,basic_auth_replace_existing:false})}/>
      <div className="form-grid"><div className="field"><label htmlFor="imported-frontend">Frontend</label><select id="imported-frontend" value={route.frontend} onChange={e=>setRoute({...route,frontend:e.target.value,basic_auth_replace_existing:false})}>{fronts.map(f=><option key={f}>{f}</option>)}</select></div>
        <div className="field"><label htmlFor="imported-target">Backend-Pool</label><input id="imported-target" required list="imported-backend-options" value={route.backend} onChange={e=>chooseBackend(e.target.value)}/><datalist id="imported-backend-options">{backends.map(b=><option key={b.name} value={b.name}/>)}</datalist></div>
      </div>
      <CertificateSelector certs={certs} value={route.certificate} onChange={name=>setRoute({...route,certificate:name})}/>
      {pool?.mode==='http'&&<ProxyOptions value={options??pool.proxy_options} disabled={busy} shared={Array.from(new Set([...sharedRoutes.filter(r=>r.backend===route.backend&&r.id!==route.id).flatMap(hostnames),...hostnames(route)]))} onChange={setOptions}/>}
      {!tcp&&<AccessPolicyEditor value={route.access_policy} disabled={busy} onChange={access_policy=>setRoute({...route,access_policy})}/>}
      <BasicAuthSelector unassignedLabel="Keine zentrale Gruppe · vorhandene Regeln behalten" groups={groups} value={route.basic_auth_group} forward={route.basic_auth_forward} disabled={tcp} onChange={id=>setRoute({...route,basic_auth_group:id,basic_auth_forward:false,basic_auth_replace_existing:!!id&&route.basic_auth_replace_existing})} onForward={forward=>setRoute({...route,basic_auth_forward:forward})}/>
      {legacy.length>0&&!migrationSaved&&<section className="notice"><div><strong>Vorhandene Backend-Anmeldung · {route.backend}</strong><p>Diese Domain unterliegt bereits eigenen Authentifizierungsregeln. Ohne zentrale Gruppe bleiben diese Regeln aktiv.</p><details><summary>Vorhandene Regeln anzeigen</summary>{legacy.map((rule,i)=><pre className="wizard-command" key={i}>{rule}</pre>)}</details>{!!route.basic_auth_group&&<><label className="checkbox"><input type="checkbox" checked={!!route.basic_auth_replace_existing} onChange={e=>setRoute({...route,basic_auth_replace_existing:e.target.checked})}/>{route.aliases?.length?'Vorhandene Backend-Anmeldung für diese Hostnamen ersetzen':'Vorhandene Backend-Anmeldung für diese Domain ersetzen'}</label><p>Für {route.domain?hostnames(route).join(', '):'diese Domain'} am Frontend {route.frontend} gilt danach die ausgewählte zentrale Gruppe. Die bisherigen Benutzer erhalten hier nur Zugang, wenn sie dieser Gruppe angehören. Andere Domains und Frontends behalten ihre bisherigen Regeln.</p></>}</div></section>}
      {!!route.basic_auth_group&&frontendAuth.length>0&&<div className="notice error" role="alert">Im Frontend {route.frontend} besteht ebenfalls eine eigene Anmeldung. Diese zuerst in der ursprünglichen Konfiguration abstimmen und erneut einlesen. Die Umstellung hier gilt für Backend-Regeln.</div>}
      {!groups.length&&<p className="stats-note">Lege unter Basic Auth zuerst eine Gruppe an und ordne ihr die gewünschten Website-Benutzer zu. Danach kannst du die Gruppe hier auswählen.</p>}
      <p className="stats-note">Speichern ändert den grafischen Entwurf auf diesem Zielserver. Der Website-Zugang wird nach Konfiguration erzeugen, Prüfen und Anwenden aktiv.</p>
      <div className="modal-actions"><button type="button" className="button secondary" onClick={onClose}>Abbrechen</button><button type="submit" className="button" disabled={conflict}>{busy&&<Loader2 size={16} className="spin"/>}Im Entwurf speichern</button></div>
    </fieldset>
  </form>;
}

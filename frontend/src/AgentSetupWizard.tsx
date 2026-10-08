import React, { useId, useRef, useState } from 'react';
import { Check, CheckCircle2, ChevronLeft, Copy, Loader2, Server, Terminal } from 'lucide-react';

import { ClassificationFields } from './ServerDirectory';

type Kind = 'native' | 'docker';
type Plan = {command:string; instance:{name:string;agent_url:string;profile:string;token:string;allow_http:boolean;notes:string;tags:string[];location:string}};
const paths = (kind:Kind) => kind === 'native'
  ? {profile:'native',config_path:'/etc/haproxy/haproxy.cfg',runtime_socket:'/run/haproxy/admin.sock',cert_dir:'/etc/haproxy/certs'}
  : {profile:'docker-edge',config_path:'/opt/haproxy/config/haproxy.cfg',runtime_socket:'/opt/haproxy/run/admin.sock',cert_dir:'/opt/haproxy/certs'};
function Input({label,hint,...props}:React.InputHTMLAttributes<HTMLInputElement>&{label:string;hint?:string}) {
  const id=useId();
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} {...props}/>{hint&&<small>{hint}</small>}</div>;
}

export function AgentSetupWizard({request,onConnected,onExisting}:{
  request:(path:string,method?:string,body?:unknown)=>Promise<any>;
  onConnected:()=>Promise<void>;
  onExisting:()=>void;
}) {
  const [step,setStep]=useState(0), [busy,setBusy]=useState(false), [error,setError]=useState(''), [copied,setCopied]=useState(false);
  const [value,setValue]=useState({tags:[] as string[],location:'',name:'',kind:'native' as Kind,host:'',port:9101,service:'haproxy',container:'haproxy',
    container_config_dir:'/usr/local/etc/haproxy',runtime_socket_config:'/run/haproxy/admin.sock',cert_dir_config:'/etc/haproxy/certs',allow_http:false,...paths('native')});
  const [plan,setPlan]=useState<Plan|null>(null);
  const commandRef=useRef<HTMLTextAreaElement>(null);
  function update(key:string,v:unknown){setValue(old=>({...old,[key]:v}));setError('');}
  async function generate(){setBusy(true);setError('');try{setPlan(await request('/agent-setup','POST',value));setStep(2);setCopied(false);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function connect(){if(!plan)return;setBusy(true);setError('');try{await request('/instances','POST',plan.instance);await onConnected();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function copy(){
    try {if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(plan!.command);
      else {commandRef.current?.focus();commandRef.current?.select();if(!document.execCommand('copy'))throw new Error('Bitte den markierten Befehl manuell kopieren.');}
      setCopied(true);
    } catch(e){setError((e as Error).message);}
  }
  return <div className="form-body setup-wizard">
    <ol className="wizard-steps" aria-label="Einrichtungsschritte">{['Installation','Host & Pfade','Befehl & Verbindung'].map((label,i)=><li key={label} className={i===step?'active':i<step?'done':''}><span>{i<step?<Check size={14}/>:i+1}</span>{label}</li>)}</ol>
    {error&&<div role="alert" className="notice error">{error}</div>}
    {step===0&&<form onSubmit={e=>{e.preventDefault();setStep(1);}}>
      <h3>Wie läuft HAProxy auf diesem Host?</h3><p className="wizard-intro">Der Assistent erstellt einen Installationsbefehl für einen vorhandenen HAProxy auf Debian oder Ubuntu mit systemd.</p>
      <div className="wizard-kind">{(['native','docker'] as Kind[]).map(kind=><button key={kind} type="button" className={value.kind===kind?'selected':''} aria-pressed={value.kind===kind} onClick={()=>setValue(old=>({...old,kind,...paths(kind)}))}><Server size={22}/><strong>{kind==='native'?'Nativ installiert':'Docker-Container'}</strong><span>{kind==='native'?'Dienst über systemd verwalten':'Agent auf dem Docker-Host installieren'}</span></button>)}</div>
      <Input label="Name in der Management-Oberfläche" required maxLength={120} placeholder="z. B. Edge Frankfurt" value={value.name} onChange={e=>update('name',e.target.value)}/>
      <ClassificationFields value={value} onChange={next=>setValue({...value,...next})}/>
      <div className="modal-actions"><button type="button" className="button secondary" onClick={onExisting}>Agent bereits installiert</button><button type="submit" className="button">Weiter</button></div>
    </form>}
    {step===1&&<form onSubmit={e=>{e.preventDefault();void generate();}}>
      <div className="form-grid"><Input label="Private IP des HAProxy-Hosts" required placeholder="192.168.10.71" value={value.host} onChange={e=>update('host',e.target.value)} hint="Vom Management-Container erreichbar; kein localhost."/><Input label="Agent-Port" type="number" required min={1024} max={65535} value={value.port} onChange={e=>update('port',Number(e.target.value))}/></div>
      <div className="form-grid"><Input label={value.kind==='native'?'systemd-Dienst':'Docker-Containername'} required value={value.kind==='native'?value.service:value.container} onChange={e=>update(value.kind==='native'?'service':'container',e.target.value)}/><Input label="Agent-Profilname" required pattern="[a-zA-Z0-9_.-]+" maxLength={80} value={value.profile} onChange={e=>update('profile',e.target.value)} hint="Ein eigenes Profil je HAProxy-Instanz."/></div>
      <Input label="HAProxy-Konfiguration auf dem Host" required value={value.config_path} onChange={e=>update('config_path',e.target.value)}/>
      <div className="form-grid"><Input label="Runtime-Socket auf dem Host" required value={value.runtime_socket} onChange={e=>update('runtime_socket',e.target.value)}/><Input label="Zertifikatsverzeichnis auf dem Host" required value={value.cert_dir} onChange={e=>update('cert_dir',e.target.value)}/></div>
      {value.kind==='docker'&&<><h3>Pfade im HAProxy-Container</h3><Input label="Konfigurationsverzeichnis im Container" required value={value.container_config_dir} onChange={e=>update('container_config_dir',e.target.value)}/><div className="form-grid"><Input label="Runtime-Socket im Container" required value={value.runtime_socket_config} onChange={e=>update('runtime_socket_config',e.target.value)}/><Input label="Zertifikatsverzeichnis im Container" required value={value.cert_dir_config} onChange={e=>update('cert_dir_config',e.target.value)}/></div><div className="notice"><Terminal size={18}/><span>Diese Verzeichnisse müssen bereits gemountet sein. Einzelne Config-Datei-Mounts werden nicht unterstützt. Mount-Änderungen erfordern eine Neuerstellung des Containers.</span></div><pre className="wizard-mounts">{`volumes:\n  - ${value.config_path.slice(0,value.config_path.lastIndexOf('/'))}:${value.container_config_dir}:ro\n  - ${value.runtime_socket.slice(0,value.runtime_socket.lastIndexOf('/'))}:${value.runtime_socket_config.slice(0,value.runtime_socket_config.lastIndexOf('/'))}\n  - ${value.cert_dir}:${value.cert_dir_config}:ro`}</pre></>}
      <label className="checkbox"><input required type="checkbox" checked={value.allow_http} onChange={e=>update('allow_http',e.target.checked)}/><span>HTTP für den Agenten im privaten Netz zulassen. Port nur für den Management-Host freigeben.</span></label>
      <div className="modal-actions"><button type="button" disabled={busy} className="button secondary" onClick={()=>setStep(0)}><ChevronLeft size={15}/>Zurück</button><button type="submit" disabled={busy} className="button">{busy?<Loader2 className="spin" size={16}/>:<Terminal size={16}/>}Installationsbefehl erstellen</button></div>
    </form>}
    {step===2&&plan&&<>
      <h3>Befehl auf {value.host} ausführen</h3><p className="wizard-intro">Per SSH auf den HAProxy-Host einloggen und den Befehl einmal ausführen. Er installiert den Agenten, erzeugt sein Profil und ergänzt bei Bedarf den Runtime-Socket. HAProxy wird nach erfolgreicher Config-Prüfung neu geladen; die bisherige Datei wird gesichert.</p>
      <textarea ref={commandRef} className="wizard-command" aria-label="Installationsbefehl" readOnly rows={5} value={plan.command} onClick={e=>e.currentTarget.select()}/>
      <button type="button" className="button secondary" onClick={()=>void copy()}>{copied?<CheckCircle2 size={16}/>:<Copy size={16}/>} {copied?'Befehl kopiert':'Befehl kopieren'}</button>
      <div className="notice"><Terminal size={18}/><span>Der Befehl enthält den neuen Agent-Token. Nach erfolgreicher Ausführung Port {value.port} für den Management-Host freigeben und unten die Verbindung prüfen. Lass diesen Dialog bis zum Verbinden geöffnet.</span></div>
      <dl className="wizard-summary"><div><dt>Agent-Adresse</dt><dd>{plan.instance.agent_url}</dd></div><div><dt>Profil</dt><dd>{plan.instance.profile}</dd></div><div><dt>Installation</dt><dd>{value.kind==='native'?'Nativ':'Docker'}</dd></div></dl>
      <p className="wizard-intro">Wenn der Dialog geschlossen wurde, lässt sich der installierte Agent mit Profilname und Token aus <code>/etc/haproxy-control/agent.json</code> über „Agent bereits installiert“ verbinden. <a href="/api/agent-guide" target="_blank" rel="noreferrer">Vollständige Anleitung</a></p>
      <div className="modal-actions"><button type="button" className="button secondary" disabled={busy} onClick={()=>{setPlan(null);setStep(1);}}>Angaben ändern</button><button type="button" className="button" disabled={busy} onClick={()=>void connect()}>{busy?<Loader2 size={16} className="spin"/>:<CheckCircle2 size={16}/>}Verbindung prüfen & speichern</button></div>
    </>}
  </div>;
}

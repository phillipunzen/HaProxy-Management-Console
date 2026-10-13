import {AccessBadge} from './AccessVisibility';
import {localNetwork,summarizeAccess} from './accessVisibility';
import {useId,useState} from 'react';
import {Plus,Settings2,ShieldCheck,Trash2} from 'lucide-react';
import {AccessPolicyEditor,type AccessPolicy} from './AccessPolicy';

export type AccessProfile={id:string;name:string;description:string;networks:string[]};
export type AccessAssignment={access_policy?:AccessPolicy|null;access_profile?:string|null;access_paths?:string[]};
const profileId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
export const localProfile=():AccessProfile=>({id:profileId(),name:'Nur lokaler Zugriff',description:'Private IPv4-Netze, IPv6-ULA und Loopback. Auf die tatsächlich erlaubten LAN-/VPN-Netze anpassen.',networks:['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7','127.0.0.1','::1']});
export function AccessFilterEditor({value,profiles,onChange,onProfilesChange,onEditingChange,disabled=false,basePath='/',manual=false}:{value:AccessAssignment;profiles:AccessProfile[];onChange:(value:AccessAssignment)=>void;onProfilesChange:(profiles:AccessProfile[])=>void;onEditingChange:(editing:boolean)=>void;disabled?:boolean;basePath?:string;manual?:boolean}){
  const id=useId(),profile=profiles.find(p=>p.id===value.access_profile);
  const [draft,setDraft]=useState<AccessProfile|null>(null),[error,setError]=useState('');
  function begin(local=false){
    const next=local?localProfile():{id:profileId(),name:'',description:'',networks:profile?.networks||value.access_policy?.networks||['']};
    if(local){const base=next.name;let n=2;while(profiles.some(p=>p.name.toLowerCase()===next.name.toLowerCase()))next.name=base+' '+n++;}
    setDraft(next);setError('');onEditingChange(true);
  }
  function finish(){setDraft(null);setError('');onEditingChange(false);}
  function adopt(){
    if(!draft)return;
    const name=draft.name.trim(),networks=Array.from(new Set(draft.networks.join('\n').split(/[\s,]+/).filter(Boolean)));
    if(!name){setError('Bitte einen Profilnamen angeben.');return;}
    if(profiles.some(p=>p.name.toLowerCase()===name.toLowerCase())){setError('Ein Profil mit diesem Namen existiert bereits auf diesem Server. Das vorhandene Profil auswählen oder einen anderen Namen verwenden.');return;}
    if(!networks.length||networks.length>100){setError('Mindestens eine und höchstens 100 erlaubte IP-Adressen oder Netze angeben.');return;}
    const invalid=networks.find(n=>localNetwork(n)===null);if(invalid){setError('Ungültige IP-Adresse oder CIDR-Netz: '+invalid);return;}
    onProfilesChange([...profiles,{...draft,name,networks}]);
    onChange({access_profile:draft.id,access_paths:value.access_profile?value.access_paths||[]:value.access_policy?.paths||[],access_policy:null});finish();
  }
  const selection=value.access_profile?'profile:'+value.access_profile:value.access_policy?'custom':'off';
  return <section className="proxy-options-editor">
    <div className="form-section-title"><h3><ShieldCheck size={16}/> IP-Filter</h3><button className="text-button" type="button" disabled={disabled||!!draft||profiles.length>=100} onClick={()=>begin()}><Plus size={14}/>Profil hinzufügen</button></div>
    <div className="field"><label htmlFor={id}>Zugriffsprofil</label><select id={id} disabled={disabled||!!draft} value={selection} onChange={e=>{
      const selected=e.target.value;
      if(selected==='create:local'||selected==='create:custom'){begin(selected==='create:local');return;}
      onChange(selected.startsWith('profile:')?{access_profile:selected.slice(8),access_paths:value.access_profile?value.access_paths||[]:value.access_policy?.paths||[],access_policy:null}:selected==='custom'?{access_profile:null,access_paths:[],access_policy:{networks:profile?.networks||value.access_policy?.networks||[''],paths:value.access_paths||[]}}:{access_profile:null,access_paths:[],access_policy:null});
    }}><option value="off">Kein IP-Filter</option><option value="custom">Eigene IP-Liste für diesen Eintrag</option>{profiles.length>0&&<optgroup label="Gespeicherte Profile dieses Servers">{profiles.map(p=><option key={p.id} value={'profile:'+p.id}>{p.name}</option>)}</optgroup>}<optgroup label="Neues Profil anlegen"><option disabled={profiles.length>=100} value="create:local">Vorlage: Nur lokaler Zugriff …</option><option disabled={profiles.length>=100} value="create:custom">Eigenes Profil erstellen …</option></optgroup>{value.access_profile&&!profile&&<option value={selection}>Profil nicht mehr vorhanden</option>}</select><small>Ein gespeichertes Profil wählen oder direkt hier ein Profil anlegen. Unter „IP-Filter-Profile“ werden die Netze zentral verwaltet. Änderungen gelten für alle zugewiesenen Einträge nach Prüfen & anwenden.</small></div>
    {!profiles.length&&!draft&&<div className="notice"><span>Noch keine IP-Filter-Profile auf diesem Server. In der Auswahl „Vorlage: Nur lokaler Zugriff …“ wählen, um mit vorausgefüllten Netzen zu starten.</span></div>}
    {draft&&<section className="access-profile-inline" aria-label="IP-Filter-Profil im Host anlegen">
      <h4>Neues IP-Filter-Profil</h4><p>Das Profil wird mit diesem Host im Entwurf gespeichert und steht anschließend für weitere Einträge dieses Servers zur Auswahl.</p>
      {error&&<div className="notice error" role="alert">{error}</div>}
      <AccessProfileFields value={draft} onChange={setDraft} disabled={disabled}/>
      <div className="modal-actions"><button className="button secondary" type="button" disabled={disabled} onClick={finish}>Profil verwerfen</button><button className="button" type="button" disabled={disabled} onClick={adopt}>Profil übernehmen</button></div>
      <small>Zuerst das Profil übernehmen oder verwerfen, anschließend den Host im Entwurf speichern.</small>
    </section>}
    <AccessBadge summary={summarizeAccess({...value,path:basePath},profiles,manual)}/>
    {value.access_profile&&<>
      {!profile?<div className="notice error" role="alert">Das zugewiesene Profil fehlt. Ein anderes Profil oder eine eigene Liste auswählen.</div>:<><p>{profile.description}</p><div className="field"><label>Erlaubte IP-Adressen / CIDR-Netze · {profile.name}</label><pre className="wizard-command">{profile.networks.join('\n')}</pre></div></>}
      <div className="field"><label htmlFor={id+'-paths'}>Geschützte Pfad-Präfixe (optional)</label><textarea id={id+'-paths'} disabled={disabled} rows={3} maxLength={6500} value={(value.access_paths||[]).join('\n')} onChange={e=>onChange({...value,access_paths:e.target.value.split('\n')})} placeholder={basePath==='/'?'/admin\n/internal':basePath+'/admin'}/><small>Leer = gesamter Eintrag mit allen Hostnamen{basePath!=='/'?' unter '+basePath:''}. /admin trifft auch /administrator. Pfade gelten nur für diesen Eintrag und werden vor Umschreibungen geprüft.</small></div>
      <div className="notice"><span>Es zählt die Client-IP aus Sicht von HAProxy. Bei einem vorgeschalteten Proxy, CDN oder NAT kann das dessen Adresse sein. X-Forwarded-For wird nicht ausgewertet. IP-Filter und Basic Auth gelten gemeinsam.</span></div>
    </>}
    {!value.access_profile&&value.access_policy&&<AccessPolicyEditor value={value.access_policy} disabled={disabled} basePath={basePath} onChange={access_policy=>onChange({access_profile:null,access_paths:[],access_policy})}/>}
  </section>;
}

export function AccessProfileManager({profiles,entries,busy,onCreate,onEdit,onDelete,onApply}:{profiles:AccessProfile[];entries:(AccessAssignment&{domain:string})[];busy:boolean;onCreate:(preset?:AccessProfile)=>void;onEdit:(profile:AccessProfile)=>void;onDelete:(profile:AccessProfile)=>void;onApply:()=>void}){
  return <section className="panel"><div className="panel-header"><div><h2>Wiederverwendbare IP-Filter</h2><p>Profile gelten für den ausgewählten Server. Ein Profil kann mehrere Proxy Hosts und übernommene Domains schützen.</p></div><button className="button" disabled={busy} onClick={()=>onCreate()}><Plus size={16}/>Profil hinzufügen</button></div>
    <div className="form-body"><div className="notice"><span>Profiländerungen werden im Entwurf gespeichert. Die laufenden Zugriffsregeln ändern sich erst mit „Prüfen & anwenden“.</span></div><div className="modal-actions"><button className="button secondary" disabled={busy} onClick={()=>onCreate(localProfile())}><ShieldCheck size={16}/>Vorlage: Nur lokaler Zugriff</button><button className="button" disabled={busy} onClick={onApply}>Prüfen & anwenden</button></div></div>
    {profiles.length?<div className="table-wrap"><table><thead><tr><th>PROFIL</th><th>ERLAUBTE NETZE</th><th>ZUORDNUNGEN</th><th></th></tr></thead><tbody>{profiles.map(p=>{const usage=entries.filter(e=>e.access_profile===p.id);return <tr key={p.id}><td><strong>{p.name}</strong>{p.description&&<small>{p.description}</small>}</td><td><details><summary>{p.networks.length} Adressen / Netze anzeigen</summary><pre className="wizard-command">{p.networks.join('\n')}</pre></details></td><td>{usage.length} Einträge{usage.length>0&&<details><summary>Domains anzeigen</summary>{usage.map((e,i)=><small key={i}>{e.domain}</small>)}</details>}</td><td><div className="row-actions"><button className="icon-button" disabled={busy} aria-label={'Profil '+p.name+' bearbeiten'} onClick={()=>onEdit(p)}><Settings2 size={16}/></button><button className="icon-button red-text" disabled={busy||usage.length>0} title={usage.length?'Zuerst die Zuordnungen dieses Profils ändern':'Profil löschen'} aria-label={'Profil '+p.name+' löschen'} onClick={()=>onDelete(p)}><Trash2 size={16}/></button></div></td></tr>;})}</tbody></table></div>:<div className="empty"><ShieldCheck size={28}/><h3>IP-Listen einmal pflegen</h3><p>Lege beispielsweise „Nur lokaler Zugriff“, „VPN“ oder „Büro“ an und wähle das Profil anschließend im Proxy Host.</p></div>}
  </section>;
}
export function AccessProfileForm({initial,busy,onClose,onSave}:{initial:AccessProfile|null;busy:boolean;onClose:()=>void;onSave:(profile:AccessProfile)=>Promise<void>}){
  const [value,setValue]=useState<AccessProfile>(initial?structuredClone(initial):{id:profileId(),name:'',description:'',networks:['']}),[error,setError]=useState('');
  return <form className="form-body" onSubmit={async e=>{e.preventDefault();setError('');try{await onSave(value);}catch(e){setError((e as Error).message);}}}>
    {error&&<div className="notice error" role="alert">{error}</div>}
    <fieldset className="imported-form-fields" disabled={busy}><AccessProfileFields value={value} onChange={setValue} disabled={busy}/><div className="modal-actions"><button className="button secondary" type="button" onClick={onClose}>Abbrechen</button><button className="button" type="submit" disabled={busy}>Im Entwurf speichern</button></div></fieldset>
  </form>;
}

export function AccessProfileFields({value,onChange,disabled=false}:{value:AccessProfile;onChange:(value:AccessProfile)=>void;disabled?:boolean}){
  const id=useId();
  return <fieldset className="imported-form-fields" disabled={disabled}><div className="field"><label htmlFor={id+'-name'}>Profilname</label><input id={id+'-name'} required maxLength={120} value={value.name} onChange={e=>onChange({...value,name:e.target.value})} placeholder="z. B. Nur lokaler Zugriff"/></div><div className="field"><label htmlFor={id+'-description'}>Beschreibung (optional)</label><input id={id+'-description'} maxLength={500} value={value.description} onChange={e=>onChange({...value,description:e.target.value})}/></div><div className="field"><label htmlFor={id+'-networks'}>Erlaubte IP-Adressen / CIDR-Netze</label><textarea id={id+'-networks'} required rows={6} maxLength={6000} value={value.networks.join('\n')} onChange={e=>onChange({...value,networks:e.target.value.split('\n')})} placeholder={'192.168.10.0/24\n10.8.0.0/24\nfd12:3456::/48'}/><small>Eine Adresse oder ein Netz pro Zeile; IPv4 und IPv6. Andere Client-Adressen erhalten HTTP 403. Die Vorlage „Nur lokaler Zugriff“ erlaubt alle privaten Netze; auf die gewünschten LAN-/VPN-Netze eingrenzen. Öffentliche IPv6-LAN-Präfixe bei Bedarf ausdrücklich ergänzen.</small></div></fieldset>;
}

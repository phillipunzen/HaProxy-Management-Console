import {useId} from 'react';
import {ShieldCheck} from 'lucide-react';
export type AccessPolicy={networks:string[];paths:string[]};
export function AccessPolicyEditor({value,onChange,disabled=false,basePath='/'}:{value?:AccessPolicy|null;onChange:(value:AccessPolicy|null)=>void;disabled?:boolean;basePath?:string}){
  const id=useId();
  return <section className="proxy-options-editor">
    <div className="form-section-title"><h3><ShieldCheck size={16}/> IP-Zugriff</h3></div>
    <label className="checkbox"><input type="checkbox" disabled={disabled} checked={!!value} onChange={e=>onChange(e.target.checked?{networks:[''],paths:[]}:null)}/>Zugriff auf erlaubte IP-Adressen und Netze beschränken</label>
    {value&&<>
      <div className="field"><label htmlFor={id+'-networks'}>Erlaubte IP-Adressen / CIDR-Netze</label><textarea id={id+'-networks'} required disabled={disabled} spellCheck={false} rows={4} maxLength={6000} value={value.networks.join('\n')} onChange={e=>onChange({...value,networks:e.target.value.split('\n')})} placeholder={'192.168.10.0/24\n10.8.0.12\n2001:db8::/32'}/><small>Eine Adresse oder ein Netz pro Zeile; Kommas sind ebenfalls möglich. Mindestens ein Eintrag. Andere Adressen erhalten HTTP 403.</small></div>
      <div className="field"><label htmlFor={id+'-paths'}>Geschützte Pfad-Präfixe (optional)</label><textarea id={id+'-paths'} disabled={disabled} spellCheck={false} rows={3} maxLength={6500} value={value.paths.join('\n')} onChange={e=>onChange({...value,paths:e.target.value.split('\n')})} placeholder={basePath==='/'?'/admin\n/internal':basePath+'/admin'}/><small>Leer = gesamter Reverseproxy-Eintrag mit allen Hostnamen{basePath!=='/'?' unter '+basePath:''}. Sonst ein absoluter Pfad-Präfix pro Zeile. /admin trifft auch /administrator. Geprüft wird vor Backend-Pfadumschreibungen.</small></div>
      <div className="notice"><span>Es zählt die Client-IP aus Sicht von HAProxy. Bei einem vorgeschalteten Proxy, CDN oder NAT kann das dessen Adresse sein. X-Forwarded-For wird für diese Prüfung nicht ausgewertet. IP-Liste und Basic Auth gelten gemeinsam; andere Domains desselben Backend-Pools behalten ihre eigenen Einstellungen.</span></div>
    </>}
  </section>;
}

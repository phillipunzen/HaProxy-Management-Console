import {useEffect,useState} from 'react';
import {Activity,Circle} from 'lucide-react';
import {currentHealth,healthLabels,type Health,type HealthData} from './proxyHealthState';

export function useProxyHealth(id:number|null,version:number,enabled:boolean,request:(path:string)=>Promise<HealthData>){
  const [snapshot,setSnapshot]=useState<{id:number;data:HealthData}|null>(null),[error,setError]=useState(''),[now,setNow]=useState(Date.now());
  useEffect(()=>{
    setSnapshot(null);setError('');
    if(!enabled||!id)return;
    let disposed=false,pending=false;
    async function refresh(){
      if(disposed||pending||document.hidden)return;
      pending=true;
      try{const value=await request(`/instances/${id}/proxy-health`);if(!disposed){setSnapshot({id:id!,data:value});setError('');setNow(Date.now());}}
      catch{if(!disposed)setError('Live-Healthchecks konnten nicht abgefragt werden. Dienstzustand ist unbekannt.');}
      finally{pending=false;}
    }
    void refresh();
    const timer=setInterval(()=>{setNow(Date.now());void refresh();},10000);
    const visible=()=>{if(!document.hidden){setNow(Date.now());void refresh();}};
    document.addEventListener('visibilitychange',visible);
    return()=>{disposed=true;clearInterval(timer);document.removeEventListener('visibilitychange',visible);};
  },[id,version,enabled,request]);
  return {data:snapshot?.id===id?snapshot.data:null,error,now,version};
}
export type HealthView=ReturnType<typeof useProxyHealth>;
export function HealthBadge({health}:{health:Health}){
  const display=healthLabels[health.state]||healthLabels.unknown;
  return <span className={'badge '+display.tone}><Circle size={7} fill="currentColor"/>{display.label}</span>;
}
export function ProxyHealthCell({view,kind,id}:{view:HealthView;kind:'hosts'|'routes';id:string}){
  const h=currentHealth(view.data,kind,id,view.version,view.now,view.error);
  return <details className="proxy-health"><summary aria-label={'Healthcheck-Details: '+(healthLabels[h.state]||healthLabels.unknown).label} title={h.reason}><HealthBadge health={h}/>{h.total>0&&<small>{h.available} / {h.total} Ziele erreichbar</small>}</summary>
    <div className="proxy-health-details"><p>{h.reason}</p>{h.backend&&<small className="mono">{h.backend}</small>}{h.targets.map(t=><div className="proxy-health-target" key={t.name}>
      <strong>{t.name}{t.backup?' · Backup':''}</strong><span className="mono">{t.address}</span>
      <span className={'badge '+({up:'green',down:'red',maintenance:'amber',draining:'amber'}[t.state]||'neutral')}>{({up:'Erreichbar',down:'Ausgefallen',maintenance:'Wartung',draining:'Drain',unchecked:'Ohne Healthcheck',unknown:'Unbekannt'} as Record<string,string>)[t.state]||'Unbekannt'}</span>
      {t.check_status&&<small>{t.check_status}{t.check_code!=null&&t.check_code>0?' · Code '+t.check_code:''}{t.check_duration_ms!=null?' · '+t.check_duration_ms+' ms':''}</small>}{t.check_description&&<small>{t.check_description}</small>}
    </div>)}</div>
  </details>;
}
export function ProxyHealthSummary({view,hosts,routes}:{view:HealthView;hosts:{id:string}[];routes:{id:string}[]}){
  const checks=[...hosts.map(h=>currentHealth(view.data,'hosts',h.id,view.version,view.now,view.error)),...routes.map(r=>currentHealth(view.data,'routes',r.id,view.version,view.now,view.error))];
  const checked=view.data?.captured_at?new Date(view.data.captured_at).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit',timeZone:'Europe/Berlin'}):null;
  return <div className="proxy-health-summary"><Activity size={19}/><div><strong>Live-Healthchecks</strong><p>Erreichbarkeit der Backend-Ziele laut HAProxy · alle 10 s{checked?' · letzte Abfrage '+checked:''}</p><small>TCP-Verbindung oder konfigurierter HTTP-Check. Gemeinsame Backend-Pools teilen denselben Zustand.</small></div><div className="proxy-health-counts">{['up','partial','down','frontend_down'].map(state=>{const count=checks.filter(h=>h.state===state).length;return count>0?<span key={state} className={'badge '+healthLabels[state].tone}>{count} {healthLabels[state].label}</span>:null;})}</div></div>;
}

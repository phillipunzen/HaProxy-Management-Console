import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Activity, ArrowRight, Globe2, Maximize2, Network, Pause, Play, RefreshCw, Search, Server, X, ZoomIn, ZoomOut } from 'lucide-react';
import './topology.css';

type Metrics = {sessions?:number|null;session_rate?:number|null;http_rate?:number|null;http_total?:number|null;bytes_in?:number|null;bytes_out?:number|null;errors?:number|null;response_ms?:number|null};
export type TopologyNode = {id:string;kind:'frontend'|'route'|'backend'|'server';name:string;proxy:string;mode:string;service:string;address:string;status:string;runtime:boolean;metrics:Metrics;binds?:string[];domain?:string;path?:string;condition?:string;terminal?:boolean;dynamic?:boolean;missing?:boolean;default?:boolean;tls?:boolean;backup?:boolean;balance?:string;configured_address?:string;local_response?:boolean};
export type TopologyGraph = {nodes:TopologyNode[];edges:{id:string;source:string;target:string;measured:boolean}[];warnings:string[];online:boolean;captured_at:string;uptime_seconds:number|null;poll_seconds:number};
type Live = {rate:number|null;unit:string;throughput:number|null;activity:number;interval:boolean};
const fmt=(n?:number|null)=>n==null?'—':new Intl.NumberFormat('de-DE',{maximumFractionDigits:1}).format(n);
const bytes=(n?:number|null)=>n==null?'—':n>=1e6?fmt(n/1e6)+' MB':n>=1e3?fmt(n/1e3)+' kB':fmt(n)+' B';
const kinds={frontend:'Frontend',route:'Dienst / Site',backend:'Backend-Pool',server:'Zielserver'};
const state=(n:TopologyNode)=>n.missing?'Fehlt':!n.runtime?'Unbekannt':n.status;
const down=(n:TopologyNode)=>/^(DOWN|MAINT|STOP)/.test(n.status)||n.missing;

export function liveMetrics(graph:TopologyGraph,previous:TopologyGraph|null):Map<string,Live>{
  const elapsed=previous?(Date.parse(graph.captured_at)-Date.parse(previous.captured_at))/1000:0;
  const reset=previous&&graph.uptime_seconds!=null&&previous.uptime_seconds!=null&&graph.uptime_seconds<previous.uptime_seconds;
  const valid=!!previous&&previous.online&&graph.online&&elapsed>=1&&elapsed<=60&&!reset;
  const old=new Map(previous?.nodes.map(n=>[n.id,n])||[]);
  const delta=(value:number|null|undefined,before:number|null|undefined)=>valid&&value!=null&&before!=null&&value>=before?(value-before)/elapsed:null;
  return new Map(graph.nodes.map(n=>{
    const m=n.metrics,p=old.get(n.id)?.metrics;
    const input=delta(m.bytes_in,p?.bytes_in),output=delta(m.bytes_out,p?.bytes_out);
    const throughput=input!=null&&output!=null?input+output:null;
    const average=n.mode==='http'&&n.kind==='backend'?delta(m.http_total,p?.http_total):null;
    const rate=n.mode==='http'&&m.http_rate!=null?m.http_rate:average??m.session_rate??null;
    const unit=n.mode==='http'&&m.http_rate!=null||average!=null?'Anfragen/s':'Sessions/s';
    return [n.id,{rate,unit,throughput,activity:graph.online&&!down(n)?Math.max(rate||0,(throughput||0)/1024):0,interval:average!=null}];
  }));
}

function related(graph:TopologyGraph,id:string):Set<string>{
  // Traverse upstream and downstream separately: sharing a backend must not
  // highlight unrelated frontends through undirected backtracking.
  const result=new Set([id]);
  for(const upstream of [true,false]){
    const visited=new Set([id]),queue=[id];
    for(let i=0;i<queue.length;i++)for(const e of graph.edges){
      if((upstream?e.target:e.source)!==queue[i])continue;
      const next=upstream?e.source:e.target;
      if(!visited.has(next)){visited.add(next);result.add(next);queue.push(next);}
    }
  }
  return result;
}

export function Topology({instanceId,instanceName,request,refreshKey=0}:{instanceId:number;instanceName:string;request:<T>(path:string)=>Promise<T>;refreshKey?:number}){
  const [graph,setGraph]=useState<TopologyGraph|null>(null),[previous,setPrevious]=useState<TopologyGraph|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
  const [search,setSearch]=useState(''),[mode,setMode]=useState('all'),[frontend,setFrontend]=useState('all'),[selected,setSelected]=useState<string|null>(null),[animate,setAnimate]=useState(true),[zoom,setZoom]=useState(.85),[tick,setTick]=useState(Date.now()),[received,setReceived]=useState(0),[visible,setVisible]=useState(!document.hidden);
  const snapshot=useRef<TopologyGraph|null>(null),refresh=useRef<()=>void>(()=>{}),marker='topology-arrow-'+useId().replace(/:/g,'');
  useEffect(()=>{
    let alive=true,inflight=false;
    snapshot.current=null;setGraph(null);setPrevious(null);setSelected(null);setFrontend('all');setError('');setLoading(true);
    const load=async()=>{
      if(inflight||document.hidden)return;inflight=true;
      try{
        const value=await request<TopologyGraph>(`/instances/${instanceId}/topology`);
        if(alive){setPrevious(snapshot.current);snapshot.current=value;setGraph(value);setReceived(Date.now());setTick(Date.now());setError('');}
      }catch(e){if(alive)setError((e as Error).message);}
      finally{inflight=false;if(alive)setLoading(false);}
    };
    refresh.current=()=>{void load();};void load();
    const timer=setInterval(()=>{setTick(Date.now());void load();},10000);
    const visibility=()=>{setVisible(!document.hidden);if(!document.hidden){setTick(Date.now());void load();}};
    document.addEventListener('visibilitychange',visibility);
    return()=>{alive=false;clearInterval(timer);document.removeEventListener('visibilitychange',visibility);};
  },[instanceId,request]);
  useEffect(()=>{if(refreshKey)refresh.current();},[refreshKey]);
  const live=useMemo(()=>graph?liveMetrics(graph,previous):new Map<string,Live>(),[graph,previous]);
  const stale=!!error||!!graph&&tick-received>30000;
  const active=!!graph?.online&&!stale;
  const graphView=useMemo(()=>{
    if(!graph)return null;
    const q=search.trim().toLowerCase();
    const allowed=frontend==='all'?null:related(graph,frontend);
    const matches=graph.nodes.filter(n=>(mode==='all'||n.mode===mode)&&(!allowed||allowed.has(n.id)));
    const ids=new Set<string>();
    if(q){for(const n of matches)if(`${n.name} ${n.proxy} ${n.domain||''} ${n.address} ${n.service}`.toLowerCase().includes(q))for(const id of related(graph,n.id))ids.add(id);}
    else for(const n of matches)ids.add(n.id);
    const filtered=matches.filter(n=>ids.has(n.id));
    // Long configurations remain searchable. Render a bounded subgraph; every
    // capped view includes an explicit notice and keeps only real endpoints.
    const shown=filtered.slice(0,240),visible=new Set(shown.map(n=>n.id));
    const columns=(['frontend','route','backend','server'] as const).map(kind=>shown.filter(n=>n.kind===kind));
    // Keep related pools and targets next to their first incoming route.
    for(const c of [2,3]){
      const preceding=new Map(columns[c-1].map((n,i)=>[n.id,i]));
      const order=(n:TopologyNode)=>Math.min(...graph.edges.filter(e=>e.target===n.id).map(e=>preceding.get(e.source)??1e6),1e6);
      columns[c].sort((a,b)=>order(a)-order(b));
    }
    const height=Math.max(410,...columns.map(c=>c.length*126+96));
    const positions=new Map<string,{x:number;y:number}>();
    columns.forEach((col,c)=>col.forEach((n,i)=>positions.set(n.id,{x:30+c*304,y:72+i*126})));
    return {columns,nodes:shown,edges:graph.edges.filter(e=>visible.has(e.source)&&visible.has(e.target)),positions,height,hidden:filtered.length-shown.length};
  },[graph,mode,frontend,search]);
  const focus=graph&&selected?related(graph,selected):null;
  const detail=graph?.nodes.find(n=>n.id===selected);
  const detailBackend=detail?.kind==='route'?graph?.nodes.find(n=>graph.edges.some(e=>e.source===detail.id&&e.target===n.id)&&n.kind==='backend'):undefined;
  const metricNode=detailBackend||detail;
  const detailLive=metricNode?live.get(metricNode.id):null;
  const hot=graph?.nodes.filter(n=>n.kind==='backend'&&!down(n)&&(live.get(n.id)?.activity||n.metrics.sessions)).sort((a,b)=>(live.get(b.id)?.activity||0)-(live.get(a.id)?.activity||0)||(b.metrics.sessions||0)-(a.metrics.sessions||0)).slice(0,4)||[];
  return <div className="topology-view">
    <div className="topology-intro"><div><span className="topology-eyebrow"><Network size={14}/>TRAFFIC TOPOLOGIE</span><h2>Vom Listener bis zum Zielserver</h2><p>Aktive Konfiguration von {instanceName}. Knoten anklicken, um den Weg und Messwerte zu sehen.</p></div><span className={'topology-live '+(active?'online':'')}><span/>{loading?'Wird geladen':stale?'Daten veraltet':graph?.online?'Live · alle 10 s':'Runtime offline'}</span></div>
    <div className="topology-toolbar"><div className="search"><Search size={16}/><input aria-label="Topologie durchsuchen" placeholder="Domain, Dienst oder Zielserver …" value={search} onChange={e=>setSearch(e.target.value)}/></div><select aria-label="Topologie-Protokoll" value={mode} onChange={e=>setMode(e.target.value)}><option value="all">Alle Protokolle</option><option value="http">HTTP / HTTPS</option><option value="tcp">TCP / TLS</option></select><select aria-label="Topologie-Frontend" value={frontend} onChange={e=>setFrontend(e.target.value)}><option value="all">Alle Frontends</option>{graph?.nodes.filter(n=>n.kind==='frontend').map(n=><option key={n.id} value={n.id}>{n.name}</option>)}</select><button className="button secondary" onClick={()=>setAnimate(!animate)} aria-pressed={!animate}>{animate?<Pause size={14}/>:<Play size={14}/>}Animation {animate?'pausieren':'starten'}</button></div>
    {error&&<div className="notice error">{error} {graph?'Die letzte Topologie bleibt sichtbar; die Animation ist angehalten.':''}</div>}
    <section className="panel topology-panel"><div className="topology-canvas-header"><div className="topology-legend"><span><i className="http"/>HTTP / HTTPS</span><span><i className="tcp"/>TCP</span><span><i className="failed"/>DOWN / MAINT</span><span><i className="structural"/>Zuordnung</span></div><div className="topology-zoom"><button className="icon-button" aria-label="Topologie verkleinern" disabled={zoom<=.5} onClick={()=>setZoom(v=>Math.max(.5,v-.1))}><ZoomOut size={16}/></button><span>{Math.round(zoom*100)} %</span><button className="icon-button" aria-label="Topologie vergrößern" disabled={zoom>=1.25} onClick={()=>setZoom(v=>Math.min(1.25,v+.1))}><ZoomIn size={16}/></button><button className="icon-button" aria-label="Zoom zurücksetzen" onClick={()=>setZoom(.85)}><Maximize2 size={16}/></button></div></div>
    {loading&&!graph?<div className="topology-empty"><RefreshCw className="spin" size={25}/>Topologie wird gelesen …</div>:graphView?.nodes.length?<><div className="topology-scroll" tabIndex={0} aria-label="Topologie-Diagramm, horizontal und vertikal scrollbar"><div className="topology-scaled" style={{width:1246*zoom,height:graphView.height*zoom}}><div className={'topology-stage '+(!animate||!active||!visible?'paused':'')} style={{width:1246,height:graphView.height,transform:`scale(${zoom})`}}>
      <div className="topology-column-labels">{['01 · FRONTENDS','02 · SITES & DIENSTE','03 · BACKEND-POOLS','04 · ZIELSERVER'].map((label,i)=><span key={label} style={{left:30+i*304}}>{label}</span>)}</div>
      <svg className="topology-links" width={1246} height={graphView.height} aria-hidden="true"><defs><marker id={marker} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>{graphView.edges.map(e=>{
        const from=graphView.positions.get(e.source)!,to=graphView.positions.get(e.target)!,target=graphView.nodes.find(n=>n.id===e.target)!;
        const metric=live.get(target.id),intensity=metric?.activity||0,failed=down(target),flow=e.measured&&intensity>0&&!failed&&active;
        const x1=from.x+248,y1=from.y+51,x2=to.x,y2=to.y+51,path=`M ${x1} ${y1} C ${x1+28} ${y1}, ${x2-28} ${y2}, ${x2} ${y2}`;
        const dim=focus&&(!focus.has(e.source)||!focus.has(e.target));
        return <g key={e.id} className={'topology-edge '+(target.mode==='tcp'?'tcp ':'http ')+(failed?'failed ':'')+(dim?'dim':'')}><path d={path} className={e.measured?'measured':'structural'} markerEnd={`url(#${marker})`} style={{strokeWidth:e.measured?1.5+Math.min(3,Math.log10(1+intensity)):1.5}}/>{flow&&<path d={path} className="topology-flow" style={{animationDuration:`${Math.max(.55,3-Math.log10(1+intensity)*.65)}s`,strokeDasharray:`3 ${Math.max(12,55-Math.log10(1+intensity)*10)}`}}/>}{e.measured&&!flow&&active&&!failed&&(target.metrics.sessions||0)>0&&<path d={path} className="topology-session"/>}</g>;
      })}</svg>
      {graphView.nodes.map(n=>{const p=graphView.positions.get(n.id)!,m=live.get(n.id),Icon=n.kind==='server'?Server:n.kind==='route'?Globe2:n.kind==='frontend'?Network:Activity;return <button key={n.id} data-node-id={n.id} data-node-kind={n.kind} className={'topology-node '+n.kind+' '+n.mode+(selected===n.id?' selected':'')+(focus&&!focus.has(n.id)?' dim':'')+(down(n)?' failed':'')} style={{left:p.x,top:p.y}} onClick={()=>setSelected(selected===n.id?null:n.id)} aria-pressed={selected===n.id} aria-label={`${kinds[n.kind]} ${n.name}`}><div className="topology-node-top"><span className="topology-node-icon"><Icon size={16}/></span><span>{n.mode==='http'?'HTTP':n.mode==='tcp'?'TCP':'DIENST'}{n.tls?' · TLS':''}</span>{n.runtime&&<i className={down(n)?'failed':'healthy'} title={state(n)}/>}</div><strong title={n.name}>{n.name}</strong><small title={n.address||n.service}>{n.address||(n.default?'Standardroute':n.dynamic?'Dynamische Auswahl':n.service)}</small><div className="topology-node-bottom">{n.kind==='route'?<span>{n.terminal?'Antwort direkt am Frontend':'Routing-Zuordnung'}</span>:<><span>{active?fmt(m?.rate):'—'} <em>{m?.unit||'Sessions/s'}</em></span><span>{active?fmt(n.metrics.sessions):'—'} <em>aktiv</em></span></>}</div></button>;})}
    </div></div></div>{graphView.hidden>0&&<div className="topology-cap">{graphView.hidden} weitere Knoten. Mit Domain-Suche oder Frontend-Filter eingrenzen.</div>}</>:<div className="topology-empty"><Network size={30}/>{graph?'Keine passenden Dienste oder Listener gefunden.':'Keine Topologie verfügbar.'}</div>}
    <div className="topology-caption"><span><ArrowRight size={15}/>Punkte: gemessener Traffic · Pulsierende Linie: aktive Sessions. Häufigkeit und Linienbreite steigen logarithmisch.</span><span>{graph?'Stand '+new Date(graph.captured_at).toLocaleTimeString('de-DE'):''}</span></div></section>
    <div className="topology-lower"><section className="panel topology-details"><div className="panel-header"><h2>{detail?kinds[detail.kind]+': '+detail.name:'Einen Dienst auswählen'}</h2>{detail&&<button className="icon-button" aria-label="Topologie-Auswahl schließen" onClick={()=>setSelected(null)}><X size={16}/></button>}</div><div className="topology-details-body">{detail?<><p>{detail.service}{detail.address?' · '+detail.address:''}</p>{detailBackend&&<div className="notice">Backend gesamt: {detailBackend.name}. Diese Messwerte umfassen alle Sites und Frontends dieses Pools; pro Domain gibt es hier keinen separaten Zähler.</div>}{detail.terminal&&<p>Dieser Dienst antwortet am Frontend und benötigt keinen Zielserver.</p>}{detail.dynamic&&<p>Das Backend wird dynamisch ausgewählt; eine feste Zuordnung ist nicht verfügbar.</p>}<div className="topology-detail-metrics"><div><strong>{active?fmt(detailLive?.rate):'—'}</strong><span>{detailLive?.unit||'Rate'}{detailLive?.interval?' · Intervallmittel':''}</span></div><div><strong>{active?fmt(metricNode?.metrics.sessions):'—'}</strong><span>Aktive Sessions</span></div><div><strong>{active?bytes(detailLive?.throughput):'—'}</strong><span>Ein + Aus / s · Intervallmittel</span></div></div>{detail.runtime&&<p>Status: <b>{state(detail)}</b>{detail.backup?' · Backup-Server':''}{detail.balance?' · Verteilung: '+detail.balance:''}</p>}{detail.condition&&<p className="mono">Bedingung: {detail.condition}</p>}{detail.configured_address&&detail.configured_address!==detail.address&&<p>Konfiguriertes Ziel: {detail.configured_address}</p>}</>:<p>Klicke eine Domain, einen TCP-Dienst oder einen Server an. Zugehörige Wege werden hervorgehoben; Details und aktuelle Messwerte erscheinen hier.</p>}</div></section><section className="panel topology-hot"><div className="panel-header"><h2>Aktive Backend-Pools</h2><Activity size={17}/></div><div className="topology-hot-body">{active&&hot.length?hot.map(n=><button key={n.id} onClick={()=>setSelected(n.id)}><span><strong>{n.name}</strong><small>{n.mode==='tcp'?'TCP':'HTTP'} · {fmt(live.get(n.id)?.rate)} {live.get(n.id)?.unit} · {fmt(n.metrics.sessions)} aktiv</small></span><span className="topology-hot-bar"><i style={{width:`${Math.max(0,(live.get(n.id)?.activity||0)/(live.get(hot[0].id)?.activity||1)*100)}%`}}/></span></button>):<p>{active?'Noch keine Aktivität gemessen. Durchsatz steht nach zwei Messungen zur Verfügung.':'Live-Messwerte sind derzeit nicht verfügbar.'}</p>}</div></section></div>
    <p className="stats-note">Die Animation verdichtet Messwerte und stellt keine einzelnen Requests dar. Gestrichelte Linien zeigen die Konfiguration. Domains mit gemeinsamem Backend teilen dessen Messwerte. Byte-Zähler können erst bei Verbindungsende steigen; eine pulsierende Linie zeigt aktive Sessions und belegt keinen laufenden Datentransfer. Alle Topologiedaten bleiben im Arbeitsspeicher.</p>
    {graph?.warnings.length? <div className="notice topology-warnings"><strong>Hinweise zur Zuordnung</strong><ul>{graph.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul></div>:null}
  </div>;
}

export type TargetHealth={name:string;address:string;status:string;state:string;checked:boolean;backup:boolean;check_status:string|null;check_code:number|null;check_duration_ms:number|null;check_description:string};
export type Health={backend:string;state:string;reason:string;available:number;total:number;targets:TargetHealth[]};
export type HealthData={online:boolean;captured_at:string;document_version:number;hosts:Record<string,Health>;routes:Record<string,Health>};
export function currentHealth(data:HealthData|null,kind:'hosts'|'routes',id:string,version:number,now:number,error=''):Health {
  const base:Health={backend:'',state:'unknown',reason:'Für diesen Eintrag liegen keine Live-Daten vor.',available:0,total:0,targets:[]};
  if(error)return {...base,reason:error};
  if(!data||data.document_version!==version)return {...base,state:'loading',reason:'Live-Healthchecks werden geladen …'};
  const timestamp=Date.parse(data.captured_at);
  if(!Number.isFinite(timestamp)||now-timestamp>45000||timestamp-now>10000)return {...base,state:'stale',reason:'Die letzte Live-Abfrage ist veraltet. Der aktuelle Dienstzustand ist unbekannt.'};
  return data[kind][id]||base;
}
export const healthLabels:Record<string,{label:string;tone:string}>={
  up:{label:'Erreichbar',tone:'green'},partial:{label:'Teilweise erreichbar',tone:'amber'},
  down:{label:'Ausgefallen',tone:'red'},frontend_down:{label:'Frontend gestoppt',tone:'red'},
  maintenance:{label:'Wartung / Drain',tone:'amber'},unchecked:{label:'Ohne Healthcheck',tone:'neutral'},
  pending:{label:'Noch nicht angewendet',tone:'blue'},disabled:{label:'Deaktiviert',tone:'neutral'},
  local:{label:'Lokale Antwort',tone:'neutral'},unknown:{label:'Unbekannt',tone:'neutral'},
  loading:{label:'Wird geprüft …',tone:'neutral'},stale:{label:'Daten veraltet',tone:'neutral'},
};

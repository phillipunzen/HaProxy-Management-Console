export type AccessKind='internal'|'external'|'restricted'|'partial'|'review';
export type AccessFilter='all'|AccessKind;
type Profile={id:string;name:string;networks:string[]};
type Assignment={access_profile?:string|null;access_paths?:string[];access_policy?:{networks:string[];paths:string[]}|null;path?:string};
export type AccessSummary={kind:AccessKind;label:string;detail:string;networks:string[];paths:string[];profile?:string};
export const accessKinds:{kind:AccessFilter;label:string}[]=[{kind:'all',label:'Alle'},{kind:'internal',label:'Intern'},{kind:'external',label:'Extern möglich'},{kind:'restricted',label:'Eingeschränkt'},{kind:'partial',label:'Teilweise geschützt'},{kind:'review',label:'Prüfen'}];

function ipv4(value:string):bigint|null{
  const parts=value.split('.');
  if(parts.length!==4||parts.some(p=>!/^\d{1,3}$/.test(p)||Number(p)>255))return null;
  return parts.reduce((n,p)=>(n<<8n)+BigInt(p),0n);
}
function ipv6(value:string):bigint|null{
  if(!value.includes(':')||value.includes('%'))return null;
  if(value.includes('.')){
    const last=value.lastIndexOf(':'),address=ipv4(value.slice(last+1));if(address===null)return null;
    value=value.slice(0,last+1)+(address>>16n).toString(16)+':'+(address&65535n).toString(16);
  }
  const compressed=value.split('::');if(compressed.length>2)return null;
  const left=compressed[0]?compressed[0].split(':'):[],right=compressed.length===2&&compressed[1]?compressed[1].split(':'):[];
  const missing=8-left.length-right.length;
  if(compressed.length===1&&left.length!==8||compressed.length===2&&missing<1)return null;
  const parts=compressed.length===2?[...left,...Array(missing).fill('0'),...right]:left;
  if(parts.some(p=>!/^[\da-f]{1,4}$/i.test(p)))return null;
  return parts.reduce((n,p)=>(n<<16n)+BigInt('0x'+p),0n);
}
export function localNetwork(value:string):boolean|null{
  const parts=value.split('/');if(parts.length>2||!parts[0])return null;
  const bits=parts[0].includes(':')?128:32,number=bits===128?ipv6(parts[0]):ipv4(parts[0]);
  if(number===null||parts.length===2&&!/^\d{1,3}$/.test(parts[1]))return null;
  const prefix=parts.length===2?Number(parts[1]):bits;if(prefix>bits)return null;
  const ranges:[bigint,number][]=bits===32?[[0x0a000000n,8],[0xac100000n,12],[0xc0a80000n,16],[0x7f000000n,8],[0xa9fe0000n,16]]:[[0xfc00n<<112n,7],[0xfe80n<<112n,10],[1n,128]];
  return ranges.some(([base,size])=>prefix>=size&&number>>BigInt(bits-size)===base>>BigInt(bits-size));
}
export function summarizeAccess(value:Assignment,profiles:Profile[]=[],manual=false):AccessSummary{
  const profile=value.access_profile?profiles.find(p=>p.id===value.access_profile):undefined;
  const review=(detail:string):AccessSummary=>({kind:'review',label:'Manuell prüfen',detail,networks:[],paths:[]});
  if(value.access_profile&&!profile)return review('Das zugewiesene IP-Filter-Profil fehlt.');
  if(profile&&value.access_policy)return review('Profil und eigene IP-Liste sind gleichzeitig zugewiesen.');
  if(!profile&&!value.access_policy){
    if(value.access_paths?.length)return review('Pfadauswahl ohne zugewiesenes IP-Filter-Profil.');
    return manual?review('Eigene Zugriffsregeln im Konfigurationstext prüfen. Kein zentraler IP-Filter zugewiesen.'):{kind:'external',label:'Extern möglich',detail:'Kein zentraler IP-Filter. Basic Auth und Netzwerkregeln können den Zugriff zusätzlich beschränken.',networks:[],paths:[]};
  }
  const networks=profile?.networks||value.access_policy!.networks,paths=profile?value.access_paths||[]:value.access_policy!.paths||[];
  const local=networks.map(localNetwork);
  if(!networks.length||local.some(v=>v===null))return review('IP-Liste enthält keine gültigen Adressen oder Netze.');
  const base=value.path||'/';
  if(paths.some(p=>!p.startsWith('/')||base!=='/'&&!p.startsWith(base)))return review('Geschützte Pfade passen nicht zum Proxy-Host-Pfad.');
  const whole=paths.length===0||paths.some(p=>base.startsWith(p));
  const detail=(profile?.name||'Eigene IP-Liste')+' · '+(whole?'Gesamter Eintrag':paths.join(', '));
  if(!whole)return {kind:'partial',label:'Teilweise geschützt',detail,networks,paths,profile:profile?.name};
  return {kind:local.every(Boolean)?'internal':'restricted',label:local.every(Boolean)?'Intern · private Netze':'Eingeschränkt',detail,networks,paths,profile:profile?.name};
}

// This is a conservative indicator, not a HAProxy rule evaluator. Generated
// blocks are excluded so clearing a central rule cannot resurrect its old label.
export function manualAccessSections(config?:string|null):Set<string>{
  const result=new Set<string>();let section='',block=false;
  for(const line of (config||'').split('\n')){
    const text=line.trim();
    if(/^# haproxy-control-(?:access|basic-auth|rules) BEGIN /.test(text)){block=true;continue;}
    if(/^# haproxy-control-(?:access|basic-auth|rules) END /.test(text)){block=false;continue;}
    if(block||!text||text.startsWith('#'))continue;
    const header=/^(global|defaults|frontend|backend|listen|resolvers|peers|userlist|cache|program|ring|log-forward|crt-store)\b(?:\s+(\S+))?/.exec(text);
    if(header){section=header[1]==='defaults'?'*':['frontend','backend','listen'].includes(header[1])?(header[2]||'').replace(/^(['"])(.*?)\1$/,'$2'):'';continue;}
    if(section&&(/^(?:http-request|http-after-response)\s+(?:deny|allow|reject|tarpit|return|set-src)\b/.test(text)||/^tcp-request\s/.test(text)||/^(?:acl|http-request|use_backend|default_backend|redirect)\s.*\b(?:src|fc_src|src_port|hdr_ip|req\.hdr_ip)\b/.test(text)))result.add(section);
  }
  return result;
}

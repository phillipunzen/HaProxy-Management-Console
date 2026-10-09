import {useId,useState} from 'react';

export type NamedHost={domain:string;aliases?:string[]};
export const hostnames=(entry:NamedHost)=>[entry.domain,...(entry.aliases||[])];

export function HostnameAliases({value=[],onChange,wildcards=false}:{value?:string[];onChange:(names:string[])=>void;wildcards?:boolean}){
  const id=useId(),[text,setText]=useState(value.join('\n'));
  return <div className="field"><label htmlFor={id}>Weitere Hostnamen</label><textarea id={id} rows={3} maxLength={7800} value={text} onChange={e=>{setText(e.target.value);onChange(e.target.value.split(/[\s,]+/).filter(Boolean));}} placeholder="www.pc-wiki.de"/><small>Optional: pro Zeile ein Name oder mit Komma trennen, bis zu 29 zusätzliche Namen{wildcards?'; auch *.example.com ist möglich':''}. Alle Namen verwenden denselben Backend-Pool, Pfad, Website-Zugang und dieselbe Zertifikatsauswahl.</small></div>;
}

export function HostnameList({entry}:{entry:NamedHost}){
  return (entry.aliases||[]).length>0?<details className="backend-targets"><summary>+ {entry.aliases!.length} weitere Hostnamen</summary>{entry.aliases!.map(name=><small className="mono" key={name}>{name}</small>)}</details>:null;
}

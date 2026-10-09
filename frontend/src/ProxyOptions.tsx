import {useId} from 'react';
import {Plus,Trash2} from 'lucide-react';
import {addOptions,forwardedOptions} from './proxyOptionTemplates';
export function ProxyOptions({value=[],onChange,disabled=false,shared=[]}:{value?:string[]|null;onChange:(options:string[])=>void;disabled?:boolean;shared?:string[]}){
  const id=useId(),options=value||[];
  return <section className="proxy-options-editor">
    <div className="form-section-title"><h3>Proxy-Optionen</h3><small>HTTP-Backend</small></div>
    {shared.length>0&&<div className="notice"><span><strong>{shared.length>1?'Gemeinsamer Backend-Pool:':'Optionen für den Backend-Pool:'}</strong> Änderungen gelten für alle zugeordneten Websites und weitere Listener dieses Pools. In diesem Entwurf: {shared.join(', ')}.</span></div>}
    <p className="stats-note">Eine HAProxy-Direktive pro Zeile. Pfade, Request-/Response-Header, option, no option und Backend-Timeouts lassen sich hinzufügen, ändern oder entfernen. HTTP-Modus, Server und Basic Auth werden separat verwaltet.</p>
    <div className="proxy-options-tools"><button type="button" className="button secondary" disabled={disabled} onClick={()=>onChange(addOptions(options,forwardedOptions))}><Plus size={14}/>Forwarded-Header hinzufügen</button><button type="button" className="button secondary" disabled={disabled} onClick={()=>onChange(addOptions(options,['http-request set-path /reset-password%[path]']))}><Plus size={14}/>Pfad-Präfix als Beispiel</button><button type="button" className="text-button red-text" disabled={disabled||!options.some(line=>line.trim())} onClick={()=>onChange([])}><Trash2 size={14}/>Alle Optionen entfernen</button></div>
    <div className="field"><label htmlFor={id}>Proxy-Optionen (HAProxy)</label><textarea id={id} className="proxy-options-code" disabled={disabled} spellCheck={false} rows={Math.min(12,Math.max(6,options.length+1))} maxLength={40000} value={options.join('\n')} onChange={e=>onChange(e.target.value.split('\n'))} placeholder={'http-request set-path /reset-password%[path]\nhttp-request set-header X-Forwarded-Proto https\noption forwardfor header X-Forwarded-For'} aria-describedby={id+'-hint'}/><small id={id+'-hint'}>Reihenfolge bleibt erhalten. Vor dem Anwenden prüft der Zielserver die Syntax. Optionen aus defaults bleiben geerbt; zum Deaktivieren z. B. no option forwardfor verwenden.</small></div>
  </section>;
}

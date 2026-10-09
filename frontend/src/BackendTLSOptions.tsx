export function BackendTLSOptions({tls,verify=true,onTLS,onVerify,label='TLS'}:{tls:boolean;verify?:boolean|null;onTLS?:(enabled:boolean)=>void;onVerify:(enabled:boolean)=>void;label?:string}){
  return <div className="backend-tls-options">
    {onTLS&&<label className="checkbox"><input type="checkbox" checked={tls} onChange={e=>onTLS(e.target.checked)}/>{label}</label>}
    {tls&&<><label className="checkbox"><input type="checkbox" checked={verify===false} onChange={e=>onVerify(!e.target.checked)}/>Zertifikat nicht prüfen (Insecure verify)</label><small>Für selbstsignierte oder interne Zertifikate. Die Verbindung bleibt TLS-verschlüsselt; das Zielzertifikat wird nicht geprüft.</small></>}
  </div>;
}

export const forwardedOptions=[
  'http-request set-header Host %[req.hdr(host)]',
  'http-request set-header X-Forwarded-Host %[req.hdr(host)]',
  'http-request set-header X-Forwarded-Proto %[ssl_fc,iif(https,http)]',
  'http-request set-header X-Forwarded-Port %[dst_port]',
  'option forwardfor header X-Forwarded-For',
];
function signature(line:string){const tokens=line.trim().split(/\s+/);return tokens[0]==='http-request'&&['set-header','add-header','del-header'].includes(tokens[1])?tokens.slice(0,3).join(' ').toLowerCase():tokens.slice(0,2).join(' ').toLowerCase();}
export function addOptions(existing:string[],added:string[]){
  const keys=new Set(existing.filter(line=>line.trim()&&!line.trim().startsWith('#')).map(signature));
  const result=[...existing];
  for(const line of added){const key=signature(line);if(!keys.has(key)){keys.add(key);result.push(line);}}
  return result;
}

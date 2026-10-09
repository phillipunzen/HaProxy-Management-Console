"""Materialize site-scoped CRT lists with transactional validation/rollback."""
from contextlib import contextmanager
from pathlib import Path,PurePosixPath
import re

from cryptography import x509
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from backend import tls_bindings as plans


def matches(pattern,domain):
    return pattern==domain or pattern.startswith('*.') and not domain.startswith('*.') and domain.endswith(pattern[1:]) and domain.count('.')==pattern.count('.')


def certificate_domains(path):
    if path.stat().st_size>4*1024*1024:raise ValueError('Zertifikatsdatei zu groß.')
    cert=x509.load_pem_x509_certificate(path.read_bytes())
    values=[a.value for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    try:values+=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:pass
    return list(dict.fromkeys(v.lower() for v in values if re.fullmatch(r'(?:\*\.)?[a-zA-Z0-9_.-]{1,253}',v)))


def content(p,plan):
    root=Path(p['cert_dir']).resolve();directory=PurePosixPath(p['cert_dir_config'])
    if plan['directory']!=str(directory).rstrip('/'):raise ValueError('TLS-Plan gehört zu einem anderen Zertifikatsverzeichnis.')
    def local(value):
        path=root/ PurePosixPath(value).relative_to(directory)
        if not path.resolve().is_relative_to(root):raise ValueError('TLS-Datei liegt außerhalb dieses Agent-Profils.')
        return path
    fallback=[]
    for value in plan['fallback']:
        path=local(value)
        if path.is_dir():
            fallback += [str(directory / file.relative_to(root)) for file in sorted(path.glob('*.pem')) if not file.name.startswith('.')]
        else:fallback.append(value)
    fallback=list(dict.fromkeys(fallback));sites=plan['sites']
    selected={site['certificate']:certificate_domains(local(str(directory/(site['certificate']+'.pem')))) for site in sites}
    for site in sites:
        if not any(matches(d,site['domain']) for d in selected[site['certificate']]):raise ValueError('Zertifikat '+site['certificate']+' deckt '+site['domain']+' nicht ab.')
    default=fallback[0] if fallback else str(directory/(sites[0]['certificate']+'.pem'))
    lines=[default+' !*']
    by_cert={}
    for site in sites:by_cert.setdefault(site['certificate'],[]).append(site['domain'])
    for name,domains in by_cert.items():lines.append(str(directory/(name+'.pem'))+' '+' '.join(domains))
    for value in fallback:
        domains=certificate_domains(local(value));filters=[];exclude=[]
        for domain in domains:
            if any(matches(site['domain'],domain) for site in sites):continue
            filters.append(domain)
            if domain.startswith('*.'):exclude += ['!'+site['domain'] for site in sites if not site['domain'].startswith('*.') and matches(domain,site['domain'])]
        if filters:lines.append(value+' '+' '.join(dict.fromkeys(filters+exclude)))
    return ('\n'.join(lines)+'\n').encode()


@contextmanager
def materialize(p,config,atomic,persist=False):
    try:specs=plans.read(config)
    except ValueError as error:raise HTTPException(422,str(error)) from error
    if not specs:yield;return
    try:
        root=Path(p['cert_dir']).resolve();parent=root/'.control-tls'
        if parent.is_symlink() or parent.exists() and (not parent.is_dir() or parent.stat().st_mode & 0o022):raise ValueError('TLS-Listenverzeichnis ist nicht sicher beschreibbar.')
        files=[]
        for spec in specs:
            path=parent/Path(spec['path']).name
            if path.is_symlink():raise ValueError('TLS-Liste darf kein symbolischer Link sein.')
            files.append((path,content(p,spec)))
    except (ValueError,OSError) as error:raise HTTPException(422,'TLS-Zuordnung kann nicht vorbereitet werden: '+str(error)) from error
    created=not parent.exists();parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    if created:parent.chmod(0o755)  # Agent systemd UMask=0077; container must traverse this directory.
    old=[];success=False;attributes=Path(p['config_path']).stat()
    try:
        for path,data in files:
            previous=(path.read_bytes(),path.stat()) if path.exists() else None
            old.append((path,previous))
            atomic(path,data,0o640,p.get('cert_uid',attributes.st_uid),p.get('cert_gid',attributes.st_gid))
        yield
        success=True
    finally:
        if not persist or not success:
            for path,previous in reversed(old):
                if previous:
                    data,st=previous;atomic(path,data,st.st_mode & 0o777,st.st_uid,st.st_gid)
                else:path.unlink(missing_ok=True)
            if created:
                try:parent.rmdir()
                except OSError:pass

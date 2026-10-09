"""Editable HTTP backend directives, preserving expressions and their order."""
import difflib
import shlex

REQUEST_ACTIONS={'set-path','set-pathq','set-uri','set-query','set-method','replace-path','replace-pathq','replace-uri',
                 'set-header','add-header','del-header','replace-header','replace-value','normalize-uri','set-var','unset-var'}
RESPONSE_ACTIONS={'set-header','add-header','del-header','replace-header','replace-value','set-status','set-var','unset-var'}


def editable(tokens):
    if len(tokens)<2:return False
    if tokens[0]=='http-request':return tokens[1] in REQUEST_ACTIONS or tokens[1].startswith(('set-var(','unset-var('))
    if tokens[0] in ('http-response','http-after-response'):return tokens[1] in RESPONSE_ACTIONS or tokens[1].startswith(('set-var(','unset-var('))
    if tokens[0]=='option':return True
    if tokens[:2]==['no','option']:return len(tokens)>2
    return tokens[0]=='timeout' and tokens[1] in {'connect','server','queue','check','tunnel','http-request','http-keep-alive','server-fin'} and len(tokens)==3


def validate(values):
    result=[]
    for line in values:
        if not isinstance(line,str) or len(line)>4000 or any(ord(c)<32 and c!='\t' or ord(c)==127 for c in line):
            raise ValueError('Proxy-Optionen benötigen einzelne Zeilen ohne Steuerzeichen (maximal 4000 Zeichen).')
        line=line.strip()
        if not line or line.startswith('#'):continue
        try:tokens=shlex.split(line,comments=True)
        except ValueError as error:raise ValueError('Ungültige Anführungszeichen in einer Proxy-Option.') from error
        if tokens==['mode','http']:continue # Mode is already managed by the HTTP pool.
        if not editable(tokens):raise ValueError('Proxy-Option nicht unterstützt: HTTP-Pfad-/Header-Regeln, option, no option oder Backend-Timeouts verwenden. Listener, Server, Authentifizierung und Routing separat bearbeiten.')
        if 'txn.mgmt_' in line or 'mgmt_basic_' in line or 'mgmt_access_' in line:
            raise ValueError('Interne Management-Namen sind in Proxy-Optionen reserviert.')
        result.append(line)
    if len(result)>100 or sum(map(len,result))>40000:raise ValueError('Maximal 100 Proxy-Optionen mit zusammen 40000 Zeichen.')
    return result


def option_lines(lines,section):
    """Ignore generated authentication rules; they remain separately managed."""
    result=[];managed=False
    for index in range(section.start+1,section.end):
        text=lines[index].strip()
        if text.startswith('# haproxy-control-basic-auth BEGIN '):managed=True;continue
        if text.startswith('# haproxy-control-basic-auth END '):managed=False;continue
        if managed:continue
        try:tokens=shlex.split(text,comments=True)
        except ValueError:continue
        if editable(tokens):
            try:
                if validate([text]):result.append((index,text))
            except ValueError:pass # Unsupported originals remain untouched in raw config.
    return result


def patch_options(lines,section,desired,patch):
    original=option_lines(lines,section);old=[text for _,text in original]
    ending='\r\n' if lines[section.start].endswith('\r\n') else '\n'
    # Keep unchanged directives in place relative to custom ACL/auth/check rules.
    for tag,i1,i2,j1,j2 in difflib.SequenceMatcher(None,old,desired,autojunk=False).get_opcodes():
        if tag=='equal':continue
        text=''.join('    '+line+ending for line in desired[j1:j2])
        if i1<i2:
            for index,_ in original[i1:i2]:patch[index]=''
            patch[original[i1][0]]=text
        elif i1<len(original):
            index=original[i1][0];patch[index]=text+patch.get(index,lines[index])
        else:
            # New directives follow existing request/auth rules when there is no
            # editable block, so a Host rewrite cannot bypass a legacy challenge.
            index=original[-1][0] if original else section.end-1
            before=patch.get(index,lines[index]);patch[index]=before+(ending if before and not before.endswith('\n') else '')+text

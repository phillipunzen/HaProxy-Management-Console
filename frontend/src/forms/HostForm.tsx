import {
  Activity,
  Check,
  CheckCircle2,
  ChevronRight,
  History,
  Loader2,
  LockKeyhole,
  Plus,
  Trash2,
  XCircle,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api';
import { AccessFilterEditor, type AccessProfile } from '../AccessProfiles';
import { BackendTLSOptions } from '../BackendTLSOptions';
import { BasicAuthSelector, type BasicGroup } from '../BasicAuth';
import { CertificateSelector } from '../Certificates';
import { HostnameAliases } from '../Hostnames';
import { type Layout } from '../ProxyLayout';
import { ProxyOptions } from '../ProxyOptions';
import { Button, Field, Modal } from '../components/ui';
import { date, hostBackend, uid } from '../format';
import { type Host, type Doc, type Cert } from '../types';

/** True if a certificate's domains cover the host name (exact or single-level wildcard). */
export function certificateCovers(cert: Cert, name: string) {
  const host = name.trim().toLowerCase();
  return (cert.domains || []).some((d) => {
    const domain = d.toLowerCase();
    return (
      domain === host ||
      (domain.startsWith('*.') &&
        !host.startsWith('*.') &&
        host.split('.').length === domain.split('.').length &&
        host.endsWith(domain.slice(1)))
    );
  });
}

type TargetResult = { ok: boolean; ms?: number; error?: string };
type HistoryEntry = {
  at: string;
  actor: string;
  event: 'saved' | 'applied';
  action: 'added' | 'changed' | 'removed';
  fields: string[];
};

/** Starting points for new hosts; everything stays editable under "Erweitert". */
const TEMPLATES: { key: string; label: string; hint: string; apply: (h: Host) => Host }[] = [
  {
    key: 'plain',
    label: 'Standard',
    hint: 'Leitet Anfragen unverändert weiter.',
    apply: (h) => ({ ...h, proxy_options: [] }),
  },
  {
    key: 'webapp',
    label: 'Webanwendung',
    hint: 'Übergibt Client-IP und Protokoll (X-Forwarded-For/-Proto), z. B. für Nextcloud oder WordPress.',
    apply: (h) => ({
      ...h,
      proxy_options: ['option forwardfor', 'http-request set-header X-Forwarded-Proto https if { ssl_fc }'],
    }),
  },
  {
    key: 'websocket',
    label: 'WebSocket / Echtzeit',
    hint: 'Wie Webanwendung, hält offene Verbindungen bis zu einer Stunde (Chat, Dashboards, Home Assistant).',
    apply: (h) => ({
      ...h,
      proxy_options: [
        'option forwardfor',
        'http-request set-header X-Forwarded-Proto https if { ssl_fc }',
        'timeout tunnel 1h',
      ],
    }),
  },
  {
    key: 'slow',
    label: 'Lange Anfragen',
    hint: 'Wartet bis zu 5 Minuten auf die Antwort, z. B. für Uploads, Exporte oder Berichte.',
    apply: (h) => ({ ...h, proxy_options: ['option forwardfor', 'timeout server 5m'] }),
  },
  {
    key: 'https-target',
    label: 'Ziel über HTTPS',
    hint: 'Die Anwendung erwartet selbst HTTPS (Port 443) mit gültigem Zertifikat.',
    apply: (h) => ({
      ...h,
      servers: h.servers.map((s) => ({
        ...s,
        port: s.port === 80 ? 443 : s.port,
        tls: true,
        tls_verify: true,
      })),
    }),
  },
];

export function HostForm({
  instanceId,
  manualSections,
  profiles,
  doc,
  fronts,
  certs,
  groups,
  initial,
  busy,
  onClose,
  onSave,
  onHttps,
}: {
  /** Opens the HTTPS assistant for this saved host (closes the form). */
  onHttps?: () => void;
  /** Server whose agent checks the targets; omitted in tests or without a selection. */
  instanceId?: number | null;
  manualSections: Set<string>;
  profiles: AccessProfile[];
  doc: Doc;
  fronts: NonNullable<Layout>['frontends'];
  certs: Cert[];
  groups: BasicGroup[];
  initial: Host | null;
  busy: boolean;
  onClose: () => void;
  onSave: (v: Host, profiles: AccessProfile[]) => Promise<void>;
}) {
  const [profileDrafts, setProfileDrafts] = useState<AccessProfile[]>(() => structuredClone(profiles)),
    [profileEditing, setProfileEditing] = useState(false);
  const [v, setV] = useState<Host>(
      initial
        ? structuredClone(initial)
        : {
            id: uid(),
            frontend:
              doc.imported_config == null && !doc.removed_frontends?.includes('public_http')
                ? 'public_http'
                : fronts.find((f) => f.tls && f.service.includes('Reverse'))?.name ||
                  fronts.find((f) => f.service.includes('Reverse'))?.name ||
                  fronts[0]?.name ||
                  '',
            certificate: null,
            domain: '',
            path: '/',
            enabled: true,
            force_https: false,
            balance: 'roundrobin',
            servers: [{ address: '', port: 80, weight: 100, tls: false }],
          },
    ),
    [error, setError] = useState(''),
    [certTouched, setCertTouched] = useState(!!initial),
    [advanced, setAdvanced] = useState(false),
    [template, setTemplate] = useState('plain'),
    [checks, setChecks] = useState<(TargetResult | null)[] | null>(null),
    [checking, setChecking] = useState(false),
    [checkError, setCheckError] = useState(''),
    [history, setHistory] = useState<HistoryEntry[] | null>(null);
  useEffect(() => {
    if (!initial || !instanceId) return;
    let alive = true;
    api<HistoryEntry[]>(`/instances/${instanceId}/history/hosts/${encodeURIComponent(initial.id)}`)
      .then((h) => alive && setHistory(h))
      .catch(() => alive && setHistory([]));
    return () => {
      alive = false;
    };
  }, [initial, instanceId]);
  const servers = v.servers;
  useEffect(() => setChecks(null), [servers]);
  async function checkTargets() {
    if (!instanceId) return;
    setChecking(true);
    setCheckError('');
    try {
      const filled = v.servers.filter((s) => s.address.trim());
      const { results } = await api<{ results: TargetResult[] }>(
        `/instances/${instanceId}/check-targets`,
        'POST',
        { targets: filled.map((s) => ({ address: s.address.trim(), port: s.port, tls: !!s.tls })) },
      );
      let n = 0;
      setChecks(v.servers.map((s) => (s.address.trim() ? results[n++] : null)));
    } catch (e) {
      setCheckError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }
  const usable = certs.filter((c) => !c.staging && !c.error);
  const setDomain = (domain: string) => {
    // Suggest a matching certificate (and HTTPS redirect) until the user picks one.
    const match = certTouched ? undefined : usable.find((c) => certificateCovers(c, domain));
    setV({
      ...v,
      domain,
      ...(certTouched ? {} : { certificate: match?.name ?? null, force_https: !!match }),
    });
  };
  const defaultFrontend = doc.imported_config == null ? 'public_http' : fronts[0]?.name;
  const advancedActive = [
    (v.frontend || 'public_http') !== (defaultFrontend || 'public_http'),
    v.balance !== 'roundrobin',
    !v.enabled,
    !!v.proxy_options?.length,
    !!(v.access_profile || v.access_policy),
    v.basic_auth_group != null,
  ].filter(Boolean).length;
  return (
    <Modal
      wide
      title={initial ? 'Proxy Host bearbeiten · ' + initial.domain : 'Proxy Host hinzufügen'}
      onClose={onClose}
    >
      <form
        className="form-body"
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy || profileEditing) return;
          setError('');
          try {
            await onSave(v, profileDrafts);
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        <div className="form-grid">
          <Field label="Domain" hint="Auch *.example.com ist möglich.">
            <input
              required
              autoFocus
              value={v.domain}
              maxLength={253}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="app.example.com"
            />
          </Field>
          <Field label="Pfad">
            <input
              required
              value={v.path}
              onChange={(e) => setV({ ...v, path: e.target.value })}
              placeholder="/"
            />
          </Field>
        </div>
        {!initial && (
          <div className="template-picker" role="radiogroup" aria-label="Vorlage">
            <span className="template-label">Vorlage</span>
            {TEMPLATES.map((t) => (
              <button
                key={t.key}
                type="button"
                role="radio"
                aria-checked={template === t.key}
                className={'chip' + (template === t.key ? ' active' : '')}
                title={t.hint}
                onClick={() => {
                  const base = TEMPLATES.find((x) => x.key === template);
                  // Undo the previous template's server change before applying the next one.
                  const reset =
                    base?.key === 'https-target'
                      ? {
                          ...v,
                          servers: v.servers.map((s) => ({
                            ...s,
                            port: s.port === 443 ? 80 : s.port,
                            tls: false,
                          })),
                        }
                      : v;
                  setTemplate(t.key);
                  setV(t.apply(reset));
                }}
              >
                {t.label}
              </button>
            ))}
            <small className="field-note">{TEMPLATES.find((t) => t.key === template)?.hint}</small>
          </div>
        )}
        <HostnameAliases value={v.aliases} wildcards onChange={(aliases) => setV({ ...v, aliases })} />
        <div className="form-section-title">
          <h3>Ziel</h3>
          {instanceId && (
            <button
              className="text-button"
              type="button"
              disabled={checking || !v.servers.some((s) => s.address.trim())}
              title="Der Agent auf dem HAProxy-Server baut eine Verbindung zu jedem Zielserver auf."
              onClick={() => void checkTargets()}
            >
              {checking ? <Loader2 size={14} className="spin" /> : <Activity size={14} />}
              Erreichbarkeit prüfen
            </button>
          )}
          <button
            className="text-button"
            type="button"
            onClick={() =>
              setV({ ...v, servers: [...v.servers, { address: '', port: 80, weight: 100, tls: false }] })
            }
          >
            <Plus size={14} />
            Weiteren Zielserver
          </button>
        </div>
        {checkError && (
          <div className="notice error" role="alert">
            <XCircle size={16} />
            <span>{checkError}</span>
          </div>
        )}
        {v.servers.map((s, i) => (
          <div key={i} className={'backend-form' + (v.servers.length > 1 ? '' : ' single')}>
            {checks?.[i] && (
              <span className={'target-check ' + (checks[i].ok ? 'ok' : 'bad')} role="status">
                {checks[i].ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                {checks[i].ok
                  ? `Erreichbar (${checks[i].ms} ms)`
                  : `Nicht erreichbar: ${checks[i].error ?? 'unbekannter Fehler'}`}
              </span>
            )}
            <Field label="IP / Hostname">
              <input
                required
                value={s.address}
                onChange={(e) =>
                  setV({
                    ...v,
                    servers: v.servers.map((x, j) => (j === i ? { ...x, address: e.target.value } : x)),
                  })
                }
                placeholder="192.168.10.30"
              />
            </Field>
            <Field label="Port">
              <input
                required
                type="number"
                min={1}
                max={65535}
                value={s.port}
                onChange={(e) =>
                  setV({
                    ...v,
                    servers: v.servers.map((x, j) => (j === i ? { ...x, port: Number(e.target.value) } : x)),
                  })
                }
              />
            </Field>
            {v.servers.length > 1 && (
              <Field label="Gewicht">
                <input
                  required
                  type="number"
                  min={1}
                  max={256}
                  value={s.weight}
                  onChange={(e) =>
                    setV({
                      ...v,
                      servers: v.servers.map((x, j) =>
                        j === i ? { ...x, weight: Number(e.target.value) } : x,
                      ),
                    })
                  }
                />
              </Field>
            )}
            <BackendTLSOptions
              tls={s.tls}
              verify={s.tls_verify}
              onTLS={(tls) =>
                setV({
                  ...v,
                  servers: v.servers.map((x, j) => (j === i ? { ...x, tls, tls_verify: true } : x)),
                })
              }
              onVerify={(tls_verify) =>
                setV({ ...v, servers: v.servers.map((x, j) => (j === i ? { ...x, tls_verify } : x)) })
              }
            />
            {v.servers.length > 1 && (
              <button
                className="icon-button red-text"
                title="Zielserver entfernen"
                type="button"
                onClick={() => setV({ ...v, servers: v.servers.filter((_, j) => j !== i) })}
              >
                <Trash2 size={16} />
              </button>
            )}
          </div>
        ))}
        <div className="form-section-title">
          <h3>HTTPS</h3>
        </div>
        {onHttps && initial && !(initial.force_https && initial.certificate) && (
          <div className="notice">
            <LockKeyhole size={16} />
            <span>
              Noch kein eigenes Zertifikat? Der HTTPS-Assistent fordert es an, weist es zu, leitet HTTP auf
              HTTPS weiter und wendet alles an.{' '}
              <button
                type="button"
                className="text-button"
                disabled={busy || JSON.stringify(v) !== JSON.stringify(initial)}
                title={
                  JSON.stringify(v) !== JSON.stringify(initial)
                    ? 'Zuerst die Änderungen speichern oder verwerfen.'
                    : undefined
                }
                onClick={onHttps}
              >
                HTTPS-Assistent öffnen
              </button>
            </span>
          </div>
        )}
        <CertificateSelector
          certs={certs}
          value={v.certificate}
          onChange={(name) => {
            setCertTouched(true);
            setV({ ...v, certificate: name, force_https: name ? v.force_https || !initial : v.force_https });
          }}
        />
        {!certTouched && v.certificate && (
          <small className="field-note">Passendes Zertifikat automatisch ausgewählt.</small>
        )}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={v.force_https}
            onChange={(e) => setV({ ...v, force_https: e.target.checked })}
          />
          HTTP auf HTTPS weiterleiten
        </label>
        <button
          type="button"
          className="disclosure"
          aria-expanded={advanced}
          onClick={() => setAdvanced(!advanced)}
        >
          <ChevronRight size={15} className={advanced ? 'open' : ''} />
          Erweitert
          <small>
            Frontend, Lastverteilung, Header, IP-Filter, Basic Auth
            {advancedActive ? ` · ${advancedActive} angepasst` : ''}
          </small>
        </button>
        <div className="advanced-section" hidden={!advanced}>
          <div className="form-grid">
            <Field
              label="HTTP-Frontend"
              hint={
                v.name
                  ? 'Backend-Pool ' + v.name + ' wird automatisch angelegt.'
                  : 'Der Backend-Pool wird automatisch angelegt und nach der Domain benannt.'
              }
            >
              <select
                required
                value={v.frontend || 'public_http'}
                onChange={(e) => setV({ ...v, frontend: e.target.value })}
              >
                <option value="">Frontend auswählen</option>
                {fronts.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name} · {f.tls ? 'HTTPS' : 'HTTP'} · {f.binds.map((b) => b.split(' ')[0]).join(', ')}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Lastverteilung">
              <select value={v.balance} onChange={(e) => setV({ ...v, balance: e.target.value })}>
                <option value="roundrobin">Round Robin</option>
                <option value="leastconn">Wenigste Verbindungen</option>
                <option value="source">Quell-IP</option>
                <option value="first">First</option>
              </select>
              {v.balance === 'first' && (
                <small>
                  First bevorzugt verfügbare Server nach aufsteigender Server-ID (standardmäßig
                  Listenreihenfolge). Gewichte werden ignoriert. Mit maxconn pro Zielserver wechselt HAProxy
                  bei voller Auslastung zum nächsten Server; bestehende Werte bleiben erhalten. maxconn im
                  Konfigurationseditor setzen.
                </small>
              )}
            </Field>
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={v.enabled}
              onChange={(e) => setV({ ...v, enabled: e.target.checked })}
            />
            Host aktiv
          </label>
          <ProxyOptions
            value={v.proxy_options}
            disabled={busy}
            onChange={(proxy_options) => setV({ ...v, proxy_options })}
          />
          <AccessFilterEditor
            manual={
              manualSections.has('*') ||
              manualSections.has(v.frontend || 'public_http') ||
              manualSections.has(hostBackend(v))
            }
            value={v}
            profiles={profileDrafts}
            onProfilesChange={setProfileDrafts}
            onEditingChange={setProfileEditing}
            basePath={v.path}
            disabled={busy}
            onChange={(assignment) => setV({ ...v, ...assignment })}
          />
          <BasicAuthSelector
            groups={groups}
            value={v.basic_auth_group}
            forward={v.basic_auth_forward}
            onChange={(id) => setV({ ...v, basic_auth_group: id, basic_auth_forward: false })}
            onForward={(forward) => setV({ ...v, basic_auth_forward: forward })}
          />
        </div>
        {initial && history && history.length > 0 && (
          <details className="host-history">
            <summary>
              <History size={14} /> Verlauf ({history.length})
            </summary>
            <ul>
              {history.map((h, n) => (
                <li key={n}>
                  <span className={'history-event ' + h.event}>
                    {h.event === 'applied' ? 'Angewendet' : 'Gespeichert'}
                  </span>
                  <span>
                    {{ added: 'Angelegt', changed: 'Geändert', removed: 'Entfernt' }[h.action]}
                    {h.fields.length > 0 && ': ' + h.fields.join(', ')}
                  </span>
                  <small>
                    {h.actor} · {date(h.at)}
                  </small>
                </li>
              ))}
            </ul>
          </details>
        )}
        <div className="modal-actions">
          <Button secondary type="button" onClick={onClose}>
            Abbrechen
          </Button>
          <Button disabled={busy || profileEditing} type="submit">
            <Check size={16} />
            Speichern
          </Button>
        </div>
      </form>
    </Modal>
  );
}

import { CheckCircle2, Circle, ListChecks, X } from 'lucide-react';
import { useState } from 'react';
import { type AppProps } from '../app/useAppState';

const KEY = 'haproxy-control-checklist-hidden';

function hiddenIds(): number[] {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(value) ? value.filter((v) => typeof v === 'number') : [];
  } catch {
    return [];
  }
}

/**
 * First steps for a newly connected server. Stays on the overview until every step
 * is done (or it is hidden for this server in this browser).
 */
export function SetupChecklist({ app }: AppProps) {
  const { current, online, doc, revisions, changes, canEdit, navigate, setModal, openApply, openHttps } = app;
  const [hidden, setHidden] = useState(hiddenIds);
  if (!current || !doc || !canEdit || hidden.includes(current.id)) return null;
  const sites = doc.hosts.length + (doc.imported_routes?.length ?? 0);
  const https =
    !!doc.tls_enabled ||
    doc.hosts.some((h) => h.certificate) ||
    (doc.imported_routes ?? []).some((r) => r.certificate) ||
    Object.values(doc.frontend_certificates ?? {}).some((v) => v.length > 0);
  const steps = [
    {
      label: 'Agent verbunden',
      done: online.some((i) => i.id === current.id),
      hint: 'Der Agent auf dem HAProxy-Server meldet sich.',
      action: () => navigate('servers'),
    },
    {
      label: 'Konfiguration übernommen',
      done: doc.imported_config != null || revisions.some((r) => r.status === 'applied'),
      hint: 'Bestehende HAProxy-Konfiguration einlesen, damit nichts verloren geht.',
      action: () => setModal('import'),
    },
    {
      label: 'Erste Website eingerichtet',
      done: sites > 0,
      hint: 'Einen Proxy Host für eine Domain anlegen.',
      action: () => navigate('hosts'),
    },
    {
      label: 'HTTPS aktiv',
      done: https,
      hint: 'Der HTTPS-Assistent fordert das Zertifikat an, weist es zu und wendet alles an.',
      action: () => {
        const host = doc.hosts.find((h) => h.enabled && !h.certificate);
        const route = (doc.imported_routes ?? []).find((r) => !r.certificate);
        if (host) openHttps({ kind: 'hosts', key: host.id });
        else if (route) openHttps({ kind: 'imported_routes', key: route.id });
        else navigate('certificates');
      },
    },
    {
      label: 'Alles angewendet',
      done: sites > 0 && changes?.count === 0,
      hint: 'Ausstehende Änderungen prüfen und auf dem Server anwenden.',
      action: () => openApply(),
    },
  ];
  const done = steps.filter((s) => s.done).length;
  if (done === steps.length) return null;
  const next = steps.find((s) => !s.done);
  const hide = () => {
    const value = [...hidden, current.id];
    setHidden(value);
    try {
      localStorage.setItem(KEY, JSON.stringify(value));
    } catch {
      /* storage unavailable – hidden for this session only */
    }
  };
  return (
    <section className="panel setup-checklist" aria-label="Einrichtung">
      <div className="panel-header">
        <div>
          <h2>
            <ListChecks size={17} /> Einrichtung von {current.name}
          </h2>
          <p>
            {done} von {steps.length} Schritten erledigt
          </p>
        </div>
        <button className="icon-button" title="Für diesen Server ausblenden" onClick={hide}>
          <X size={16} />
        </button>
      </div>
      <div
        className="checklist-progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={steps.length}
        aria-valuenow={done}
      >
        <span style={{ width: `${(done / steps.length) * 100}%` }} />
      </div>
      <ol className="checklist">
        {steps.map((s) => (
          <li key={s.label} className={s.done ? 'done' : s === next ? 'next' : ''}>
            {s.done ? <CheckCircle2 size={16} /> : <Circle size={16} />}
            <div>
              <strong>{s.label}</strong>
              {!s.done && <small>{s.hint}</small>}
            </div>
            {s === next && (
              <button className="text-button" onClick={s.action}>
                Jetzt erledigen
              </button>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

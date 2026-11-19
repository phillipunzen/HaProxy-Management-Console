import {
  ArrowRight,
  Check,
  CheckCircle2,
  Download,
  FileClock,
  FileCode2,
  RefreshCw,
  ShieldCheck,
  Square,
} from 'lucide-react';
import { api } from '../api';
import { type AppProps } from '../app/useAppState';
import { Button, Badge, Field, Empty } from '../components/ui';
import { date } from '../format';

export function ConfigView({ app }: AppProps) {
  const {
    selected,
    doc,
    config,
    setConfig,
    active,
    hash,
    revisions,
    rev,
    setRev,
    message,
    setMessage,
    validation,
    setValidation,
    generatedVersion,
    setGeneratedVersion,
    busy,
    resourceReady,
    setConfirm,
    setModal,
    current,
    isAdmin,
    notify,
    run,
    dirty,
    openApply,
    changes,
    generateConfirm,
    service,
    saveDraft,
  } = app;
  if (!selected) return null;
  return (
    <>
      <div className="config-controls">
        <div className="row-actions">
          {changes?.count ? (
            <Button secondary disabled={busy || !resourceReady} onClick={generateConfirm}>
              <FileCode2 size={16} />
              Ausstehende Änderungen in Editor laden
            </Button>
          ) : null}
          <Button
            secondary
            disabled={busy || !resourceReady || !hash}
            onClick={() =>
              run(async () => {
                const result = await api(`/instances/${selected}/validate`, 'POST', {
                  config,
                  base_hash: hash,
                  message,
                });
                setValidation(result.output || 'Konfiguration gültig.');
              }, 'HAProxy-Prüfung erfolgreich.')
            }
          >
            <ShieldCheck size={16} />
            Prüfen
          </Button>
          <Button
            secondary
            disabled={busy || !resourceReady || !hash}
            onClick={() =>
              run(async () => {
                await saveDraft();
              }, 'Als Version gespeichert.')
            }
          >
            <FileClock size={16} />
            Als Version speichern
          </Button>
          <Button
            disabled={busy || !resourceReady || !hash}
            onClick={() =>
              openApply({
                config,
                baseHash: hash,
                revision: rev,
                documentVersion: generatedVersion ?? doc.version,
                message,
              })
            }
          >
            <Check size={16} />
            Prüfen & anwenden …
          </Button>
        </div>
        <span className="spacer" />
        <Badge>{rev ? 'Version #' + rev : config !== active ? 'Bearbeitet' : 'Wie auf dem Server'}</Badge>
        <Button
          secondary
          disabled={busy || !resourceReady || !hash}
          onClick={() =>
            dirty
              ? setConfirm({
                  title: 'Vorhandene Config einlesen',
                  text: 'Der Import verwendet die aktive Serverkonfiguration. Deine ungespeicherten Textänderungen werden dabei verworfen.',
                  fn: async () => setModal('import'),
                })
              : setModal('import')
          }
        >
          <Download size={16} />
          Config einlesen
        </Button>
      </div>
      <Field label="Versionsnotiz">
        <input
          placeholder="z. B. Neue Domain und HTTPS-Redirect"
          value={message}
          maxLength={500}
          onChange={(e) => setMessage(e.target.value)}
        />
      </Field>
      {validation && (
        <div className="notice success">
          <CheckCircle2 size={18} />
          <pre>{validation}</pre>
        </div>
      )}
      <div className="editor-grid">
        <section className="panel editor-panel">
          <div className="panel-header">
            <h2>Aktive Konfiguration</h2>
            <Badge>SERVER</Badge>
          </div>
          <pre className="code-readonly">{active || 'Konfiguration wird geladen …'}</pre>
        </section>
        <section className="panel editor-panel">
          <div className="panel-header">
            <h2>Dein Entwurf</h2>
            <button
              className="text-button"
              onClick={() => {
                const blob = new Blob([config], { type: 'text/plain' });
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = 'haproxy.cfg';
                a.click();
                URL.revokeObjectURL(a.href);
              }}
            >
              <Download size={15} />
              Exportieren
            </button>
          </div>
          <textarea
            spellCheck={false}
            aria-label="HAProxy-Konfigurationsentwurf"
            value={config}
            onChange={(e) => {
              setConfig(e.target.value);
              setRev(null);
              setValidation('');
            }}
          />
        </section>
      </div>
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Versionshistorie</h2>
            <p>Für einen Rollback eine vorherige Version als neuen Entwurf laden.</p>
          </div>
          <FileClock size={19} />
        </div>
        {revisions.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>VERSION</th>
                  <th>NOTIZ</th>
                  <th>BENUTZER</th>
                  <th>DATUM</th>
                  <th>STATUS</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {revisions.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">#{r.id}</td>
                    <td>{r.message || 'Ohne Notiz'}</td>
                    <td>{r.author}</td>
                    <td>{date(r.created_at)}</td>
                    <td>
                      <Badge
                        tone={
                          r.status === 'applied'
                            ? 'green'
                            : r.status === 'failed'
                              ? 'red'
                              : r.status === 'uncertain'
                                ? 'amber'
                                : 'neutral'
                        }
                      >
                        {(
                          {
                            applied: 'Angewendet',
                            draft: 'Entwurf',
                            snapshot: 'Sicherung',
                            failed: 'Fehlgeschlagen',
                            applying: 'Wird angewendet',
                            uncertain: 'Status prüfen',
                          } as Record<string, string>
                        )[r.status] || r.status}
                      </Badge>
                    </td>
                    <td>
                      <button
                        className="text-button"
                        disabled={busy || !resourceReady || !hash}
                        onClick={() =>
                          run(async () => {
                            const old = await api(`/instances/${selected}/revisions/${r.id}`);
                            setConfig(old.config);
                            setGeneratedVersion(null);
                            setRev(null);
                            setValidation('');
                            setMessage('Wiederherstellung von Version #' + r.id);
                            notify(
                              'Version geladen. Aktive Konfiguration bleibt unverändert, bis du den Entwurf anwendest.',
                            );
                          })
                        }
                      >
                        Als Entwurf laden <ArrowRight size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={FileClock}
            title="Noch keine Versionen"
            text="Gespeicherte Entwürfe und Sicherungen vor Änderungen erscheinen hier."
          />
        )}
      </section>
      <div className="service-bar">
        <div>
          <h3>Dienst steuern</h3>
          <p>
            {current?.name} · {current?.kind === 'docker' ? 'Docker-Container' : 'systemd-Dienst'}
          </p>
        </div>
        <div className="row-actions">
          <Button secondary disabled={busy} onClick={() => service('reload')}>
            <RefreshCw size={15} />
            Reload
          </Button>
          <Button secondary disabled={busy} onClick={() => service('start')}>
            Starten
          </Button>
          <Button secondary disabled={busy} onClick={() => service('restart')}>
            Neustart
          </Button>
          {isAdmin && (
            <Button danger secondary disabled={busy} onClick={() => service('stop')}>
              <Square size={13} />
              Stoppen
            </Button>
          )}
        </div>
      </div>
    </>
  );
}

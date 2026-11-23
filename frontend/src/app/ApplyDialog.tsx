import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  MinusCircle,
  XCircle,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { Button, Modal } from '../components/ui';
import {
  type ApplyPreview,
  type ApplyRequest,
  type Instance,
  type PendingChange,
  type SiteCheck,
} from '../types';
import { ChangeIcon } from './ChangesDialog';

/** Short default note from the change list, e.g. "app.example.com hinzugefügt, shop_pool geändert". */
export function describeChanges(items: PendingChange[] | null | undefined) {
  if (!items?.length) return '';
  const verb = { added: 'hinzugefügt', changed: 'geändert', removed: 'entfernt' } as const;
  const parts = items.slice(0, 3).map((c) => `${c.label} ${verb[c.action]}`);
  return parts.join(', ') + (items.length > 3 ? ` und ${items.length - 3} weitere` : '');
}

/**
 * The one place where configuration goes live: shows what changes (summary + diff),
 * the result of `haproxy -c`, takes a version note and applies with reload.
 */
export function ApplyDialog({
  request,
  target,
  onClose,
  onApplied,
  onShowChanges,
  canCheckSites = false,
}: {
  /** The agent can request the changed domains through HAProxy after the reload. */
  canCheckSites?: boolean;
  request: ApplyRequest;
  target: Instance | undefined;
  onClose: () => void;
  /** Refreshes the views; the dialog stays open to show the result and the site check. */
  onApplied: (result: unknown) => Promise<void>;
  /** Offered when the draft cannot be generated, so single changes can be undone. */
  onShowChanges?: () => void;
}) {
  const [preview, setPreview] = useState<ApplyPreview | null>(null),
    [error, setError] = useState(''),
    [message, setMessage] = useState(request.message ?? ''),
    [applying, setApplying] = useState(false),
    [showOutput, setShowOutput] = useState(false),
    [applied, setApplied] = useState(false),
    [checks, setChecks] = useState<SiteCheck[] | null>(null),
    [checkError, setCheckError] = useState('');
  useEffect(() => {
    let alive = true;
    api<ApplyPreview>(
      `/instances/${request.id}/apply-preview`,
      'POST',
      request.config != null ? { config: request.config, base_hash: request.baseHash || null } : {},
    )
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        setMessage((m) => m || describeChanges(p.changes));
      })
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [request]);
  const unchanged = !!preview && preview.diff.hunks.length === 0;
  async function apply() {
    if (!preview) return;
    setApplying(true);
    setError('');
    try {
      const revision =
        request.revision ??
        (
          await api<{ id: number }>(`/instances/${request.id}/revisions`, 'POST', {
            config: preview.config,
            base_hash: preview.base_hash,
            message: message.trim() || 'Konfiguration angewendet',
          })
        ).id;
      const result = await api(`/instances/${request.id}/revisions/${revision}/apply`, 'POST', {
        document_version:
          preview.source === 'draft' ? preview.document_version : (request.documentVersion ?? null),
      });
      setApplied(true);
      setApplying(false);
      void onApplied(result);
      if (canCheckSites) await checkSites();
    } catch (e) {
      setError((e as Error).message);
      setApplying(false);
    }
  }
  async function checkSites() {
    setChecks(null);
    setCheckError('');
    // Only the changed hosts/routes; settings or text changes may affect every domain.
    const items = (preview?.changes ?? [])
      .filter((c) => (c.kind === 'hosts' || c.kind === 'imported_routes') && c.action !== 'removed')
      .map((c) => ({ kind: c.kind, key: c.key }));
    const onlyItems =
      preview?.source === 'draft' &&
      items.length > 0 &&
      (preview.changes ?? []).every((c) => c.kind === 'hosts' || c.kind === 'imported_routes');
    try {
      const { results } = await api<{ results: SiteCheck[] }>(
        `/instances/${request.id}/check-sites`,
        'POST',
        onlyItems ? { items } : {},
      );
      setChecks(results);
    } catch (e) {
      setCheckError((e as Error).message);
    }
  }
  if (applied) {
    const failed = checks?.filter((c) => c.state === 'error').length ?? 0;
    return (
      <Modal wide title={'Angewendet · ' + (target?.name ?? 'Server')} onClose={onClose}>
        <div className="form-body apply-dialog">
          <div className="apply-summary">
            <span className="apply-check ok">
              <CheckCircle2 size={15} /> Konfiguration angewendet, Reload bestätigt
            </span>
          </div>
          {canCheckSites ? (
            <section className="site-checks" aria-live="polite">
              <h3>Test der Domains</h3>
              {!checks && !checkError && (
                <div className="apply-loading">
                  <Loader2 size={16} className="spin" /> Domains werden über HAProxy abgerufen …
                </div>
              )}
              {checkError && (
                <div className="notice error" role="alert">
                  <XCircle size={16} />
                  <span>{checkError}</span>
                </div>
              )}
              {checks && checks.length === 0 && <p className="apply-hint">Keine Domains zum Testen.</p>}
              {checks && checks.length > 0 && (
                <ul className="check-list">
                  {checks.map((c) => (
                    <li key={c.kind + c.key} className={c.state}>
                      {c.state === 'ok' ? (
                        <CheckCircle2 size={15} />
                      ) : c.state === 'warning' ? (
                        <AlertTriangle size={15} />
                      ) : c.state === 'error' ? (
                        <XCircle size={15} />
                      ) : (
                        <MinusCircle size={15} />
                      )}
                      <strong>{c.label}</strong>
                      <span>{c.message}</span>
                      {c.ms != null && <small>{c.ms} ms</small>}
                    </li>
                  ))}
                </ul>
              )}
              {failed > 0 && (
                <p className="apply-hint">
                  <AlertTriangle size={13} /> Die Konfiguration ist aktiv, aber {failed}{' '}
                  {failed === 1 ? 'Domain antwortet' : 'Domains antworten'} nicht. Zielserver und Ports prüfen
                  oder über die Versionshistorie zurückrollen.
                </p>
              )}
            </section>
          ) : (
            <p className="apply-hint">
              Nach einem Agent-Update testet die Verwaltung die geänderten Domains hier automatisch.
            </p>
          )}
        </div>
        <div className="modal-actions">
          {canCheckSites && (checks || checkError) && (
            <Button secondary onClick={() => void checkSites()}>
              Erneut testen
            </Button>
          )}
          <Button onClick={onClose}>Schließen</Button>
        </div>
      </Modal>
    );
  }
  return (
    <Modal wide title={'Änderungen anwenden · ' + (target?.name ?? 'Server')} onClose={onClose}>
      <div className="form-body apply-dialog">
        {!preview && !error && (
          <div className="apply-loading">
            <Loader2 size={18} className="spin" /> Konfiguration wird erzeugt und mit HAProxy geprüft …
          </div>
        )}
        {error && (
          <div className="notice error" role="alert">
            <XCircle size={16} />
            <span>
              {error}
              {!preview && request.config == null && onShowChanges && (
                <>
                  {' '}
                  <button className="text-button" onClick={onShowChanges}>
                    Ausstehende Änderungen ansehen
                  </button>
                </>
              )}
            </span>
          </div>
        )}
        {preview && (
          <>
            <div className="apply-summary">
              {preview.valid ? (
                <span className="apply-check ok">
                  <CheckCircle2 size={15} /> HAProxy-Prüfung bestanden
                </span>
              ) : (
                <span className="apply-check bad">
                  <XCircle size={15} /> HAProxy-Prüfung fehlgeschlagen
                </span>
              )}
              <span className="diff-stat">
                <span className="plus">+{preview.diff.added}</span>
                <span className="minus">−{preview.diff.removed}</span> Zeilen
              </span>
              {preview.output && (
                <button className="text-button" onClick={() => setShowOutput(!showOutput)}>
                  {showOutput ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  Prüfausgabe
                </button>
              )}
            </div>
            {(showOutput || !preview.valid) && preview.output && (
              <pre className={'apply-output' + (preview.valid ? '' : ' bad')}>{preview.output}</pre>
            )}
            {preview.changes && preview.changes.length > 0 && (
              <ul className="change-list compact">
                {preview.changes.map((c) => (
                  <li key={c.kind + c.key}>
                    <ChangeIcon action={c.action} />
                    <span className="change-type">{c.type}</span>
                    <strong>{c.label}</strong>
                  </li>
                ))}
              </ul>
            )}
            {unchanged ? (
              <div className="notice">
                <Check size={16} />
                <span>
                  Die Konfiguration auf dem Server entspricht bereits diesem Stand. Es gibt nichts anzuwenden.
                </span>
              </div>
            ) : (
              <DiffView hunks={preview.diff.hunks} />
            )}
            {!unchanged && (
              <label className="field">
                <span>Versionsnotiz</span>
                <input
                  value={message}
                  maxLength={500}
                  placeholder="Was ändert sich und warum?"
                  onChange={(e) => setMessage(e.target.value)}
                />
              </label>
            )}
            {preview.source === 'draft' && !unchanged && (
              <p className="apply-hint">
                <AlertTriangle size={13} /> Die aktuelle Konfiguration wird vorher als Version gesichert.
                Schlägt der Reload fehl, stellt der Agent sie automatisch wieder her.
              </p>
            )}
          </>
        )}
      </div>
      <div className="modal-actions">
        <Button secondary disabled={applying} onClick={onClose}>
          {unchanged ? 'Schließen' : 'Abbrechen'}
        </Button>
        {!unchanged && (
          <Button disabled={!preview || !preview.valid || applying} onClick={() => void apply()}>
            {applying ? <Loader2 size={15} className="spin" /> : <Check size={15} />}
            Anwenden & neu laden
          </Button>
        )}
      </div>
    </Modal>
  );
}

function DiffView({ hunks }: { hunks: ApplyPreview['diff']['hunks'] }) {
  const lines = useMemo(() => hunks.reduce((n, h) => n + h.lines.length, 0), [hunks]);
  return (
    <div className="diff-view" aria-label={`Unterschiede, ${lines} Zeilen`}>
      {hunks.map((h, index) => (
        <div className="diff-hunk" key={index}>
          <div className="diff-header">{h.header}</div>
          {h.lines.map((line, n) => (
            <div className={'diff-line ' + line.type + (isMeta(line.text) ? ' meta' : '')} key={n}>
              <span className="ln">{line.old ?? ''}</span>
              <span className="ln">{line.new ?? ''}</span>
              <span className="sign">
                {line.type === 'added' ? '+' : line.type === 'removed' ? '−' : ' '}
              </span>
              <code>{isMeta(line.text) ? metaLabel(line.text) : line.text || ' '}</code>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// Encoded bookkeeping comments written by the management; shortened in the diff.
const isMeta = (text: string) => text.startsWith('# haproxy-control-');
const metaLabel = (text: string) => text.split(' ').slice(0, 2).join(' ') + ' … (interne Verwaltungsdaten)';

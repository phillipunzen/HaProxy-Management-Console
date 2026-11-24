import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Loader2,
  LockKeyhole,
  MinusCircle,
  XCircle,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { type ApplyPreview, type HttpsPlan, type SiteCheck } from '../types';
import { Button, Modal } from './ui';

export type HttpsTarget = { kind: 'hosts' | 'imported_routes'; key: string };

type StepState = 'pending' | 'running' | 'done' | 'error' | 'skipped';
type Step = { id: string; label: string; state: StepState; detail?: string };

const NEW = '__new__';

/**
 * One dialog for HTTPS on a proxy host or domain: make the ACME challenge reachable,
 * request the certificate, assign it, redirect HTTP to HTTPS, apply and test.
 * Every step uses the regular endpoints, so validation, snapshots and audit apply.
 */
export function HttpsAssistant({
  instanceId,
  target,
  onClose,
  onChanged,
}: {
  instanceId: number;
  target: HttpsTarget;
  onClose: () => void;
  /** Reloads draft, certificates and pending changes after each saved step. */
  onChanged: (applyResult?: unknown) => Promise<void>;
}) {
  const [plan, setPlan] = useState<HttpsPlan | null>(null),
    [loadError, setLoadError] = useState(''),
    [source, setSource] = useState(''),
    [challenge, setChallenge] = useState<'http' | 'dns'>('http'),
    [credential, setCredential] = useState(''),
    [email, setEmail] = useState(''),
    [name, setName] = useState(''),
    [stagingFirst, setStagingFirst] = useState(false),
    [steps, setSteps] = useState<Step[] | null>(null),
    [running, setRunning] = useState(false),
    [failure, setFailure] = useState(''),
    [checks, setChecks] = useState<SiteCheck[] | null>(null);

  useEffect(() => {
    let alive = true;
    api<HttpsPlan>(`/instances/${instanceId}/https/${target.kind}/${encodeURIComponent(target.key)}`)
      .then((p) => {
        if (!alive) return;
        setPlan(p);
        setSource(p.certificate_ok ? p.certificate! : (p.existing[0]?.name ?? NEW));
        setChallenge(p.challenges.http.available || !p.challenges.dns.available ? 'http' : 'dns');
        setCredential(p.challenges.dns.credentials[0]?.id ?? '');
        setName(p.suggested_name);
        try {
          setEmail(p.suggested_email || localStorage.getItem('haproxy-control-acme-email') || '');
        } catch {
          setEmail(p.suggested_email);
        }
      })
      .catch((e) => alive && setLoadError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [instanceId, target]);

  const issuing = source === NEW;
  const http = issuing && challenge === 'http';
  const planned = useMemo<Step[]>(() => {
    if (!plan) return [];
    const result: Step[] = [];
    if (http && (plan.enable_challenge || !plan.challenge_live))
      result.push({
        id: 'challenge',
        label:
          'Challenge-Weiterleitung ' +
          (plan.enable_challenge ? 'einschalten und ' : '') +
          'anwenden' +
          (plan.challenge_target ? ` (an ${plan.challenge_target})` : ''),
        state: 'pending',
      });
    if (issuing && stagingFirst)
      result.push({ id: 'staging', label: 'Testzertifikat (Staging) anfordern', state: 'pending' });
    if (issuing)
      result.push({
        id: 'issue',
        label: `Zertifikat „${name}“ bei Let’s Encrypt anfordern`,
        state: 'pending',
      });
    const assign = ['Zertifikat zuweisen'];
    if (plan.redirect) assign.push('HTTP auf HTTPS weiterleiten');
    if (plan.enable_tls) assign.push('HTTPS-Listener einschalten');
    result.push({ id: 'assign', label: assign.join(', '), state: 'pending' });
    result.push({ id: 'apply', label: 'Konfiguration prüfen und anwenden', state: 'pending' });
    if (plan.can_check_sites)
      result.push({ id: 'check', label: 'Domain über HTTPS testen', state: 'pending' });
    return result;
  }, [plan, http, issuing, stagingFirst, name]);

  const nameOk = /^[a-zA-Z0-9_-]{1,80}$/.test(name);
  const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const challengeOk =
    !issuing ||
    (challenge === 'http' ? plan?.challenges.http.available : plan?.challenges.dns.available && !!credential);
  const ready = !!plan && plan.blockers.length === 0 && challengeOk && (!issuing || (nameOk && emailOk));

  function update(id: string, state: StepState, detail?: string) {
    setSteps((all) => all?.map((s) => (s.id === id ? { ...s, state, detail } : s)) ?? null);
  }

  async function applyDraft(message: string) {
    const preview = await api<ApplyPreview>(`/instances/${instanceId}/apply-preview`, 'POST', {});
    if (!preview.valid) throw new Error('HAProxy lehnt die Konfiguration ab: ' + preview.output);
    if (preview.diff.hunks.length === 0) return null;
    const { id } = await api<{ id: number }>(`/instances/${instanceId}/revisions`, 'POST', {
      config: preview.config,
      base_hash: preview.base_hash,
      message,
    });
    return api(`/instances/${instanceId}/revisions/${id}/apply`, 'POST', {
      document_version: preview.document_version,
    });
  }

  async function run() {
    if (!plan || !ready) return;
    setSteps(planned);
    setRunning(true);
    setFailure('');
    try {
      localStorage.setItem('haproxy-control-acme-email', email);
    } catch {
      /* storage unavailable */
    }
    const path = `/instances/${instanceId}/https/${plan.kind}/${encodeURIComponent(plan.key)}`;
    const ids = planned.map((s) => s.id);
    let current = '';
    let rollback: Record<string, unknown> | null = null;
    let lastApply: unknown = undefined;
    try {
      let version = plan.version;
      if (ids.includes('challenge')) {
        current = 'challenge';
        update(current, 'running');
        if (plan.enable_challenge)
          version = (
            await api<{ version: number }>(`/instances/${instanceId}/https/prepare-challenge`, 'POST', {
              version,
            })
          ).version;
        lastApply = await applyDraft('HTTPS-Assistent: Challenge-Weiterleitung für ' + plan.label);
        await onChanged(lastApply);
        update(current, 'done');
      }
      const certificate = issuing ? name : source;
      const request = {
        name,
        domains: plan.domains,
        email,
        challenge,
        provider:
          challenge === 'dns'
            ? (plan.challenges.dns.credentials.find((c) => c.id === credential)?.provider ?? '')
            : '',
        engine: 'auto',
        automatic: true,
        ...(challenge === 'dns' ? { dns_credential: credential } : {}),
      };
      for (const staging of ids.includes('staging') ? [true, false] : issuing ? [false] : []) {
        current = staging ? 'staging' : 'issue';
        update(current, 'running', 'Kann bis zu zwei Minuten dauern …');
        await api(`/instances/${instanceId}/certificates/issue`, 'POST', { ...request, staging });
        update(current, 'done');
      }
      current = 'assign';
      update(current, 'running');
      // Applying may advance the draft (imported configs), so read the current state.
      const before = await api<Record<string, unknown> & { version: number }>(
        `/instances/${instanceId}/document`,
      );
      const saved = await api<{ version: number }>(`${path}/assign`, 'POST', {
        certificate,
        version: before.version,
      });
      rollback = { ...before, version: saved.version };
      await onChanged();
      update(current, 'done');
      current = 'apply';
      update(current, 'running');
      lastApply = await applyDraft('HTTPS-Assistent: HTTPS für ' + plan.label);
      rollback = null;
      await onChanged(lastApply);
      update(current, 'done', lastApply ? undefined : 'Keine Änderung nötig – bereits aktiv.');
      if (ids.includes('check')) {
        current = 'check';
        update(current, 'running');
        const { results } = await api<{ results: SiteCheck[] }>(
          `/instances/${instanceId}/check-sites`,
          'POST',
          {
            items: [{ kind: plan.kind, key: plan.key }],
          },
        );
        setChecks(results);
        const bad = results.some((r) => r.state === 'error');
        update(current, bad ? 'error' : 'done', results[0]?.message);
      }
    } catch (e) {
      let message = (e as Error).message;
      if (rollback) {
        try {
          await api(`/instances/${instanceId}/document`, 'PUT', rollback);
          message += ' Die Zuweisung wurde im Entwurf zurückgenommen; das Zertifikat bleibt erhalten.';
        } catch {
          message += ' Die Zuweisung ist im Entwurf gespeichert, aber noch nicht aktiv.';
        }
        await onChanged().catch(() => undefined);
      }
      update(current, 'error', message);
      setSteps((all) => all?.map((s) => (s.state === 'pending' ? { ...s, state: 'skipped' } : s)) ?? null);
      setFailure(message);
    } finally {
      setRunning(false);
    }
  }

  const title = 'HTTPS einrichten' + (plan ? ' · ' + plan.label : '');
  if (steps) {
    const done = !running && !failure;
    return (
      <Modal wide title={title} onClose={() => !running && onClose()}>
        <div className="form-body https-assistant">
          <ol className="assistant-steps" aria-live="polite">
            {steps.map((s) => (
              <li key={s.id} className={s.state}>
                {s.state === 'running' ? (
                  <Loader2 size={16} className="spin" />
                ) : s.state === 'done' ? (
                  <CheckCircle2 size={16} />
                ) : s.state === 'error' ? (
                  <XCircle size={16} />
                ) : s.state === 'skipped' ? (
                  <MinusCircle size={16} />
                ) : (
                  <Circle size={16} />
                )}
                <div>
                  <strong>{s.label}</strong>
                  {s.detail && <small>{s.detail}</small>}
                </div>
              </li>
            ))}
          </ol>
          {done && (
            <div className="notice success" role="status">
              <LockKeyhole size={16} />
              <span>
                {plan!.domains.join(', ')} {plan!.domains.length === 1 ? 'ist' : 'sind'} jetzt per HTTPS
                erreichbar
                {plan!.redirect ? '; HTTP leitet auf HTTPS weiter' : ''}. Das Zertifikat wird automatisch
                erneuert.
              </span>
            </div>
          )}
          {checks?.some((c) => c.state === 'error') && (
            <p className="apply-hint">
              <AlertTriangle size={13} /> HTTPS ist aktiv, aber die Domain antwortet nicht wie erwartet.
              Zielserver prüfen oder über die Versionshistorie zurückrollen.
            </p>
          )}
          {failure && (
            <p className="apply-hint">
              Bereits erledigte Schritte bleiben bestehen. Den Assistenten nach dem Beheben erneut öffnen; er
              setzt dort fort, wo es nötig ist.
            </p>
          )}
        </div>
        <div className="modal-actions">
          <Button disabled={running} onClick={onClose}>
            {running ? 'Läuft …' : 'Schließen'}
          </Button>
        </div>
      </Modal>
    );
  }
  return (
    <Modal wide title={title} onClose={onClose}>
      <form
        className="form-body https-assistant"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        {!plan && !loadError && (
          <div className="apply-loading">
            <Loader2 size={16} className="spin" /> Voraussetzungen werden geprüft …
          </div>
        )}
        {loadError && (
          <div className="notice error" role="alert">
            <XCircle size={16} />
            <span>{loadError}</span>
          </div>
        )}
        {plan && (
          <>
            <p className="assistant-intro">
              Domains: <strong>{plan.domains.join(', ')}</strong>
            </p>
            {plan.blockers.map((b) => (
              <div key={b} className="notice error" role="alert">
                <XCircle size={16} />
                <span>{b}</span>
              </div>
            ))}
            {plan.blockers.length === 0 && (
              <>
                <fieldset className="assistant-choice">
                  <legend>Zertifikat</legend>
                  {plan.existing.map((c) => (
                    <label key={c.name} className="radio-row">
                      <input
                        type="radio"
                        name="https-source"
                        checked={source === c.name}
                        onChange={() => setSource(c.name)}
                      />
                      <span>
                        Vorhandenes Zertifikat <strong>{c.name}</strong>
                        {c.name === plan.certificate ? ' (bereits zugewiesen)' : ''}
                        <small>
                          {c.domains.join(', ')}
                          {c.days_remaining != null ? ` · noch ${c.days_remaining} Tage gültig` : ''}
                        </small>
                      </span>
                    </label>
                  ))}
                  <label className="radio-row">
                    <input
                      type="radio"
                      name="https-source"
                      checked={issuing}
                      onChange={() => setSource(NEW)}
                    />
                    <span>
                      Neues Let’s-Encrypt-Zertifikat anfordern
                      <small>Kostenlos, wird automatisch vor Ablauf erneuert.</small>
                    </span>
                  </label>
                </fieldset>
                {issuing && (
                  <>
                    <fieldset className="assistant-choice">
                      <legend>Bestätigung der Domain</legend>
                      <label className={'radio-row' + (plan.challenges.http.available ? '' : ' disabled')}>
                        <input
                          type="radio"
                          name="https-challenge"
                          disabled={!plan.challenges.http.available}
                          checked={challenge === 'http'}
                          onChange={() => setChallenge('http')}
                        />
                        <span>
                          HTTP-Challenge
                          <small>
                            {plan.challenges.http.reason ||
                              'Die Domain muss per DNS auf diesen HAProxy-Server zeigen und Port 80 aus dem Internet erreichbar sein.'}
                          </small>
                        </span>
                      </label>
                      <label className={'radio-row' + (plan.challenges.dns.available ? '' : ' disabled')}>
                        <input
                          type="radio"
                          name="https-challenge"
                          disabled={!plan.challenges.dns.available}
                          checked={challenge === 'dns'}
                          onChange={() => setChallenge('dns')}
                        />
                        <span>
                          DNS-Challenge
                          <small>
                            {plan.challenges.dns.reason ||
                              'Über den gespeicherten DNS-Zugang; funktioniert auch für Platzhalter-Domains und interne Server.'}
                          </small>
                        </span>
                      </label>
                      {challenge === 'dns' && plan.challenges.dns.available && (
                        <div className="field">
                          <label htmlFor="https-credential">DNS-Zugang</label>
                          <select
                            id="https-credential"
                            value={credential}
                            onChange={(e) => setCredential(e.target.value)}
                          >
                            {plan.challenges.dns.credentials.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.label} · {c.provider}
                              </option>
                            ))}
                          </select>
                        </div>
                      )}
                    </fieldset>
                    <div className="form-grid">
                      <div className="field">
                        <label htmlFor="https-email">E-Mail für Let’s Encrypt</label>
                        <input
                          id="https-email"
                          type="email"
                          required
                          value={email}
                          onChange={(e) => setEmail(e.target.value)}
                        />
                        <small>Für Hinweise zu ablaufenden Zertifikaten.</small>
                      </div>
                      <div className="field">
                        <label htmlFor="https-name">Name des Zertifikats</label>
                        <input
                          id="https-name"
                          className="mono"
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                        />
                        <small>
                          {nameOk ? 'Dateiname auf dem Server.' : 'Nur Buchstaben, Ziffern, _ und -.'}
                        </small>
                      </div>
                    </div>
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={stagingFirst}
                        onChange={(e) => setStagingFirst(e.target.checked)}
                      />
                      <span>
                        Zuerst mit Testzertifikat prüfen
                        <small>
                          Empfohlen, wenn die Einrichtung neu ist: Fehlversuche zählen dann nicht gegen die
                          Let’s-Encrypt-Limits.
                        </small>
                      </span>
                    </label>
                  </>
                )}
                {plan.pending.length > 0 && (
                  <div className="notice warning">
                    <AlertTriangle size={16} />
                    <span>
                      Beim Anwenden werden auch {plan.pending.length} weitere ausstehende{' '}
                      {plan.pending.length === 1 ? 'Änderung' : 'Änderungen'} aktiv:{' '}
                      {plan.pending
                        .slice(0, 5)
                        .map((p) => p.type + ' ' + p.label)
                        .join(', ')}
                      {plan.pending.length > 5 ? ' …' : ''}
                    </span>
                  </div>
                )}
                <section className="assistant-preview">
                  <h3>Das passiert</h3>
                  <ol>
                    {planned.map((s) => (
                      <li key={s.id}>{s.label}</li>
                    ))}
                  </ol>
                  <small>
                    Schlägt ein Schritt fehl, wird die Zuweisung zurückgenommen und nichts weiter geändert.
                  </small>
                </section>
              </>
            )}
          </>
        )}
        <div className="modal-actions">
          <Button type="button" secondary onClick={onClose}>
            Abbrechen
          </Button>
          {plan && plan.blockers.length === 0 && (
            <Button type="submit" disabled={!ready}>
              <LockKeyhole size={15} /> HTTPS einrichten
            </Button>
          )}
        </div>
      </form>
    </Modal>
  );
}

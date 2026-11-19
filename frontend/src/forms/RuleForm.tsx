import { Check } from 'lucide-react';
import { useState } from 'react';
import { type Layout } from '../ProxyLayout';
import { Button, Field, Modal } from '../components/ui';
import { uid } from '../format';
import { type Rule } from '../types';

export function RuleForm({
  initial,
  domainRedirect,
  fronts,
  busy,
  onClose,
  onSave,
}: {
  initial: Rule | null;
  domainRedirect: boolean;
  fronts: NonNullable<Layout>['frontends'];
  busy: boolean;
  onClose: () => void;
  onSave: (v: Rule) => Promise<void>;
}) {
  const [v, setV] = useState<Rule>(
      initial
        ? structuredClone(initial)
        : {
            id: uid(),
            name: domainRedirect ? 'Domain-Weiterleitung' : '',
            enabled: true,
            frontend: fronts.find((f) => f.tls)?.name || fronts[0]?.name || 'public_http',
            match: domainRedirect ? 'host' : 'path_prefix',
            value: '',
            action: 'redirect',
            target: '',
            code: 301,
            preserve_path: domainRedirect,
          },
    ),
    [error, setError] = useState('');
  return (
    <Modal
      title={
        initial
          ? domainRedirect
            ? 'Domain-Weiterleitung bearbeiten'
            : 'Regel bearbeiten'
          : domainRedirect
            ? 'Domain-Weiterleitung erstellen'
            : 'Regel erstellen'
      }
      onClose={onClose}
    >
      <form
        className="form-body"
        onSubmit={async (e) => {
          e.preventDefault();
          setError('');
          try {
            await onSave(v);
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
        <fieldset className="imported-form-fields" disabled={busy}>
          <Field label="Name">
            <input
              required
              maxLength={120}
              value={v.name}
              onChange={(e) => setV({ ...v, name: e.target.value })}
            />
          </Field>
          <Field label="HTTP-/HTTPS-Frontend">
            <select
              required
              value={v.frontend || 'public_http'}
              onChange={(e) => setV({ ...v, frontend: e.target.value })}
            >
              {!fronts.some((f) => f.name === (v.frontend || 'public_http')) && (
                <option value="">Frontend auswählen</option>
              )}
              {fronts.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name} · {f.tls ? 'HTTPS' : 'HTTP'} · {f.binds.map((b) => b.split(' ')[0]).join(', ')}
                </option>
              ))}
            </select>
          </Field>
          {domainRedirect ? (
            <Field label="Quell-Hostname" hint="Beispiel: a.domain.de. Auch *.domain.de ist möglich.">
              <input
                required
                maxLength={253}
                value={v.value}
                onChange={(e) => setV({ ...v, value: e.target.value })}
                placeholder="a.domain.de"
              />
            </Field>
          ) : (
            <div className="form-grid">
              <Field label="Bedingung">
                <select value={v.match} onChange={(e) => setV({ ...v, match: e.target.value })}>
                  <option value="path_prefix">Pfad beginnt mit</option>
                  <option value="host">Hostname ist</option>
                  <option value="source_ip">Quell-IP / Netzwerk</option>
                  <option value="method">HTTP-Methode</option>
                </select>
              </Field>
              <Field label="Wert">
                <input
                  required
                  value={v.value}
                  onChange={(e) => setV({ ...v, value: e.target.value })}
                  placeholder={
                    v.match === 'path_prefix'
                      ? '/alt'
                      : v.match === 'host'
                        ? 'example.com'
                        : v.match === 'source_ip'
                          ? '192.168.1.0/24'
                          : 'POST'
                  }
                />
              </Field>
            </div>
          )}
          {!domainRedirect && (
            <Field label="Aktion">
              <select
                value={v.action}
                onChange={(e) => setV({ ...v, action: e.target.value, preserve_path: false })}
              >
                <option value="redirect">Weiterleiten (Redirect)</option>
                <option value="deny">Zugriff verweigern (403)</option>
                <option value="set_header">Request-Header setzen</option>
              </select>
            </Field>
          )}
          {v.action !== 'deny' && (
            <Field
              label={v.action === 'redirect' ? 'Ziel-URL' : 'Header: Wert'}
              hint={
                v.action === 'redirect'
                  ? v.preserve_path
                    ? 'Protokoll und Ziel-Hostname, optional mit Port: https://b.domain.de'
                    : 'Die Anfrage wird auf genau diese URL weitergeleitet.'
                  : 'Ein Header pro Regel, z. B. X-Forwarded-Proto: https'
              }
            >
              <input
                required
                maxLength={500}
                type={v.action === 'redirect' ? 'url' : 'text'}
                value={v.target}
                onChange={(e) => setV({ ...v, target: e.target.value })}
                placeholder={v.action === 'redirect' ? 'https://b.domain.de' : 'X-Forwarded-Proto: https'}
              />
            </Field>
          )}
          {v.action === 'redirect' && (
            <>
              <Field label="HTTP-Statuscode">
                <select value={v.code} onChange={(e) => setV({ ...v, code: Number(e.target.value) })}>
                  <option value={301}>301 · Permanent</option>
                  <option value={302}>302 · Temporär</option>
                  <option value={307}>307 · Temporär, Methode beibehalten</option>
                  <option value={308}>308 · Permanent, Methode beibehalten</option>
                </select>
              </Field>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={!!v.preserve_path}
                  onChange={(e) => setV({ ...v, preserve_path: e.target.checked })}
                />
                Pfad und Query übernehmen
              </label>
              <p className="stats-note">
                {v.preserve_path
                  ? 'Beispiel: a.domain.de/artikel?id=5 → b.domain.de/artikel?id=5.'
                  : 'Pfad und Query der Anfrage werden durch die feste Ziel-URL ersetzt.'}
              </p>
              <p className="stats-note">
                Der Quell-Hostname muss auf diesen HAProxy zeigen. Für HTTPS muss sein Zertifikat am gewählten
                Frontend verfügbar sein. Eine Weiterleitung benötigt keinen Backend-Server.
              </p>
            </>
          )}
          <label className="checkbox">
            <input
              type="checkbox"
              checked={v.enabled}
              onChange={(e) => setV({ ...v, enabled: e.target.checked })}
            />
            {domainRedirect ? 'Weiterleitung' : 'Regel'} aktivieren
          </label>
          <div className="modal-actions">
            <Button secondary type="button" onClick={onClose}>
              Abbrechen
            </Button>
            <Button disabled={busy} type="submit">
              <Check size={16} />
              Im Entwurf speichern
            </Button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}

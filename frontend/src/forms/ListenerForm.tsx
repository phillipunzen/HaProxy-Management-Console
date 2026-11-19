import { useState } from 'react';
import { Button, Field, Modal } from '../components/ui';
import { type Doc } from '../types';

export function ListenerForm({
  doc,
  busy,
  onClose,
  onSave,
}: {
  doc: Doc;
  busy: boolean;
  onClose: () => void;
  onSave: (v: Doc) => void;
}) {
  const [v, setV] = useState(doc);
  return (
    <Modal title="Listener & HTTP-Challenges" onClose={onClose}>
      <form
        className="form-body"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(v);
        }}
      >
        <div className="form-grid">
          <Field label="HTTP-Port">
            <input
              required
              type="number"
              min={1}
              max={65535}
              value={v.http_port}
              onChange={(e) => setV({ ...v, http_port: Number(e.target.value) })}
            />
          </Field>
          <Field label="Maximale Verbindungen">
            <input
              required
              type="number"
              min={100}
              max={1000000}
              value={v.maxconn}
              onChange={(e) => setV({ ...v, maxconn: Number(e.target.value) })}
            />
          </Field>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={v.tls_enabled}
            onChange={(e) => setV({ ...v, tls_enabled: e.target.checked })}
          />
          HTTPS-Listener aktivieren (Zertifikat erforderlich)
        </label>
        {v.tls_enabled && (
          <Field label="HTTPS-Port">
            <input
              required
              type="number"
              min={1}
              max={65535}
              value={v.https_port}
              onChange={(e) => setV({ ...v, https_port: Number(e.target.value) })}
            />
          </Field>
        )}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={v.acme_enabled}
            onChange={(e) => setV({ ...v, acme_enabled: e.target.checked })}
          />
          HTTP-01-Challenges an Webroot-Dienst weiterleiten
        </label>
        {v.acme_enabled && (
          <>
            <div className="form-grid">
              <Field label="Webroot-Dienst / IP" hint="Aus Sicht des HAProxy-Prozesses erreichbar.">
                <input
                  required
                  value={v.acme_address}
                  onChange={(e) => setV({ ...v, acme_address: e.target.value })}
                />
              </Field>
              <Field label="Webroot-Port">
                <input
                  required
                  type="number"
                  min={1}
                  max={65535}
                  value={v.acme_port}
                  onChange={(e) => setV({ ...v, acme_port: Number(e.target.value) })}
                />
              </Field>
            </div>
            <div className="notice">
              Für HTTP-01 muss Port 80 öffentlich erreichbar sein. Bei Docker ist 127.0.0.1 die
              Container-Adresse; verwende die erreichbare Host-Adresse.
            </div>
          </>
        )}
        <div className="modal-actions">
          <Button secondary type="button" onClick={onClose}>
            Abbrechen
          </Button>
          <Button disabled={busy} type="submit">
            Entwurf speichern
          </Button>
        </div>
      </form>
    </Modal>
  );
}

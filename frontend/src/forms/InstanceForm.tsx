import { Loader2, Network, Terminal } from 'lucide-react';
import { useState } from 'react';
import { type Infrastructure } from '../Infrastructures';
import { ClassificationFields } from '../ServerDirectory';
import { Button, Field, Modal } from '../components/ui';
import { type Instance } from '../types';

export function InstanceForm({
  initial,
  busy,
  onClose,
  onSave,
  infrastructures,
  defaultInfrastructure,
}: {
  infrastructures: Infrastructure[];
  defaultInfrastructure: number | null;
  initial: Instance | null;
  busy: boolean;
  onClose: () => void;
  onSave: (v: any) => void;
}) {
  const [v, setV] = useState({
    infrastructure_id: initial ? initial.infrastructure_id : defaultInfrastructure,
    name: initial?.name ?? '',
    agent_url: initial?.agent_url ?? '',
    profile: initial?.profile ?? '',
    token: '',
    allow_http: initial?.allow_http ?? false,
    notes: initial?.notes ?? '',
    tags: initial?.tags ?? [],
    location: initial?.location ?? '',
    metadata_version: initial?.metadata_version ?? 0,
  });
  return (
    <Modal title={initial ? 'Server bearbeiten' : 'Server verbinden'} onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSave({ ...v, token: v.token || null });
        }}
        className="form-body"
      >
        <div className="notice">
          <Terminal size={18} />
          <span>
            Installiere zuerst den Agenten auf dem HAProxy-Host.{' '}
            <a href="/api/agent-guide" target="_blank" rel="noreferrer">
              Anleitung für Nativ und Docker
            </a>
          </span>
        </div>
        <Field label="Servername">
          <input
            required
            maxLength={120}
            value={v.name}
            onChange={(e) => setV({ ...v, name: e.target.value })}
            placeholder="z. B. Edge Frankfurt"
          />
        </Field>
        <Field label="Agent-Adresse" hint="Basis-URL des Agenten, ohne Profilpfad.">
          <input
            required
            type="url"
            value={v.agent_url}
            onChange={(e) => setV({ ...v, agent_url: e.target.value })}
            placeholder="https://192.168.10.20:9443"
          />
        </Field>
        <Field label="Profilname">
          <input
            required
            pattern="[a-zA-Z0-9_.-]+"
            value={v.profile}
            onChange={(e) => setV({ ...v, profile: e.target.value })}
            placeholder="z. B. docker-edge"
          />
        </Field>
        <Field
          label="Agent-Token"
          hint={
            initial
              ? 'Leer lassen, um den gespeicherten Token beizubehalten. Nur zum Ersetzen ausfüllen.'
              : 'Zugriffstoken aus dem lokalen Agent-Profil.'
          }
        >
          <input
            required={!initial}
            type="password"
            minLength={32}
            maxLength={500}
            autoComplete="off"
            value={v.token}
            onChange={(e) => setV({ ...v, token: e.target.value })}
          />
        </Field>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={v.allow_http}
            onChange={(e) => setV({ ...v, allow_http: e.target.checked })}
          />
          <span>HTTP-Verbindung im privaten Netz zulassen</span>
        </label>
        <ClassificationFields
          infrastructures={infrastructures}
          value={v}
          onChange={(value) => setV({ ...v, ...value })}
        />
        <Field label="Notiz">
          <textarea
            rows={2}
            maxLength={2000}
            value={v.notes}
            onChange={(e) => setV({ ...v, notes: e.target.value })}
          />
        </Field>
        <div className="modal-actions">
          <Button secondary type="button" onClick={onClose}>
            Abbrechen
          </Button>
          <Button disabled={busy} type="submit">
            {busy ? <Loader2 size={16} className="spin" /> : <Network size={16} />}
            {initial ? 'Änderungen speichern' : 'Verbindung prüfen & speichern'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

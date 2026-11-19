import { useState } from 'react';
import { Button, Field, Modal } from '../components/ui';

export function UserForm({
  busy,
  onClose,
  onSave,
}: {
  busy: boolean;
  onClose: () => void;
  onSave: (v: any) => void;
}) {
  const [v, setV] = useState({ username: '', password: '', role: 'viewer' });
  return (
    <Modal title="Benutzer anlegen" onClose={onClose}>
      <form
        className="form-body"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(v);
        }}
      >
        <Field label="Benutzername">
          <input
            required
            minLength={3}
            maxLength={80}
            pattern="[a-zA-Z0-9_.-]+"
            value={v.username}
            onChange={(e) => setV({ ...v, username: e.target.value })}
          />
        </Field>
        <Field label="Startpasswort" hint="Mindestens 10 Zeichen; Änderung beim ersten Login.">
          <input
            required
            type="password"
            minLength={10}
            maxLength={200}
            autoComplete="new-password"
            value={v.password}
            onChange={(e) => setV({ ...v, password: e.target.value })}
          />
        </Field>
        <Field label="Rolle">
          <select value={v.role} onChange={(e) => setV({ ...v, role: e.target.value })}>
            <option value="viewer">Viewer · Statistiken lesen</option>
            <option value="operator">Operator · HAProxy bearbeiten</option>
            <option value="admin">Administrator · Alle Rechte</option>
          </select>
        </Field>
        <div className="modal-actions">
          <Button secondary type="button" onClick={onClose}>
            Abbrechen
          </Button>
          <Button disabled={busy} type="submit">
            Benutzer anlegen
          </Button>
        </div>
      </form>
    </Modal>
  );
}

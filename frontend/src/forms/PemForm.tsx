import { useState } from 'react';
import { Button, Field, Modal } from '../components/ui';

export function PemForm({
  busy,
  onClose,
  onSave,
}: {
  busy: boolean;
  onClose: () => void;
  onSave: (v: any) => void;
}) {
  const [name, setName] = useState(''),
    [pem, setPem] = useState('');
  return (
    <Modal title="PEM-Zertifikat importieren" onClose={onClose}>
      <form
        className="form-body"
        onSubmit={(e) => {
          e.preventDefault();
          onSave({ name, pem });
        }}
      >
        <Field label="Name">
          <input
            required
            pattern="[a-zA-Z0-9_-]+"
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="PEM-Inhalt" hint="Zertifikatskette und passender unverschlüsselter privater Schlüssel.">
          <textarea
            required
            rows={10}
            spellCheck={false}
            className="mono"
            value={pem}
            onChange={(e) => setPem(e.target.value)}
            placeholder="-----BEGIN CERTIFICATE-----"
          />
        </Field>
        <div className="modal-actions">
          <Button secondary type="button" onClick={onClose}>
            Abbrechen
          </Button>
          <Button disabled={busy} type="submit">
            Prüfen & importieren
          </Button>
        </div>
      </form>
    </Modal>
  );
}

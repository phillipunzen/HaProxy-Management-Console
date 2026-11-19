import { Check, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { type Infrastructure } from '../Infrastructures';
import { ClassificationFields } from '../ServerDirectory';
import { Button, Modal } from '../components/ui';
import { type Instance } from '../types';

export function ClassificationForm({
  initial,
  busy,
  onClose,
  onSave,
  infrastructures,
}: {
  infrastructures: Infrastructure[];
  initial: Instance;
  busy: boolean;
  onClose: () => void;
  onSave: (value: any) => void;
}) {
  const [value, setValue] = useState({
    infrastructure_id: initial.infrastructure_id,
    tags: initial.tags,
    location: initial.location,
    metadata_version: initial.metadata_version,
  });
  return (
    <Modal title={'Server-Zuordnung · ' + initial.name} onClose={onClose}>
      <form
        className="form-body"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(value);
        }}
      >
        <p className="wizard-intro">Ordne diesen Server deiner Umgebung und einem Standort zu.</p>
        <ClassificationFields
          infrastructures={infrastructures}
          value={value}
          onChange={(next) => setValue({ ...value, ...next })}
        />
        <div className="modal-actions">
          <Button secondary type="button" disabled={busy} onClick={onClose}>
            Abbrechen
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? <Loader2 size={16} className="spin" /> : <Check size={16} />}Zuordnung speichern
          </Button>
        </div>
      </form>
    </Modal>
  );
}

import { Minus, PencilLine, Plus, Undo2 } from 'lucide-react';
import { Button, Modal } from '../components/ui';
import { type PendingChange, type PendingChanges } from '../types';

const ACTION_LABEL = { added: 'Neu', changed: 'Geändert', removed: 'Entfernt' } as const;

export function ChangeIcon({ action }: { action: PendingChange['action'] }) {
  const Icon = action === 'added' ? Plus : action === 'removed' ? Minus : PencilLine;
  return (
    <span className={'change-icon ' + action} title={ACTION_LABEL[action]}>
      <Icon size={12} />
    </span>
  );
}

/** JetBrains-style "local changes": what is saved but not yet live, with undo per entry. */
export function ChangesDialog({
  changes,
  serverName,
  busy,
  onRevert,
  onRevertAll,
  onApply,
  onClose,
}: {
  changes: PendingChanges;
  serverName: string;
  busy: boolean;
  onRevert: (change: PendingChange) => void;
  onRevertAll: () => void;
  onApply: () => void;
  onClose: () => void;
}) {
  const items = changes.changes;
  return (
    <Modal wide title={'Ausstehende Änderungen · ' + serverName} onClose={onClose}>
      <div className="form-body">
        {!changes.baseline ? (
          <p className="muted">
            Für diesen Server ist noch kein angewendeter Stand bekannt. Öffne „Prüfen & anwenden“, um die
            Unterschiede zur laufenden Konfiguration zu sehen. Nach dem nächsten Anwenden werden Änderungen
            hier einzeln aufgelistet.
          </p>
        ) : items.length === 0 ? (
          <p className="muted">
            Keine ausstehenden Änderungen. Alles Gespeicherte läuft bereits auf dem Server.
          </p>
        ) : (
          <>
            <p className="muted">
              Gespeichert, aber noch nicht auf dem Server aktiv. Einzelne Änderungen lassen sich hier
              verwerfen.
            </p>
            <ul className="change-list">
              {items.map((c) => (
                <li key={c.kind + c.key}>
                  <ChangeIcon action={c.action} />
                  <span className="change-type">{c.type}</span>
                  <strong>{c.label}</strong>
                  <span className="change-action">{ACTION_LABEL[c.action]}</span>
                  <button
                    className="icon-button"
                    title="Diese Änderung verwerfen"
                    aria-label={'Änderung an ' + c.label + ' verwerfen'}
                    disabled={busy}
                    onClick={() => onRevert(c)}
                  >
                    <Undo2 size={15} />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      <div className="modal-actions">
        {changes.baseline && items.length > 0 && (
          <Button secondary danger disabled={busy} onClick={onRevertAll}>
            <Undo2 size={15} />
            Alle verwerfen
          </Button>
        )}
        <span className="spacer" />
        <Button secondary disabled={busy} onClick={onClose}>
          Schließen
        </Button>
        <Button disabled={busy || (changes.baseline && items.length === 0)} onClick={onApply}>
          Prüfen & anwenden …
        </Button>
      </div>
    </Modal>
  );
}

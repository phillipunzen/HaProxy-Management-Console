import { Download, Plus, Server, Terminal } from 'lucide-react';
import { ServerDirectory } from '../ServerDirectory';
import { api } from '../api';
import { type AppProps } from '../app/useAppState';
import { Button, Status, Empty } from '../components/ui';

export function ServersView({ app }: AppProps) {
  const {
    setSelected,
    busy,
    setInstanceEdit,
    setUpdateCommand,
    scoped,
    setConfirm,
    setModal,
    isAdmin,
    run,
    navigate,
    loadInstances,
    online,
    notify,
  } = app;
  return (
    <>
      <div className="section-summary">
        <span>
          <b>{scoped.length}</b> Instanzen
        </span>
        <span>
          <span className="status-dot" />
          <b>{online.length}</b> erreichbar
        </span>
        <span>Docker & native Installation</span>
      </div>
      {scoped.length ? (
        <ServerDirectory
          servers={scoped}
          isAdmin={isAdmin}
          busy={busy}
          status={(i) => <Status instance={scoped.find((s) => s.id === i.id)!} />}
          onManage={(id) => {
            setSelected(id);
            navigate('statistics');
          }}
          onEdit={(id) => {
            setInstanceEdit(scoped.find((i) => i.id === id)!);
            setModal('instance');
          }}
          onMetadata={(id) => {
            setInstanceEdit(scoped.find((i) => i.id === id)!);
            setModal('instance-metadata');
          }}
          onUpdate={(id) =>
            run(async () => {
              const result = await api(`/instances/${id}/agent-update`, 'POST');
              setUpdateCommand(result.command);
              setModal('agent-update');
            })
          }
          onRotateToken={(id) =>
            setConfirm({
              title: 'Token erneuern',
              text: 'Neues Agent-Token erzeugen? Der Agent übernimmt das neue Token sofort; das alte wird ungültig.',
              danger: true,
              fn: async () => {
                await api(`/instances/${id}/rotate-token`, 'POST');
                notify('Agent-Token wurde erneuert.');
              },
            })
          }
          onRemove={(id) => {
            const i = scoped.find((s) => s.id === id)!;
            setConfirm({
              title: 'Server aus Verwaltung entfernen',
              text:
                'Die Verbindung zu ' +
                i.name +
                ' und die gespeicherte Historie werden gelöscht. Der HAProxy-Dienst läuft weiter.',
              danger: true,
              fn: async () => {
                await api(`/instances/${id}`, 'DELETE');
                await loadInstances();
              },
            });
          }}
        />
      ) : (
        <section className="panel">
          <Empty
            icon={Server}
            title="Noch keine Server verbunden"
            text="Ein Agent kann mehrere HAProxy-Profile auf einem Server verwalten. Du benötigst seine Adresse, einen Profilnamen und den Zugriffstoken."
            action={
              isAdmin ? (
                <Button
                  onClick={() => {
                    setInstanceEdit(null);
                    setModal('instance');
                  }}
                >
                  <Plus size={16} />
                  Server verbinden
                </Button>
              ) : undefined
            }
          />
        </section>
      )}
      <section className="setup-card">
        <div className="tile-icon">
          <Terminal size={23} />
        </div>
        <div>
          <h3>Ein Agent pro HAProxy-Server</h3>
          <p>
            Der Agent übernimmt Konfigurationsprüfung, Reloads, Runtime-Statistiken und Certbot. Profile legen
            lokal fest, welche Dienste und Pfade verwaltet werden dürfen.
          </p>
          <div className="setup-downloads">
            {isAdmin && (
              <>
                <a className="text-button" href="/api/agent-guide">
                  <Download size={14} />
                  Einrichtungsanleitung
                </a>
                <a className="text-button" href="/api/agent-config">
                  <Download size={14} />
                  Profilvorlage mit neuen Tokens
                </a>
              </>
            )}
          </div>
        </div>
      </section>
    </>
  );
}

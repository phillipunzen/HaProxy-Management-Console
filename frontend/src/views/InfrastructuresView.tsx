import { InfrastructureManager } from '../Infrastructures';
import { api } from '../api';
import { type AppProps } from '../app/useAppState';

export function InfrastructuresView({ app }: AppProps) {
  const {
    busy,
    setBusy,
    infrastructures,
    scope,
    setScope,
    scopeRef,
    setConfirm,
    isAdmin,
    notify,
    navigate,
    chooseScope,
    loadInstances,
  } = app;
  return (
    <InfrastructureManager
      items={infrastructures}
      isAdmin={isAdmin}
      busy={busy}
      onOpen={(id) => {
        chooseScope(String(id));
        navigate('servers');
      }}
      onSave={async (value) => {
        setBusy(true);
        try {
          await api('/infrastructures' + (value.id ? '/' + value.id : ''), value.id ? 'PUT' : 'POST', value);
          await loadInstances();
          notify('Infrastruktur gespeichert.');
        } catch (e) {
          notify((e as Error).message, true);
          throw e;
        } finally {
          setBusy(false);
        }
      }}
      onRemove={(value) =>
        setConfirm({
          title: 'Infrastruktur löschen',
          text: 'Die leere Infrastruktur ' + value.name + ' wird entfernt.',
          danger: true,
          fn: async () => {
            await api('/infrastructures/' + value.id, 'DELETE');
            if (scope === String(value.id)) {
              setScope('');
              scopeRef.current = '';
            }
            await loadInstances();
          },
        })
      }
    />
  );
}

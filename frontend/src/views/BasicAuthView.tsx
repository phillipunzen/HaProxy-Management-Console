import { BasicAuthManager } from '../BasicAuth';
import { api } from '../api';
import { type AppProps } from '../app/useAppState';

export function BasicAuthView({ app }: AppProps) {
  const { busy, authDirectory, setConfirm, isAdmin, loadBasicAuth, openApply } = app;
  return (
    <BasicAuthManager
      data={authDirectory}
      isAdmin={isAdmin}
      busy={busy}
      request={api}
      onChanged={loadBasicAuth}
      onConfirm={(title, text, fn) => setConfirm({ title, text, fn })}
      onGenerate={(id) => openApply({ id, message: 'Basic-Auth-Zugänge aktualisiert' })}
    />
  );
}

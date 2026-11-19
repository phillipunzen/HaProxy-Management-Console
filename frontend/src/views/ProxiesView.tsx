import { ProxyLayout } from '../ProxyLayout';
import { type AppProps } from '../app/useAppState';

export function ProxiesView({ app }: AppProps) {
  const {
    selected,
    doc,
    busy,
    setHostEdit,
    setImportedBackendEdit,
    layout,
    setFrontendEdit,
    setPoolEdit,
    setCertificateFrontend,
    resourceReady,
    setModal,
    canEdit,
    notify,
    navigate,
    removeBackend,
    removeFrontend,
  } = app;
  if (!selected) return null;
  return (
    <ProxyLayout
      removableFrontends={(layout?.frontends || []).map((f) => f.name)}
      removableBackends={(layout?.backends || [])
        .filter(
          (b) =>
            doc.imported_config != null ||
            doc.backends?.some((p) => p.name === b.name) ||
            doc.hosts.some((h) => 'backend_' + h.id === b.name),
        )
        .map((b) => b.name)}
      layout={layout}
      busy={busy || !resourceReady}
      canEdit={canEdit}
      onFrontend={() => {
        setFrontendEdit(null);
        setModal('frontend');
      }}
      onBackend={() => {
        setPoolEdit(null);
        setModal('backend');
      }}
      onCertificates={(name) => {
        setCertificateFrontend(name);
        setModal('frontend-certificates');
      }}
      onEditFrontend={(name) => {
        const f = doc.frontends?.find((f) => f.name === name);
        if (f) {
          setFrontendEdit(f);
          setModal('frontend');
        } else if (name === 'public_http' && doc.imported_config == null) setModal('listeners');
        else if (layout?.frontends.find((f) => f.name === name)?.tls) {
          setCertificateFrontend(name);
          setModal('frontend-certificates');
        } else {
          navigate('config');
          notify('Konfigurationseditor für den übernommenen Listener ' + name + ' geöffnet.');
        }
      }}
      onEditBackend={(name) => {
        const p = doc.backends?.find((b) => b.name === name),
          old = doc.imported_backends?.find((b) => b.name === name),
          h = doc.hosts.find((h) => 'backend_' + h.id === name);
        if (p) {
          setPoolEdit(p);
          setModal('backend');
        } else if (old) {
          setImportedBackendEdit(old);
          setModal('imported-backend');
        } else if (h) {
          setHostEdit(h);
          setModal('host');
        } else {
          navigate('config');
          notify('Konfigurationseditor für den bestehenden Pool ' + name + ' geöffnet.');
        }
      }}
      onRemoveFrontend={(name) => void removeFrontend(name)}
      onRemoveBackend={(name) => void removeBackend(name)}
      onHosts={() => navigate('hosts')}
    />
  );
}

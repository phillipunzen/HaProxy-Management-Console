import { Download, Globe2, LockKeyhole, Plus, Search, Settings2, Trash2 } from 'lucide-react';
import { AccessBadge, AccessFilters } from '../AccessVisibility';
import { ImportedConfigPanel } from '../ConfigImport';
import { HostnameList } from '../Hostnames';
import { ProxyHealthCell, ProxyHealthSummary } from '../ProxyHealth';
import { type AppProps } from '../app/useAppState';
import { Button, Badge, Empty } from '../components/ui';

export function HostsView({ app }: AppProps) {
  const {
    selected,
    doc,
    cap,
    search,
    setSearch,
    busy,
    setHostEdit,
    accessFilter,
    setAccessFilter,
    setImportedBackendEdit,
    setImportedRouteEdit,
    setImportedRouteIndex,
    authDirectory,
    resourceReady,
    proxyHealth,
    setModal,
    navigate,
    saveDoc,
    saveImportedFields,
    removeBackend,
    confirmDelete,
    hostAccess,
    routeAccess,
    visibleRoute,
    filteredHosts,
  } = app;
  if (!selected) return null;
  return (
    <>
      <div className="toolbar">
        <div className="search">
          <Search size={16} />
          <input
            placeholder="Domain oder Backend suchen …"
            aria-label="Hosts durchsuchen"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
      <AccessFilters
        value={accessFilter}
        onChange={setAccessFilter}
        summaries={[...Object.values(hostAccess), ...Object.values(routeAccess)]}
      />
      <ProxyHealthSummary view={proxyHealth} hosts={doc.hosts} routes={doc.imported_routes || []} />
      {cap && !cap.tls_site_bindings && (
        <div className="notice">
          <span>
            Für die gezielte Zertifikatsauswahl je Reverseproxy den Agenten auf diesem HAProxy-Server
            aktualisieren.{' '}
            <button className="text-button" onClick={() => navigate('servers')}>
              Server und Agent-Update öffnen
            </button>
          </span>
        </div>
      )}
      {doc.imported_config != null && (
        <ImportedConfigPanel
          visibleRoute={visibleRoute}
          accessSummaries={routeAccess}
          health={proxyHealth}
          groups={authDirectory?.groups || []}
          doc={doc}
          busy={busy || !resourceReady}
          onSave={saveImportedFields}
          onRemoveBackend={(name) => void removeBackend(name)}
          onEditBackend={(value) => {
            setImportedBackendEdit(value);
            setModal('imported-backend');
          }}
          onEditRoute={(value, index) => {
            setImportedRouteEdit(value);
            setImportedRouteIndex(index);
            setModal('imported-route');
          }}
          onImport={() => setModal('import')}
        />
      )}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>{doc.imported_config != null ? 'Zusätzliche Proxy Hosts' : 'Proxy Hosts'}</h2>
            <p>Backend-Pools entstehen automatisch; HTTP-Frontend und Zertifikat wählst du am Host.</p>
          </div>
        </div>
        {filteredHosts.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>DOMAIN / PFAD</th>
                  <th>BACKEND</th>
                  <th>SSL</th>
                  <th>ZUGRIFF</th>
                  <th>ENTWURF</th>
                  <th>HEALTHCHECK</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filteredHosts.map((h) => (
                  <tr key={h.id}>
                    <td>
                      <div className="table-primary">
                        <span className="small-icon">
                          <Globe2 size={18} />
                        </span>
                        <div>
                          <strong>{h.domain}</strong>
                          <HostnameList entry={h} />
                          <small className="mono">
                            {h.frontend || 'public_http'} · {h.path}
                          </small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="mono">
                        {h.servers[0].address}:{h.servers[0].port}
                      </span>
                      {h.servers.length > 1 && (
                        <small>
                          + {h.servers.length - 1} Backends · {h.balance}
                        </small>
                      )}
                    </td>
                    <td>
                      <Badge tone={h.force_https ? 'green' : 'neutral'}>
                        {h.force_https ? <LockKeyhole size={12} /> : null}
                        {h.force_https ? 'HTTPS' : 'HTTP'}
                      </Badge>
                      {h.basic_auth_group && (
                        <small>
                          <LockKeyhole size={11} />{' '}
                          {authDirectory?.groups.find((g) => g.id === h.basic_auth_group)?.name ||
                            'Basic Auth'}
                        </small>
                      )}
                    </td>
                    <td>
                      <AccessBadge summary={hostAccess[h.id]} />
                    </td>
                    <td>
                      <Badge tone={h.enabled ? 'green' : 'neutral'}>
                        {h.enabled ? 'Aktiv' : 'Deaktiviert'}
                      </Badge>
                    </td>
                    <td>
                      <ProxyHealthCell view={proxyHealth} kind="hosts" id={h.id} />
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="icon-button"
                          title="Bearbeiten"
                          onClick={() => {
                            setHostEdit(h);
                            setModal('host');
                          }}
                        >
                          <Settings2 size={16} />
                        </button>
                        <button
                          className="icon-button red-text"
                          title="Löschen"
                          onClick={() =>
                            confirmDelete('Proxy Host ' + h.domain + ' löschen', async () =>
                              saveDoc({
                                ...doc,
                                hosts: doc.hosts.filter((x) => x.id !== h.id),
                              }),
                            )
                          }
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={Globe2}
            title={search || accessFilter !== 'all' ? 'Keine passenden Hosts' : 'Dein erster Proxy Host'}
            text="Leite Domains und Pfade an einen oder mehrere Zielserver weiter. Läuft auf dem Server schon eine Konfiguration, lies sie zuerst ein – vorhandene Domains erscheinen dann hier."
            action={
              !search &&
              accessFilter === 'all' && (
                <div className="row-actions">
                  {doc.imported_config == null && (
                    <Button secondary disabled={!resourceReady} onClick={() => setModal('import')}>
                      <Download size={16} />
                      Vorhandene Config einlesen
                    </Button>
                  )}
                  <Button disabled={!resourceReady} onClick={() => setModal('host')}>
                    <Plus size={16} />
                    Proxy Host hinzufügen
                  </Button>
                </div>
              )
            }
          />
        )}
      </section>
    </>
  );
}

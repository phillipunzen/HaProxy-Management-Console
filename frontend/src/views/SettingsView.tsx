import { KeyRound, LockKeyhole, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { api } from '../api';
import { type AppProps } from '../app/useAppState';
import { Button, Badge } from '../components/ui';
import { fmt, bytes } from '../format';

export function SettingsView({ app }: AppProps) {
  const {
    user,
    users,
    setUsers,
    busy,
    metricStorage,
    setMetricStorage,
    setConfirm,
    setModal,
    isAdmin,
    notify,
  } = app;
  return (
    <>
      <section className="panel">
        <div className="panel-header">
          <h2>Dein Konto</h2>
          <LockKeyhole size={18} />
        </div>
        <div className="settings-body">
          <div className="account-summary">
            <span className="avatar large">{user.username.slice(0, 2).toUpperCase()}</span>
            <div>
              <h3>{user.username}</h3>
              <p>
                {user.role === 'admin'
                  ? 'Administrator'
                  : user.role === 'operator'
                    ? 'Operator'
                    : 'Leserechte'}
              </p>
            </div>
          </div>
          <Button secondary onClick={() => setModal('password')}>
            <KeyRound size={16} />
            Passwort ändern
          </Button>
        </div>
      </section>
      {isAdmin && (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Benutzer & Rollen</h2>
              <p>
                Administratoren verwalten Zugänge. Operatoren bearbeiten HAProxy. Viewer lesen Statistiken.
              </p>
            </div>
            <Button onClick={() => setModal('user')}>
              <Plus size={16} />
              Benutzer
            </Button>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>BENUTZER</th>
                  <th>ROLLE</th>
                  <th>PASSWORT</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <strong>{u.username}</strong>
                      {u.id === user.id && <small>Dein Konto</small>}
                    </td>
                    <td>
                      <Badge>{u.role}</Badge>
                    </td>
                    <td>
                      {u.must_change_password ? 'Änderung beim nächsten Login' : 'Persönliches Passwort'}
                    </td>
                    <td>
                      {u.id !== user.id && (
                        <button
                          className="icon-button red-text"
                          title="Benutzer löschen"
                          onClick={() =>
                            setConfirm({
                              title: 'Benutzer löschen',
                              text:
                                'Der Zugang für ' + u.username + ' und alle seine Sitzungen werden entfernt.',
                              danger: true,
                              fn: async () => {
                                await api(`/users/${u.id}`, 'DELETE');
                                setUsers(await api('/users'));
                              },
                            })
                          }
                        >
                          <Trash2 size={16} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {isAdmin && metricStorage && (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Metrikspeicher</h2>
              <p>Kompakte Verlaufswerte mit automatischer Aufbewahrung.</p>
            </div>
            <div className="row-actions">
              <Badge>{fmt(Object.values(metricStorage.buckets).reduce((a, b) => a + b, 0))} Intervalle</Badge>
              {metricStorage.allocated_bytes != null && (
                <Badge>ca. {bytes(metricStorage.allocated_bytes)}</Badge>
              )}
            </div>
          </div>
          <div className="settings-body">
            <div className="metric-storage-grid">
              {[
                [
                  '30-Sekunden-Intervalle',
                  metricStorage.retention.raw_hours + ' Stunden',
                  metricStorage.buckets['30'],
                ],
                [
                  '5-Minuten-Intervalle',
                  metricStorage.retention.fine_days + ' Tag(e)',
                  metricStorage.buckets['300'],
                ],
                ['Stundenwerte', metricStorage.retention.total_days + ' Tage', metricStorage.buckets['3600']],
              ].map(([label, retention, count]) => (
                <div key={label}>
                  <strong>{label}</strong>
                  <span>
                    {retention} · {fmt(Number(count))} Datensätze
                  </span>
                </div>
              ))}
            </div>
            <p className="stats-note">
              Sammlung alle {metricStorage.interval_seconds} s. Höchstens ungefähr{' '}
              {fmt(metricStorage.max_buckets_per_instance)} historische Intervalle pro Instanz nach der
              Bereinigung. Live-Details werden bei Bedarf vom Agenten geladen. Aufbewahrung über die
              METRICS-Variablen in .env einstellen.
            </p>
            {metricStorage.legacy_rows > 0 && (
              <div className="notice">
                {fmt(metricStorage.legacy_rows)} alte Metrikdatensätze werden im Hintergrund übernommen und
                durch kompakte Intervalle ersetzt. Aktualisieren zeigt den Fortschritt.
              </div>
            )}
            {metricStorage.legacy_rows === 0 && (metricStorage.legacy_table_bytes || 0) > 1024 * 1024 && (
              <div className="notice">
                <div>
                  <p>
                    Die alte Metriktabelle ist leer, belegt laut MariaDB aber noch ca.{' '}
                    {bytes(metricStorage.legacy_table_bytes!)}. Freien Speicher nach der Übernahme
                    zurückgeben.
                  </p>
                  <Button
                    secondary
                    disabled={busy}
                    onClick={() =>
                      setConfirm({
                        title: 'Alten Metrikspeicher freigeben',
                        text: 'MariaDB optimiert die leere alte Metriktabelle. Der kompakte Verlauf bleibt erhalten. Die Optimierung kann kurzzeitig Datenbanksperren benötigen.',
                        fn: async () => {
                          setMetricStorage(await api('/metrics/storage/compact', 'POST'));
                          notify('MariaDB-Optimierung abgeschlossen.');
                        },
                      })
                    }
                  >
                    Speicher freigeben
                  </Button>
                </div>
              </div>
            )}
          </div>
        </section>
      )}
      <div className="notice">
        <ShieldCheck size={18} />
        <span>
          HTTP-Zugriff im privaten Netz ist aktiviert. Für den Betrieb über HTTPS COOKIE_SECURE aktivieren und
          APP_ORIGIN auf die HTTPS-Adresse setzen.
        </span>
      </div>
    </>
  );
}

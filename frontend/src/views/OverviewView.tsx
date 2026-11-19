import {
  ArrowUpRight,
  ArrowRight,
  FileCode2,
  Network,
  Plus,
  Server,
  ShieldCheck,
  Terminal,
  Zap,
} from 'lucide-react';
import { type AppProps } from '../app/useAppState';
import { Chart, MetricCard } from '../components/Charts';
import { ServerTable } from '../components/ServerTable';
import { Button, Badge, Empty } from '../components/ui';
import { fmt, bytes } from '../format';

export function OverviewView({ app }: AppProps) {
  const {
    setSelected,
    stats,
    metrics,
    setInstanceEdit,
    scoped,
    setModal,
    current,
    canEdit,
    isAdmin,
    navigate,
    online,
    total,
  } = app;
  return (
    <>
      <div className="metrics-grid">
        <MetricCard
          label="Erreichbare Server"
          value={scoped.length ? `${online.length} / ${scoped.length}` : '0'}
          icon={Server}
          foot={
            scoped.length
              ? `${scoped.filter((i) => i.kind === 'docker').length} Docker · ${scoped.filter((i) => i.kind === 'native').length} Nativ`
              : 'Noch keine Server verbunden'
          }
          accent
        />
        <MetricCard
          label="Aktive Verbindungen"
          value={fmt(total('sessions'))}
          icon={Network}
          foot="Summe der erreichbaren Instanzen"
        />
        <MetricCard
          label="Anfragen pro Sekunde"
          value={fmt(total('request_rate'))}
          icon={Zap}
          foot="Aktuelle HTTP-Anfragerate"
        />
        <MetricCard
          label="Ausgehender Traffic"
          value={bytes(total('bytes_out'))}
          icon={ArrowUpRight}
          foot="Seit Start des jeweiligen Workers"
        />
      </div>
      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Anfragen im Verlauf</h2>
              <p>{current?.name ?? 'Keine Instanz ausgewählt'} · letzte Stunde</p>
            </div>
            <Badge tone="green">
              <span className="status-dot" />
              LIVE
            </Badge>
          </div>
          <Chart data={metrics} />
          <div className="chart-footer">
            <span>
              <i />
              Anfragen pro Sekunde
            </span>
            <button className="text-button" onClick={() => navigate('statistics')}>
              Alle Statistiken <ArrowRight size={14} />
            </button>
          </div>
        </section>
        <section className="panel status-panel">
          <div className="panel-header">
            <h2>Instanzstatus</h2>
            <Terminal size={18} />
          </div>
          {current ? (
            <>
              <div className="server-visual">
                <Server size={35} />
                <span className={stats?.online ? 'pulse-dot' : 'offline-dot'} />
              </div>
              <h3>{current.name}</h3>
              <Badge tone={stats?.online ? 'green' : 'neutral'}>
                {stats?.online ? 'Dienst erreichbar' : 'Keine Live-Verbindung'}
              </Badge>
              <dl>
                <div>
                  <dt>Installation</dt>
                  <dd>{current.kind === 'docker' ? 'Docker' : 'Nativ'}</dd>
                </div>
                <div>
                  <dt>HAProxy</dt>
                  <dd>{stats?.version ?? '—'}</dd>
                </div>
                <div>
                  <dt>Laufzeit</dt>
                  <dd>{stats?.uptime ?? '—'}</dd>
                </div>
                <div>
                  <dt>Profil</dt>
                  <dd className="mono">{current.profile}</dd>
                </div>
              </dl>
              <Button secondary onClick={() => navigate('config')} disabled={!canEdit}>
                <FileCode2 size={16} />
                Konfiguration öffnen
              </Button>
            </>
          ) : (
            <Empty
              icon={Server}
              title="Dein erster Server"
              text="Verbinde eine native Installation oder einen Docker-Container."
            />
          )}
        </section>
      </div>
      <section className="panel">
        <div className="panel-header">
          <h2>
            Deine Server <span className="count">{scoped.length}</span>
          </h2>
          <button className="text-button" onClick={() => navigate('servers')}>
            Server verwalten <ArrowRight size={14} />
          </button>
        </div>
        {scoped.length ? (
          <ServerTable
            instances={scoped}
            onOpen={(i) => {
              setSelected(i.id);
              navigate('statistics');
            }}
          />
        ) : (
          <Empty
            icon={Network}
            title="Alles bereit für deine Infrastruktur"
            text="Die Datenbank ist eingerichtet. Verbinde jetzt deine HAProxy-Server, um Hosts, Zertifikate und Statistiken zentral zu verwalten."
            action={
              isAdmin ? (
                <Button
                  onClick={() => {
                    setInstanceEdit(null);
                    setModal('instance');
                  }}
                >
                  <Plus size={16} />
                  Ersten Server verbinden
                </Button>
              ) : undefined
            }
          />
        )}
      </section>
      <div className="info-strip">
        <ShieldCheck size={19} />
        <span>
          Änderungen werden vor dem Anwenden mit HAProxy geprüft. Vorherige Konfigurationen bleiben als
          Version erhalten.
        </span>
      </div>
    </>
  );
}

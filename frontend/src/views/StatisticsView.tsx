import { Activity, ArrowDownLeft, Clock3, Network, ShieldX, Zap } from 'lucide-react';
import { ProxyStatistics } from '../ProxyStatistics';
import { type AppProps } from '../app/useAppState';
import { Chart, MetricCard } from '../components/Charts';
import { Badge } from '../components/ui';
import { fmt } from '../format';

export function StatisticsView({ app }: AppProps) {
  const { selected, stats, metrics, hours, setHours } = app;
  if (!selected) return null;
  return (
    <>
      <div className="metrics-grid">
        <MetricCard
          label="Anfragen / Sekunde"
          value={fmt(stats?.request_rate)}
          icon={Zap}
          foot="Aktuelle HTTP-Anfragerate"
          accent
        />
        <MetricCard
          label="Aktive Verbindungen"
          value={fmt(stats?.sessions)}
          icon={Network}
          foot="Aktuell offene Sessions"
        />
        <MetricCard
          label="Anfragen gesamt"
          value={fmt(stats?.requests)}
          icon={ArrowDownLeft}
          foot="Seit Start des Workers"
        />
        <MetricCard
          label="HTTP 5xx"
          value={fmt(stats?.errors_5xx)}
          icon={ShieldX}
          foot="Fehlerantworten seit Worker-Start"
        />
      </div>
      <div className="toolbar">
        <span>
          <Badge tone={stats?.online ? 'green' : 'red'}>
            {stats?.online ? 'Runtime verbunden' : 'Runtime nicht erreichbar'}
          </Badge>{' '}
          {stats?.uptime && 'Laufzeit ' + stats.uptime}
        </span>
        <select
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
          aria-label="Statistikzeitraum"
        >
          <option value={1}>Letzte Stunde</option>
          <option value={6}>Letzte 6 Stunden</option>
          <option value={24}>Letzte 24 Stunden</option>
          <option value={168}>Letzte 7 Tage</option>
        </select>
      </div>
      <div className="dashboard-grid equal">
        <section className="panel">
          <div className="panel-header">
            <h2>HTTP-Anfragen / Sekunde</h2>
            <Activity size={18} />
          </div>
          <Chart data={metrics} />
        </section>
        <section className="panel">
          <div className="panel-header">
            <h2>Aktive Verbindungen</h2>
            <Network size={18} />
          </div>
          <Chart data={metrics} field="sessions" />
        </section>
      </div>
      <ProxyStatistics rows={stats?.rows || []} version={stats?.version} />
      <div className="info-strip">
        <Clock3 size={18} />
        <span>
          Verlauf: 30-Sekunden-Intervalle für {stats?.history_policy?.raw_hours ?? 2} h, 5-Minuten-Intervalle
          für {stats?.history_policy?.fine_days ?? 1} Tag(e), Stundenwerte für{' '}
          {stats?.history_policy?.total_days ?? 7} Tage. Diagramme zeigen Durchschnitt und Spitzenwert im
          Tooltip. HAProxy-Zähler beginnen nach einem Worker-Neustart oder Reload neu.
        </span>
      </div>
    </>
  );
}

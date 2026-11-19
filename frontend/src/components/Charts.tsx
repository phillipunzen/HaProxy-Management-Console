import { Activity, Server } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip } from 'recharts';
import { fmt, date } from '../format';
import { type Metric } from '../types';

export function Chart({
  data,
  field = 'request_rate',
}: {
  data: Metric[];
  field?: 'request_rate' | 'sessions';
}) {
  return (
    <div className="chart">
      {data.length > 1 ? (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data.map((m) => ({
              ...m,
              [field]: m.online ? m[field] : null,
              label: new Date(m.time).toLocaleTimeString('de-DE', {
                hour: '2-digit',
                minute: '2-digit',
                timeZone: 'Europe/Berlin',
              }),
            }))}
          >
            <defs>
              <linearGradient id={'gradient-' + field} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.2} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 4" />
            <XAxis
              dataKey="label"
              axisLine={false}
              tickLine={false}
              minTickGap={35}
              tick={{ fontSize: 11, fill: 'var(--text-faint)' }}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              width={40}
              tick={{ fontSize: 11, fill: 'var(--text-faint)' }}
            />
            <Tooltip content={<HistoryTooltip field={field} />} />
            <Area
              type="monotone"
              dataKey={field}
              stroke="var(--accent)"
              strokeWidth={2.3}
              fill={'url(#gradient-' + field + ')'}
              connectNulls={false}
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <div className="chart-empty">
          <Activity size={24} />
          <span>Messwerte werden gesammelt</span>
          <small>Verlauf verfügbar, sobald zwei Messpunkte vorliegen.</small>
        </div>
      )}
    </div>
  );
}
export function HistoryTooltip({
  active,
  payload,
  field,
}: {
  active?: boolean;
  payload?: any[];
  field: 'request_rate' | 'sessions';
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload as Metric;
  const peak = field === 'sessions' ? point.sessions_peak : point.request_rate_peak;
  return (
    <div className="history-tooltip">
      <strong>{date(point.time)}</strong>
      <span>
        Durchschnitt: {fmt(point[field])}
        {field === 'request_rate' ? ' Anfragen/s' : ' Verbindungen'}
      </span>
      {peak != null && <span>Spitzenwert: {fmt(peak)}</span>}
      {point.availability != null && (
        <small>
          Erreichbar: {(point.availability * 100).toFixed(1)} % · {point.samples} Messpunkte
        </small>
      )}
    </div>
  );
}
export function MetricCard({
  label,
  value,
  icon: Icon,
  foot,
  accent = false,
}: {
  label: string;
  value: string;
  icon: typeof Server;
  foot: string;
  accent?: boolean;
}) {
  return (
    <section className={'metric-card ' + (accent ? 'accent' : '')}>
      <div className="metric-label">
        {label}
        <span>
          <Icon size={18} />
        </span>
      </div>
      <strong>{value}</strong>
      <small>{foot}</small>
    </section>
  );
}

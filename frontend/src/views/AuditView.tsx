import { type AppProps } from '../app/useAppState';
import { Badge } from '../components/ui';
import { date } from '../format';

export function AuditView({ app }: AppProps) {
  const { audit } = app;
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Letzte Aktivitäten</h2>
        <Badge>{audit.length} Einträge</Badge>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ZEITPUNKT</th>
              <th>BENUTZER</th>
              <th>AKTION</th>
              <th>ZIEL</th>
              <th>DETAILS</th>
            </tr>
          </thead>
          <tbody>
            {audit.map((a) => (
              <tr key={a.id}>
                <td className="nowrap">{date(a.created_at)}</td>
                <td>
                  <strong>{a.actor}</strong>
                </td>
                <td className="mono">{a.action}</td>
                <td>{a.target || '—'}</td>
                <td className="audit-detail">{a.detail || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

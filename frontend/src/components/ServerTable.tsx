import { ArrowRight, Server } from 'lucide-react';
import { ClassificationBadges } from '../ServerDirectory';
import { Badge, Status } from './ui';
import { fmt } from '../format';
import { type Instance } from '../types';

export function ServerTable({ instances, onOpen }: { instances: Instance[]; onOpen: (i: Instance) => void }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>SERVER</th>
            <th>INSTALLATION</th>
            <th>STATUS</th>
            <th>VERBINDUNGEN</th>
            <th>ANFRAGEN/S</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {instances.map((i) => (
            <tr key={i.id}>
              <td>
                <div className="table-primary">
                  <span className="small-icon">
                    <Server size={19} />
                  </span>
                  <div>
                    <strong>{i.name}</strong>
                    <small className="mono">{i.agent_url}</small>
                    <ClassificationBadges
                      tags={i.tags}
                      location={i.location}
                      infrastructure_name={i.infrastructure_name}
                    />
                  </div>
                </div>
              </td>
              <td>
                <Badge>{i.kind === 'docker' ? 'Docker' : 'Nativ'}</Badge>
              </td>
              <td>
                <Status instance={i} />
              </td>
              <td>{fmt(i.stats?.sessions)}</td>
              <td>{fmt(i.stats?.request_rate)}</td>
              <td>
                <button className="icon-button" title="Statistiken öffnen" onClick={() => onOpen(i)}>
                  <ArrowRight size={17} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

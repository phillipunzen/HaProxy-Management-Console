import { Topology } from '../Topology';
import { api } from '../api';
import { type AppProps } from '../app/useAppState';

export function TopologyView({ app }: AppProps) {
  const { selected, topologyRefresh, current } = app;
  if (!selected) return null;
  if (!current) return null;
  return (
    <Topology instanceId={selected} instanceName={current.name} request={api} refreshKey={topologyRefresh} />
  );
}

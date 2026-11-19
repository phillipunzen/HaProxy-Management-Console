import { CircleDot, Database, Monitor, Moon, RefreshCw, Sun, UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api';
import { type ThemeChoice } from './theme';
import { type AppProps } from './useAppState';

const THEME_LABEL: Record<ThemeChoice, string> = { system: 'System', light: 'Hell', dark: 'Dunkel' };
const ROLE_LABEL: Record<string, string> = {
  admin: 'Administrator',
  operator: 'Operator',
  viewer: 'Leserechte',
};

/** IDE-style status bar: target server, refresh cadence, account, version, theme. */
export function StatusBar({ app, theme, onTheme }: AppProps & { theme: ThemeChoice; onTheme: () => void }) {
  const { user, view, current, scoped, busy } = app;
  const [version, setVersion] = useState('');
  useEffect(() => {
    api('/auth/me')
      .then((r) => setVersion(r.version || ''))
      .catch(() => {});
  }, []);
  const online = scoped.filter((i) => i.stats?.online).length;
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor;
  return (
    <footer className="statusbar" aria-label="Statusleiste">
      <span className="status-item" title="Erreichbare Server in der aktuellen Auswahl">
        <CircleDot
          size={12}
          className={online === scoped.length && scoped.length ? 'ok' : online ? 'warn' : ''}
        />
        {online}/{scoped.length} Server online
      </span>
      {current && (
        <span className="status-item hide-narrow" title={current.agent_url}>
          {current.name}
          {current.stats?.version ? ' · HAProxy ' + current.stats.version.split('-')[0] : ''}
        </span>
      )}
      <span className="status-item hide-narrow">
        <RefreshCw size={12} className={busy ? 'spin' : ''} />
        alle {['topology', 'hosts'].includes(view) ? '10' : '30'} s
      </span>
      <span className="status-spacer" />
      <span className="status-item hide-narrow">
        <Database size={12} />
        MariaDB
      </span>
      <span className="status-item">
        <UserRound size={12} />
        {user.username} · {ROLE_LABEL[user.role] ?? user.role}
      </span>
      {version && (
        <span className="status-item hide-narrow">{/^\d/.test(version) ? 'v' + version : version}</span>
      )}
      <button
        className="status-item status-button"
        onClick={onTheme}
        title="Farbschema wechseln (System → Hell → Dunkel)"
        aria-label={'Farbschema: ' + THEME_LABEL[theme]}
      >
        <ThemeIcon size={12} />
        {THEME_LABEL[theme]}
      </button>
    </footer>
  );
}

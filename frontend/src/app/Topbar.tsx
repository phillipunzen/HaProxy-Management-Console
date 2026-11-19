import {
  Building2,
  Check,
  ChevronRight,
  GitCommitHorizontal,
  LogOut,
  Menu,
  Network,
  Server,
} from 'lucide-react';
import { api, setCsrf } from '../api';
import { labels } from './constants';
import { type AppProps } from './useAppState';

/** JetBrains-style title bar: product, infrastructure + server switcher, account. */
export function Topbar({ app }: AppProps) {
  const {
    user,
    setUser,
    view,
    mobile,
    setMobile,
    run,
    busy,
    modal,
    confirm,
    scope,
    chooseScope,
    infrastructures,
    scoped,
    selected,
    chooseInstance,
    current,
    canEdit,
    changes,
    setModal,
    openApply,
  } = app;
  const locked = busy || !!modal || !!confirm;
  const serverScoped =
    scoped.length > 0 && !['servers', 'infrastructures', 'audit', 'settings', 'basic-auth'].includes(view);
  const online = current?.stats?.online;
  return (
    <header className="titlebar">
      <button
        className="icon-button mobile-toggle"
        aria-label="Menü öffnen"
        onClick={() => setMobile(!mobile)}
      >
        <Menu size={18} />
      </button>
      <a
        className="brand"
        href="#overview"
        onClick={(e) => {
          e.preventDefault();
          app.navigate('overview');
        }}
      >
        <span className="brand-mark">
          <Network size={15} />
        </span>
        <span className="brand-name">HAProxy Control</span>
      </a>
      <div className="switcher" title="Infrastruktur">
        <Building2 size={14} />
        <select
          aria-label="Infrastruktur auswählen"
          disabled={locked}
          value={scope}
          onChange={(e) => chooseScope(e.target.value)}
        >
          <option value="">Alle Infrastrukturen</option>
          <option value="none">Ohne Zuordnung</option>
          {infrastructures.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name}
            </option>
          ))}
          {scope && scope !== 'none' && !infrastructures.some((i) => String(i.id) === scope) && (
            <option value={scope}>Entfernte Infrastruktur</option>
          )}
        </select>
      </div>
      {serverScoped && (
        <>
          <ChevronRight size={14} className="titlebar-sep" />
          <div className="switcher" title={current?.agent_url}>
            <span className={'dot ' + (online ? 'ok' : online === false ? 'bad' : '')} />
            <Server size={14} />
            <select
              aria-label="HAProxy-Instanz auswählen"
              disabled={locked}
              value={selected ?? ''}
              onChange={(e) => chooseInstance(Number(e.target.value))}
            >
              {scoped.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name} · {i.kind === 'docker' ? 'Docker' : 'Nativ'}
                  {i.location ? ' · ' + i.location : ''}
                </option>
              ))}
            </select>
          </div>
        </>
      )}
      <ChevronRight size={14} className="titlebar-sep" />
      <span className="titlebar-view">{labels[view] || 'Übersicht'}</span>
      <div className="titlebar-right">
        {canEdit && selected && changes && (
          <>
            <button
              className={'changes-button' + (changes.count ? ' pending' : '')}
              disabled={locked}
              title="Gespeichert, aber noch nicht auf dem Server aktiv"
              onClick={() => setModal('changes')}
            >
              <GitCommitHorizontal size={15} />
              {!changes.baseline
                ? 'Änderungen'
                : changes.count
                  ? changes.count + (changes.count === 1 ? ' Änderung' : ' Änderungen')
                  : 'Keine Änderungen'}
            </button>
            {(!changes.baseline || !!changes.count) && (
              <button className="button titlebar-apply" disabled={locked} onClick={() => openApply()}>
                <Check size={14} />
                Prüfen & anwenden
              </button>
            )}
          </>
        )}
        <span className="avatar" title={user.username}>
          {user.username.slice(0, 2).toUpperCase()}
        </span>
        <button
          className="icon-button"
          aria-label="Abmelden"
          title="Abmelden"
          onClick={() =>
            run(async () => {
              await api('/auth/logout', 'POST');
              setUser(null);
              setCsrf('');
            })
          }
        >
          <LogOut size={16} />
        </button>
      </div>
    </header>
  );
}

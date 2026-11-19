import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { nav, navGroups } from './constants';
import { type AppProps } from './useAppState';

const EDITOR_ONLY = ['config', 'rules', 'hosts', 'basic-auth', 'access-profiles'];

export function Sidebar({
  app,
  collapsed,
  onToggle,
}: AppProps & { collapsed: boolean; onToggle: () => void }) {
  const { view, mobile, scoped, canEdit, isAdmin, navigate } = app;
  const visible = nav
    .filter((n) => n.key !== 'audit' || isAdmin)
    .filter((n) => !EDITOR_ONLY.includes(n.key) || canEdit);
  const item = (n: (typeof nav)[number]) => (
    <button
      key={n.key}
      aria-label={n.label}
      title={collapsed ? n.label : undefined}
      aria-current={view === n.key ? 'page' : undefined}
      className={view === n.key ? 'active' : ''}
      onClick={() => navigate(n.key)}
    >
      <n.icon size={16} />
      <span className="nav-label">{n.label}</span>
      {n.key === 'servers' && <span className="nav-count">{scoped.length}</span>}
    </button>
  );
  return (
    <aside className={'sidebar' + (mobile ? ' open' : '') + (collapsed && !mobile ? ' collapsed' : '')}>
      <nav aria-label="Hauptnavigation">
        {navGroups.map((g) => {
          const items = visible.filter((n) => n.group === g.key);
          if (!items.length) return null;
          return (
            <div className="nav-group" key={g.key}>
              <span className="nav-heading">{g.label}</span>
              {items.map(item)}
            </div>
          );
        })}
      </nav>
      <div className="sidebar-bottom">
        {visible.filter((n) => n.group === 'footer').map(item)}
        <button
          className="sidebar-collapse"
          aria-label={collapsed ? 'Navigation ausklappen' : 'Navigation einklappen'}
          title={collapsed ? 'Navigation ausklappen' : 'Navigation einklappen'}
          onClick={onToggle}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          <span className="nav-label">Einklappen</span>
        </button>
      </div>
    </aside>
  );
}

import { CheckCircle2, Loader2, Network, Server, X, XCircle } from 'lucide-react';
import { createRoot } from 'react-dom/client';
import { setCsrf } from './api';
import { AppModals } from './app/AppModals';
import { PageIntro } from './app/PageIntro';
import { Sidebar } from './app/Sidebar';
import { StatusBar } from './app/StatusBar';
import { useSidebarCollapsed, useTheme } from './app/theme';
import { Topbar } from './app/Topbar';
import { useAppState } from './app/useAppState';
import { Button, ModalContext, Empty } from './components/ui';
import { AccessProfilesView } from './views/AccessProfilesView';
import { AuditView } from './views/AuditView';
import { BasicAuthView } from './views/BasicAuthView';
import { CertificatesView } from './views/CertificatesView';
import { ConfigView } from './views/ConfigView';
import { HostsView } from './views/HostsView';
import { InfrastructuresView } from './views/InfrastructuresView';
import { Login, PasswordScreen } from './views/Login';
import { OverviewView } from './views/OverviewView';
import { ProxiesView } from './views/ProxiesView';
import { RulesView } from './views/RulesView';
import { ServersView } from './views/ServersView';
import { SettingsView } from './views/SettingsView';
import { StatisticsView } from './views/StatisticsView';
import { TopologyView } from './views/TopologyView';
import '@fontsource-variable/inter';
import './styles.css';

function App() {
  const app = useAppState();
  const theme = useTheme();
  const sidebar = useSidebarCollapsed();
  const {
    user,
    setUser,
    checking,
    view,
    mobile,
    setMobile,
    selected,
    toast,
    setToast,
    busy,
    resourceError,
    modalTarget,
    scoped,
    resourceReady,
    current,
    canEdit,
    isAdmin,
    navigate,
    requiresInstance,
  } = app;
  if (checking)
    return (
      <div className="loading-screen">
        <Loader2 className="spin" /> HAProxy Control wird geladen
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={(r) => {
          setCsrf(r.csrf);
          setUser(r.user);
          setToast(null);
        }}
      />
    );
  if (user.must_change_password)
    return (
      <PasswordScreen
        onDone={() => {
          setUser(null);
          setCsrf('');
        }}
      />
    );
  const viewApp = { ...app, user };
  return (
    <ModalContext.Provider value={{ target: modalTarget, busy }}>
      <div className={'app-shell' + (sidebar.collapsed ? ' nav-collapsed' : '')}>
        <Topbar app={viewApp} />
        <Sidebar app={viewApp} collapsed={sidebar.collapsed} onToggle={sidebar.toggle} />
        {mobile && <div className="mobile-shade" onClick={() => setMobile(false)} />}
        <main>
          <div className="page">
            <PageIntro app={viewApp} />
            {requiresInstance && scoped.length === 0 ? (
              <section className="panel">
                <Empty
                  icon={Server}
                  title="Kein Server in dieser Auswahl"
                  text="Wähle eine andere Infrastruktur oder ordne ihr unter Server eine HAProxy-Instanz zu."
                  action={
                    <Button onClick={() => navigate('servers')}>
                      <Server size={16} />
                      Zur Serververwaltung
                    </Button>
                  }
                />
              </section>
            ) : requiresInstance && !resourceReady ? (
              <section className="panel">
                <div className="empty">
                  <Loader2 size={24} className={resourceError ? '' : 'spin'} />
                  <p>
                    {resourceError
                      ? 'Daten des Zielservers konnten nicht geladen werden. Bitte erneut aktualisieren.'
                      : 'Daten des ausgewählten Servers werden geladen …'}
                  </p>
                </div>
              </section>
            ) : (
              <>
                {view === 'overview' && <OverviewView app={viewApp} />}
                {view === 'servers' && <ServersView app={viewApp} />}
                {view === 'infrastructures' && <InfrastructuresView app={viewApp} />}
                {view === 'access-profiles' && canEdit && <AccessProfilesView app={viewApp} />}
                {view === 'hosts' && selected && <HostsView app={viewApp} />}
                {view === 'rules' && selected && <RulesView app={viewApp} />}
                {view === 'basic-auth' && canEdit && <BasicAuthView app={viewApp} />}
                {view === 'certificates' && selected && <CertificatesView app={viewApp} />}
                {view === 'proxies' && selected && <ProxiesView app={viewApp} />}
                {view === 'config' && selected && <ConfigView app={viewApp} />}
                {view === 'topology' && selected && current && <TopologyView app={viewApp} />}
                {view === 'statistics' && selected && <StatisticsView app={viewApp} />}
                {view === 'audit' && isAdmin && <AuditView app={viewApp} />}
                {view === 'settings' && <SettingsView app={viewApp} />}
              </>
            )}
          </div>
        </main>
        <StatusBar app={viewApp} theme={theme.theme} onTheme={theme.next} />
        {busy && (
          <div className="busy-bar">
            <Loader2 size={16} className="spin" />
            Aktion wird ausgeführt …
          </div>
        )}
        {toast && (
          <div className={'toast ' + (toast.error ? 'error' : '')} role="alert">
            {toast.error ? <XCircle size={20} /> : <CheckCircle2 size={20} />}
            <span>{toast.text}</span>
            <button className="icon-button" aria-label="Meldung schließen" onClick={() => setToast(null)}>
              <X size={17} />
            </button>
          </div>
        )}
        <AppModals app={viewApp} />
      </div>
    </ModalContext.Provider>
  );
}

createRoot(document.getElementById('root')!).render(<App />);

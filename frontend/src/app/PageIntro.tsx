import { Building2, Plus, RefreshCw, ShieldX } from 'lucide-react';
import { labels } from './constants';
import { type AppProps } from './useAppState';
import { Button } from '../components/ui';

export function PageIntro({ app }: AppProps) {
  const {
    view,
    selected,
    doc,
    cap,
    busy,
    modal,
    confirm,
    setHostEdit,
    setRuleEdit,
    setInstanceEdit,
    resourceError,
    scope,
    resourceReady,
    setConfirm,
    setModal,
    current,
    canEdit,
    isAdmin,
    notify,
    run,
    dirty,
    reloadPage,
  } = app;
  const pageDescriptions: Record<string, string> = {
    infrastructures: 'Gruppiere Server und wähle gezielt ihre Zertifikate und Einstellungen.',
    overview: 'Deine Infrastruktur auf einen Blick.',
    servers: 'Verbinde und verwalte deine HAProxy-Instanzen.',
    hosts: 'Domains, Routen und Backends für ' + (current?.name ?? 'deine Server') + '.',
    proxies: 'Listener, Zertifikate und Zielserver für ' + (current?.name ?? 'deinen Server') + '.',
    certificates: 'TLS-Zertifikate ausschließlich für ' + (current?.name ?? 'den ausgewählten Server') + '.',
    rules: 'Zugriff steuern, Header setzen und Anfragen weiterleiten.',
    'access-profiles': 'Gemeinsame IP-Listen pflegen und Proxy Hosts auf diesem Server zuweisen.',
    'basic-auth': 'Website-Zugänge zentral verwalten und einzelnen Sites zuordnen.',
    config: 'Entwürfe prüfen, vergleichen und sicher anwenden.',
    statistics: 'Live-Messwerte und Verlauf deiner Instanz.',
    topology: 'Sites, TCP-Dienste und Zielserver mit gemessener Aktivität.',
    audit: 'Änderungen und Anmeldungen nachvollziehen.',
    settings: 'Dein Konto und die Zugriffsrechte.',
  };
  return (
    <>
      <header className="page-header">
        <div className="page-title">
          <h1>{labels[view] || 'Übersicht'}</h1>
          <p>{pageDescriptions[view]}</p>
        </div>
        <div className="page-actions">
          <Button
            secondary
            disabled={busy || !!modal || !!confirm}
            onClick={() =>
              dirty
                ? setConfirm({
                    title: 'Konfiguration neu laden',
                    text: 'Der Arbeitsentwurf wird durch die aktuelle Serverkonfiguration ersetzt.',
                    fn: async () => {
                      await reloadPage();
                      notify('Ansicht aktualisiert.');
                    },
                  })
                : run(reloadPage, 'Ansicht aktualisiert.')
            }
          >
            <RefreshCw size={16} className={busy ? 'spin' : ''} />
            <span>Aktualisieren</span>
          </Button>
          {view === 'servers' && isAdmin && (
            <Button
              onClick={() => {
                setInstanceEdit(null);
                setModal('instance');
              }}
            >
              <Plus size={17} />
              Server hinzufügen
            </Button>
          )}
          {view === 'overview' && isAdmin && (
            <Button
              onClick={() => {
                setInstanceEdit(null);
                setModal('instance');
              }}
            >
              <Plus size={17} />
              Server verbinden
            </Button>
          )}
          {view === 'hosts' && canEdit && selected && (
            <Button
              disabled={busy || !resourceReady}
              onClick={() => {
                setHostEdit(null);
                setModal('host');
              }}
            >
              <Plus size={17} />
              Proxy Host
            </Button>
          )}
          {view === 'rules' && canEdit && selected && doc.imported_config == null && (
            <Button
              disabled={busy || !resourceReady}
              onClick={() => {
                setRuleEdit(null);
                setModal('rule');
              }}
            >
              <Plus size={17} />
              Regel erstellen
            </Button>
          )}
          {view === 'certificates' && canEdit && selected && (
            <Button
              disabled={busy || !resourceReady || !cap || !!cap.certificate_scope_error}
              onClick={() => setModal('cert')}
            >
              <Plus size={17} />
              Zertifikat anfordern
            </Button>
          )}
        </div>
      </header>
      {['infrastructures', 'basic-auth', 'audit', 'settings'].includes(view) && scope && (
        <div className="notice">
          <Building2 size={18} />
          <span>
            Diese zentrale Ansicht gilt für die gesamte Verwaltung. Die Infrastruktur-Auswahl filtert
            Serverübersicht und Server-Funktionen.
          </span>
        </div>
      )}
      {resourceError && (
        <div className="notice error">
          <ShieldX size={18} />
          <span>{resourceError}</span>
        </div>
      )}
    </>
  );
}

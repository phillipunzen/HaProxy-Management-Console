import {
  ArrowUpRight,
  ArrowRight,
  Code2,
  Plus,
  Settings2,
  ShieldX,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react';
import { type AppProps } from '../app/useAppState';
import { Button, Badge, Empty } from '../components/ui';

export function RulesView({ app }: AppProps) {
  const {
    selected,
    doc,
    busy,
    setRuleEdit,
    setRuleDomainMode,
    resourceReady,
    setModal,
    canEdit,
    run,
    navigate,
    saveDoc,
    confirmDelete,
  } = app;
  if (!selected) return null;
  return (
    <>
      {doc.imported_config != null && (
        <div className="notice">
          <span>
            Neue Regeln und Domain-Weiterleitungen werden am gewählten HTTP-/HTTPS-Frontend ergänzt.
            Ursprüngliche Regeln bleiben im Konfigurationseditor erhalten.{' '}
            <button className="text-button" onClick={() => navigate('config')}>
              Konfiguration öffnen
            </button>
          </span>
        </div>
      )}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Regeln & Domain-Weiterleitungen</h2>
            <p>
              Hostname oder Pfad weiterleiten, Anfragen einschränken und Header setzen. Reihenfolge gilt je
              Frontend.
            </p>
          </div>
          {canEdit && (
            <div className="row-actions rule-create-actions">
              <Button
                secondary
                disabled={busy || !resourceReady}
                onClick={() => {
                  setRuleEdit(null);
                  setRuleDomainMode(false);
                  setModal('rule');
                }}
              >
                <Plus size={16} />
                Regel
              </Button>
              <Button
                disabled={busy || !resourceReady}
                onClick={() => {
                  setRuleEdit(null);
                  setRuleDomainMode(true);
                  setModal('rule');
                }}
              >
                <ArrowRight size={16} />
                Domain-Weiterleitung
              </Button>
            </div>
          )}
        </div>
        {doc.rules.length ? (
          <div className="rule-list">
            {doc.rules.map((r, index) => (
              <div className="rule-row" key={r.id}>
                <span className="rule-number">{String(index + 1).padStart(2, '0')}</span>
                <span className="small-icon">
                  {r.action === 'redirect' ? (
                    <ArrowRight size={18} />
                  ) : r.action === 'deny' ? (
                    <ShieldX size={18} />
                  ) : (
                    <Code2 size={18} />
                  )}
                </span>
                <div className="rule-description">
                  <strong>{r.name}</strong>
                  <small>
                    <span className="mono">
                      {r.frontend || 'public_http'} · {r.match === 'host' ? 'Hostname' : r.match} = {r.value}
                    </span>
                    <ArrowRight size={12} />
                    {r.action === 'deny' ? 'Zugriff verweigern' : r.target}
                  </small>
                  {r.action === 'redirect' && (
                    <small>
                      HTTP {r.code} · {r.preserve_path ? 'Pfad & Query übernehmen' : 'Feste Ziel-URL'}
                    </small>
                  )}
                </div>
                <Badge tone={r.enabled ? 'green' : 'neutral'}>{r.enabled ? 'Aktiv' : 'Deaktiviert'}</Badge>
                {canEdit && (
                  <div className="row-actions">
                    <button
                      className="icon-button"
                      title="Nach oben"
                      disabled={busy || !resourceReady || index === 0}
                      onClick={() =>
                        run(async () => {
                          const rules = [...doc.rules];
                          [rules[index - 1], rules[index]] = [rules[index], rules[index - 1]];
                          await saveDoc({ ...doc, rules });
                        })
                      }
                    >
                      <ArrowUpRight size={16} />
                    </button>
                    <button
                      className="icon-button"
                      title="Bearbeiten"
                      disabled={busy || !resourceReady}
                      onClick={() => {
                        setRuleEdit(r);
                        setRuleDomainMode(r.match === 'host' && r.action === 'redirect');
                        setModal('rule');
                      }}
                    >
                      <Settings2 size={16} />
                    </button>
                    <button
                      className="icon-button red-text"
                      title="Löschen"
                      disabled={busy || !resourceReady}
                      onClick={() =>
                        confirmDelete('Regel löschen', async () =>
                          saveDoc({ ...doc, rules: doc.rules.filter((x) => x.id !== r.id) }),
                        )
                      }
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <Empty
            icon={SlidersHorizontal}
            title="Domains direkt weiterleiten"
            text="Zum Beispiel a.domain.de → https://b.domain.de, auf Wunsch mit Pfad und Query. Dafür wird kein Backend benötigt."
            action={
              canEdit && (
                <Button
                  disabled={busy || !resourceReady}
                  onClick={() => {
                    setRuleEdit(null);
                    setRuleDomainMode(true);
                    setModal('rule');
                  }}
                >
                  <ArrowRight size={16} />
                  Erste Domain-Weiterleitung
                </Button>
              )
            }
          />
        )}
      </section>
    </>
  );
}

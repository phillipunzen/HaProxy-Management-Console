import { Check, Copy, Loader2 } from 'lucide-react';
import { AccessProfileForm } from '../AccessProfiles';
import { AgentSetupWizard } from '../AgentSetupWizard';
import { CertificateRequestForm, RenewalSettingsForm, CertificatePolicyForm } from '../Certificates';
import { ConfigImport, ImportedBackendForm, ImportedRouteForm } from '../ConfigImport';
import { hostnames } from '../Hostnames';
import { FrontendForm, BackendForm, FrontendCertificateForm } from '../ProxyLayout';
import { api, setCsrf } from '../api';
import { type AppProps } from './useAppState';
import { Button, Modal } from '../components/ui';
import { ClassificationForm } from '../forms/ClassificationForm';
import { HostForm } from '../forms/HostForm';
import { InstanceForm } from '../forms/InstanceForm';
import { ListenerForm } from '../forms/ListenerForm';
import { PemForm } from '../forms/PemForm';
import { RuleForm } from '../forms/RuleForm';
import { UserForm } from '../forms/UserForm';
import { PasswordForm } from '../views/Login';
import { ApplyDialog } from './ApplyDialog';
import { ChangesDialog } from './ChangesDialog';

export function AppModals({ app }: AppProps) {
  const {
    setUser,
    doc,
    setDoc,
    cap,
    setCap,
    certs,
    setCerts,
    setUsers,
    busy,
    setBusy,
    modal,
    confirm,
    hostEdit,
    ruleEdit,
    instanceEdit,
    accessProfileEdit,
    ruleDomainMode,
    importedBackendEdit,
    setImportedBackendEdit,
    importedRouteEdit,
    importedRouteIndex,
    renewalSettings,
    setRenewalSettings,
    certificateEdit,
    layout,
    setLayout,
    frontendEdit,
    setFrontendEdit,
    poolEdit,
    certificateFrontend,
    authDirectory,
    updateCommand,
    infrastructures,
    scope,
    modalTarget,
    setConfirm,
    setModal,
    isAdmin,
    notify,
    run,
    commitNavigate,
    loadInstances,
    selectedRef,
    selected,
    saveDoc,
    saveImportedFields,
    modalRequest,
    controlledSave,
    removeBackend,
    removeFrontend,
    manualAccess,
    changes,
    applyRequest,
    setApplyRequest,
    openApply,
    revertChange,
    finishApply,
    instances,
    current,
    view,
  } = app;
  return (
    <>
      {modal === 'changes' && changes && (
        <ChangesDialog
          changes={changes}
          serverName={current?.name ?? 'Server'}
          busy={busy}
          onClose={() => setModal(null)}
          onApply={() => openApply()}
          onRevert={(c) => run(() => revertChange(c.kind, c.key), c.label + ': Änderung verworfen.')}
          onRevertAll={() =>
            setConfirm({
              title: 'Alle Änderungen verwerfen',
              text:
                'Alle gespeicherten, noch nicht angewendeten Änderungen auf ' +
                (current?.name ?? 'diesem Server') +
                ' werden auf den laufenden Stand zurückgesetzt.',
              danger: true,
              fn: async () => {
                await revertChange('all', '');
                setModal(null);
                notify('Alle ausstehenden Änderungen verworfen.');
              },
            })
          }
        />
      )}
      {modal === 'apply' && applyRequest && (
        <ApplyDialog
          request={applyRequest}
          target={instances.find((i) => i.id === applyRequest.id)}
          onClose={() => {
            setModal(null);
            setApplyRequest(null);
          }}
          onShowChanges={() => {
            setApplyRequest(null);
            setModal('changes');
          }}
          canCheckSites={!!cap?.probes && applyRequest.id === selected}
          onApplied={(result) => finishApply(applyRequest.id, view, result)}
        />
      )}
      {modal === 'instance' && !instanceEdit && (
        <Modal wide title="HAProxy-Host hinzufügen" onClose={() => setModal(null)}>
          <AgentSetupWizard
            infrastructures={infrastructures}
            defaultInfrastructure={scope && scope !== 'none' ? Number(scope) : null}
            request={api}
            onExisting={() => setModal('instance-manual')}
            onConnected={async () => {
              await loadInstances();
              setModal(null);
              notify('Server verbunden.');
            }}
          />
        </Modal>
      )}
      {modal === 'instance-metadata' && instanceEdit && (
        <ClassificationForm
          infrastructures={infrastructures}
          initial={instanceEdit}
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(value) =>
            run(async () => {
              await api(`/instances/${instanceEdit.id}/metadata`, 'PUT', value);
              await loadInstances();
              setModal(null);
            }, 'Server-Zuordnung gespeichert.')
          }
        />
      )}
      {(modal === 'instance-manual' || (modal === 'instance' && !!instanceEdit)) && (
        <InstanceForm
          infrastructures={infrastructures}
          defaultInfrastructure={scope && scope !== 'none' ? Number(scope) : null}
          initial={instanceEdit}
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(value) =>
            run(
              async () => {
                await api(
                  '/instances' + (instanceEdit ? '/' + instanceEdit.id : ''),
                  instanceEdit ? 'PUT' : 'POST',
                  value,
                );
                await loadInstances();
                setModal(null);
              },
              instanceEdit ? 'Serverdaten gespeichert.' : 'Server verbunden.',
            )
          }
        />
      )}
      {modal === 'import' && modalTarget && (
        <Modal wide title="Vorhandene HAProxy-Konfiguration einlesen" onClose={() => setModal(null)}>
          <ConfigImport
            canUpdateAgent={isAdmin}
            id={modalTarget.id}
            onBusy={setBusy}
            request={modalRequest}
            onDone={(value) => {
              if (selectedRef.current !== modalTarget.id) {
                notify(
                  'Import auf dem ursprünglichen Zielserver gespeichert. Bitte dessen Ansicht neu laden.',
                );
                return;
              }
              setDoc(value);
              setModal(null);
              commitNavigate('hosts');
              notify('Konfiguration als grafischen Entwurf übernommen. HAProxy läuft unverändert weiter.');
            }}
          />
        </Modal>
      )}
      {modal === 'imported-backend' && importedBackendEdit && modalTarget && (
        <Modal
          wide
          title={'Backend ' + importedBackendEdit.name + ' bearbeiten'}
          onClose={() => setModal(null)}
        >
          <ImportedBackendForm
            initial={importedBackendEdit}
            busy={busy}
            onRemove={() =>
              void removeBackend(importedBackendEdit.name, () => {
                setModal(null);
                setImportedBackendEdit(null);
              })
            }
            onClose={() => setModal(null)}
            onSave={async (value) => {
              await saveImportedFields({
                imported_backends: (doc.imported_backends || []).map((b) =>
                  b.name === value.name ? value : b,
                ),
              });
              setModal(null);
              notify('Backend gespeichert – noch nicht aktiv.');
            }}
          />
        </Modal>
      )}
      {modal === 'imported-route' && importedRouteEdit && modalTarget && (
        <Modal
          wide
          title={
            importedRouteIndex === null
              ? 'Domain-Zuordnung hinzufügen'
              : 'Domain-Zuordnung bearbeiten · ' + importedRouteEdit.domain
          }
          onClose={() => setModal(null)}
        >
          <ImportedRouteForm
            manualSections={manualAccess}
            accessProfiles={doc.access_profiles || []}
            sharedRoutes={doc.imported_routes || []}
            existing={doc.basic_auth_existing || []}
            certs={certs}
            initial={importedRouteEdit}
            groups={authDirectory?.groups || []}
            fronts={
              doc.imported_route_frontends ||
              Array.from(new Set((doc.imported_routes || []).map((r) => r.frontend)))
            }
            backends={[
              ...(doc.imported_backends || []),
              ...(doc.backends || []).map((b) => ({
                ...b,
                servers: b.servers.map((s, i) => ({ ...s, name: 'srv_' + (i + 1) })),
              })),
            ]}
            busy={busy}
            onClose={() => setModal(null)}
            onSave={async (value, options, profiles) => {
              await saveImportedFields({
                access_profiles: profiles,
                ...(options
                  ? {
                      imported_backends: (doc.imported_backends || []).map((b) =>
                        b.name === value.backend ? { ...b, proxy_options: options } : b,
                      ),
                      backends: (doc.backends || []).map((b) =>
                        b.name === value.backend ? { ...b, proxy_options: options } : b,
                      ),
                    }
                  : {}),
                imported_routes:
                  importedRouteIndex === null
                    ? [...(doc.imported_routes || []), value]
                    : (doc.imported_routes || []).map((r, i) => (i === importedRouteIndex ? value : r)),
              });
              setModal(null);
              notify('Domain und Website-Zugang gespeichert – noch nicht aktiv.');
            }}
          />
        </Modal>
      )}
      {modal === 'agent-update' && (
        <Modal wide title="Agent aktualisieren" onClose={() => setModal(null)}>
          <div className="form-body">
            <p>
              Führe diesen Befehl auf dem HAProxy-Host aus. Er aktualisiert den bestehenden systemd-Agenten
              und startet ihn neu. Profile, Zugriffstokens und HAProxy-Konfigurationen bleiben erhalten.
            </p>
            <textarea
              className="wizard-command"
              rows={7}
              readOnly
              aria-label="Agent-Aktualisierungsbefehl"
              value={updateCommand}
              onFocus={(e) => e.target.select()}
            />
            <div className="modal-actions">
              <Button secondary onClick={() => setModal(null)}>
                Schließen
              </Button>
              <Button
                onClick={() =>
                  run(async () => {
                    if (!navigator.clipboard)
                      throw new Error('Bitte den markierten Befehl manuell kopieren.');
                    await navigator.clipboard.writeText(updateCommand);
                  }, 'Befehl kopiert.')
                }
              >
                <Copy size={16} />
                Kopieren
              </Button>
            </div>
          </div>
        </Modal>
      )}
      {modal === 'access-profile' && (
        <Modal
          title={accessProfileEdit ? 'IP-Filter-Profil bearbeiten' : 'IP-Filter-Profil hinzufügen'}
          onClose={() => setModal(null)}
        >
          <AccessProfileForm
            initial={accessProfileEdit}
            busy={busy}
            onClose={() => setModal(null)}
            onSave={(value) =>
              controlledSave(async () => {
                await saveDoc({
                  ...doc,
                  access_profiles: (doc.access_profiles || []).some((p) => p.id === value.id)
                    ? (doc.access_profiles || []).map((p) => (p.id === value.id ? value : p))
                    : [...(doc.access_profiles || []), value],
                });
                setModal(null);
              }, 'IP-Filter-Profil gespeichert – noch nicht aktiv.')
            }
          />
        </Modal>
      )}
      {modal === 'host' && (
        <HostForm
          instanceId={selected}
          manualSections={manualAccess}
          profiles={doc.access_profiles || []}
          doc={doc}
          fronts={layout?.frontends.filter((f) => f.mode === 'http') || []}
          certs={certs}
          groups={authDirectory?.groups || []}
          initial={hostEdit}
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(value, profiles) =>
            controlledSave(async () => {
              await saveDoc({
                ...doc,
                access_profiles: profiles,
                tls_enabled:
                  doc.tls_enabled ||
                  (doc.imported_config == null &&
                    (value.frontend || 'public_http') === 'public_http' &&
                    !!value.certificate),
                hosts: hostEdit
                  ? doc.hosts.map((h) => (h.id === value.id ? value : h))
                  : [...doc.hosts, value],
              });
              setModal(null);
            }, 'Proxy Host gespeichert – noch nicht aktiv.')
          }
        />
      )}
      {modal === 'rule' && (
        <RuleForm
          initial={ruleEdit}
          domainRedirect={ruleDomainMode}
          fronts={layout?.frontends.filter((f) => f.mode === 'http') || []}
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(value) =>
            controlledSave(async () => {
              await saveDoc({
                ...doc,
                rules: ruleEdit
                  ? doc.rules.map((r) => (r.id === value.id ? value : r))
                  : [...doc.rules, value],
              });
              setModal(null);
            }, 'Regel gespeichert – noch nicht aktiv.')
          }
        />
      )}
      {modal === 'listeners' && (
        <ListenerForm
          doc={doc}
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(value) =>
            run(async () => {
              await saveDoc(value);
              setModal(null);
            }, 'Listener-Einstellungen gespeichert – noch nicht aktiv.')
          }
        />
      )}
      {(modal === 'cert' || modal === 'adopt-cert' || modal === 'production-cert') && (
        <Modal
          wide
          title={
            modal === 'adopt-cert'
              ? 'Vorhandenen LEGO-Auftrag übernehmen'
              : modal === 'production-cert'
                ? 'Produktionszertifikat anfordern'
                : "Let's-Encrypt-Zertifikat"
          }
          onClose={() => setModal(null)}
        >
          <CertificateRequestForm
            cap={cap}
            domains={Array.from(
              new Set([
                ...doc.hosts.flatMap(hostnames),
                ...(doc.imported_routes || []).flatMap(hostnames),
                ...doc.rules
                  .filter((r) => r.enabled && r.match === 'host' && r.action === 'redirect')
                  .map((r) => r.value),
              ]),
            )}
            busy={busy}
            adopt={modal === 'adopt-cert'}
            productionFrom={modal === 'production-cert' ? certificateEdit : null}
            productionNames={certs.filter((c) => !c.staging).map((c) => c.name)}
            onClose={() => setModal(null)}
            onSave={(value) =>
              controlledSave(async () => {
                await modalRequest(
                  `/instances/${modalTarget!.id}/certificates/${modal === 'adopt-cert' ? 'adopt-lego' : 'issue'}`,
                  'POST',
                  value,
                );
                setCerts(await modalRequest(`/instances/${modalTarget!.id}/certificates`));
                setCap(await modalRequest(`/instances/${modalTarget!.id}/capabilities`));
                setModal(null);
              }, 'Zertifikat und ACME-Auftrag gespeichert.')
            }
          />
        </Modal>
      )}
      {modal === 'renewal-settings' && renewalSettings && (
        <Modal title="Zeitplan für Zertifikatserneuerung" onClose={() => setModal(null)}>
          <RenewalSettingsForm
            initial={renewalSettings}
            busy={busy}
            onClose={() => setModal(null)}
            onSave={(value) =>
              controlledSave(async () => {
                setRenewalSettings(
                  await modalRequest(
                    `/instances/${modalTarget!.id}/certificates/renewal-settings`,
                    'PUT',
                    value,
                  ),
                );
                setModal(null);
              }, 'Zeitplan auf dem Agenten gespeichert.')
            }
          />
        </Modal>
      )}
      {modal === 'certificate-policy' && certificateEdit && (
        <Modal title={'Automatische Erneuerung · ' + certificateEdit.name} onClose={() => setModal(null)}>
          <CertificatePolicyForm
            cert={certificateEdit}
            busy={busy}
            onClose={() => setModal(null)}
            onSave={(value) =>
              controlledSave(async () => {
                await modalRequest(
                  `/instances/${modalTarget!.id}/certificates/${certificateEdit.name}/policy`,
                  'PUT',
                  value,
                );
                setCerts(await modalRequest(`/instances/${modalTarget!.id}/certificates`));
                setModal(null);
              }, 'Automatische Erneuerung gespeichert.')
            }
          />
        </Modal>
      )}
      {modal === 'frontend' && (
        <Modal
          title={frontendEdit ? 'Frontend bearbeiten' : 'Frontend hinzufügen'}
          onClose={() => setModal(null)}
        >
          <FrontendForm
            initial={frontendEdit}
            onRemove={
              frontendEdit
                ? () =>
                    void removeFrontend(frontendEdit.name, () => {
                      setModal(null);
                      setFrontendEdit(null);
                    })
                : undefined
            }
            backends={layout?.backends || []}
            busy={busy}
            onClose={() => setModal(null)}
            onSave={(value) =>
              controlledSave(async () => {
                await saveDoc({
                  ...doc,
                  frontends: frontendEdit
                    ? (doc.frontends || []).map((f) => (f.name === frontendEdit.name ? value : f))
                    : [...(doc.frontends || []), value],
                });
                setLayout(await modalRequest(`/instances/${modalTarget!.id}/proxy-layout`));
                setModal(null);
              }, 'Frontend gespeichert – noch nicht aktiv.')
            }
          />
        </Modal>
      )}
      {modal === 'backend' && (
        <Modal
          wide
          title={poolEdit ? 'Backend bearbeiten' : 'Backend hinzufügen'}
          onClose={() => setModal(null)}
        >
          <BackendForm
            initial={poolEdit}
            busy={busy}
            onClose={() => setModal(null)}
            onSave={(value) =>
              controlledSave(async () => {
                await saveDoc({
                  ...doc,
                  backends: poolEdit
                    ? (doc.backends || []).map((b) => (b.name === poolEdit.name ? value : b))
                    : [...(doc.backends || []), value],
                });
                setLayout(await modalRequest(`/instances/${modalTarget!.id}/proxy-layout`));
                setModal(null);
              }, 'Backend gespeichert – noch nicht aktiv.')
            }
          />
        </Modal>
      )}
      {modal === 'frontend-certificates' && (
        <Modal title={'Zertifikate · ' + certificateFrontend} onClose={() => setModal(null)}>
          <FrontendCertificateForm
            name={certificateFrontend}
            initial={
              doc.frontend_certificates?.[certificateFrontend] ||
              Array.from(
                new Set(
                  (layout?.frontends.find((f) => f.name === certificateFrontend)?.binds || []).flatMap((b) =>
                    Array.from(b.matchAll(/(?:^|\s)crt\s+\S*\/([a-zA-Z0-9_-]+)\.pem(?:\s|$)/g), (m) => m[1]),
                  ),
                ),
              )
            }
            certs={certs}
            busy={busy}
            onClose={() => setModal(null)}
            onSave={(names) =>
              controlledSave(async () => {
                await saveDoc({
                  ...doc,
                  frontend_certificates: { ...doc.frontend_certificates, [certificateFrontend]: names },
                });
                setLayout(await modalRequest(`/instances/${modalTarget!.id}/proxy-layout`));
                setModal(null);
              }, 'Zertifikatszuweisung gespeichert – noch nicht aktiv.')
            }
          />
        </Modal>
      )}

      {modal === 'pem' && (
        <PemForm
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(value) =>
            run(async () => {
              await modalRequest(`/instances/${modalTarget!.id}/certificates/import`, 'POST', value);
              setCerts(await modalRequest(`/instances/${modalTarget!.id}/certificates`));
              setModal(null);
            }, 'Zertifikat importiert und Reload bestätigt.')
          }
        />
      )}
      {modal === 'user' && (
        <UserForm
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(value) =>
            run(async () => {
              await api('/users', 'POST', value);
              setUsers(await api('/users'));
              setModal(null);
            }, 'Benutzer angelegt.')
          }
        />
      )}
      {modal === 'password' && (
        <Modal title="Passwort ändern" onClose={() => setModal(null)}>
          <PasswordForm
            onDone={() => {
              setModal(null);
              setUser(null);
              setCsrf('');
            }}
          />
        </Modal>
      )}
      {confirm && (
        <Modal title={confirm.title} onClose={() => !busy && setConfirm(null)}>
          <p className="modal-copy">{confirm.text}</p>
          <div className="modal-actions">
            <Button secondary disabled={busy} onClick={() => setConfirm(null)}>
              Abbrechen
            </Button>
            <Button
              danger={confirm.danger}
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await confirm.fn();
                  setConfirm(null);
                })
              }
            >
              {busy ? <Loader2 size={16} className="spin" /> : <Check size={16} />}Bestätigen
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}

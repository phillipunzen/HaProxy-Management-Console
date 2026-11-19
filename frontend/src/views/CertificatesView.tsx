import { CertificatesPanel } from '../Certificates';
import { api } from '../api';
import { type AppProps } from '../app/useAppState';

export function CertificatesView({ app }: AppProps) {
  const {
    selected,
    cap,
    certs,
    setCerts,
    busy,
    renewalSettings,
    setRenewalSettings,
    setCertificateEdit,
    resourceReady,
    setConfirm,
    setModal,
    current,
    canEdit,
    notify,
    run,
    checkRenewal,
  } = app;
  if (!selected) return null;
  return (
    <CertificatesPanel
      certs={certs}
      cap={cap}
      schedule={renewalSettings}
      busy={busy || !resourceReady}
      canEdit={canEdit}
      onRequest={() => setModal('cert')}
      onProduction={(cert) => {
        setCertificateEdit(cert);
        setModal('production-cert');
      }}
      onAdopt={() => setModal('adopt-cert')}
      onPem={() => setModal('pem')}
      onSchedule={() => setModal('renewal-settings')}
      onRenew={(name) => run(() => checkRenewal(name))}
      onForce={(name) =>
        setConfirm({
          title: 'Zertifikat jetzt erneuern',
          text:
            'Zielserver: ' +
            current?.name +
            '. Für ' +
            name +
            ' wird sofort ein neues Zertifikat angefordert, auch wenn es noch nicht fällig ist. Dabei gelten die Let’s-Encrypt-Ausstellungslimits.',
          fn: () => checkRenewal(name, true),
        })
      }
      onPolicy={(cert) => {
        setCertificateEdit(cert);
        setModal('certificate-policy');
      }}
      onDelete={(cert) => {
        const id = selected;
        setConfirm({
          title: (cert.staging ? 'Staging-Zertifikat' : 'Produktionszertifikat') + ' löschen',
          text:
            'Zielserver: ' +
            current?.name +
            '. Zertifikat „' +
            cert.name +
            '“ und sein Erneuerungsauftrag werden gelöscht. Zugewiesene oder von HAProxy geladene Zertifikate sind geschützt. Gespeicherte DNS-Zugänge und das gleichnamige ' +
            (cert.staging ? 'Produktionszertifikat' : 'Staging-Zertifikat') +
            ' bleiben erhalten.',
          danger: true,
          fn: async () => {
            await api(
              `/instances/${id}/certificates/${encodeURIComponent(cert.name)}?staging=${cert.staging}`,
              'DELETE',
            );
            setCerts(await api(`/instances/${id}/certificates`));
            setRenewalSettings(await api(`/instances/${id}/certificates/renewal-settings`));
            notify('Zertifikat gelöscht.');
          },
        });
      }}
    />
  );
}

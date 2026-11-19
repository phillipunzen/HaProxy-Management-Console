import { AccessProfileManager } from '../AccessProfiles';
import { type AppProps } from '../app/useAppState';

export function AccessProfilesView({ app }: AppProps) {
  const { doc, busy, setAccessProfileEdit, resourceReady, setModal, saveDoc, openApply, confirmDelete } = app;
  return (
    <AccessProfileManager
      profiles={doc.access_profiles || []}
      entries={[...doc.hosts, ...(doc.imported_routes || [])]}
      busy={busy || !resourceReady}
      onCreate={(preset) => {
        setAccessProfileEdit(preset || null);
        setModal('access-profile');
      }}
      onEdit={(profile) => {
        setAccessProfileEdit(profile);
        setModal('access-profile');
      }}
      onDelete={(profile) =>
        confirmDelete('IP-Filter-Profil ' + profile.name + ' löschen', async () =>
          saveDoc({
            ...doc,
            access_profiles: (doc.access_profiles || []).filter((p) => p.id !== profile.id),
          }),
        )
      }
      onApply={() => openApply()}
    />
  );
}

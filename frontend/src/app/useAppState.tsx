import { hostBackend } from '../format';
import { useEffect, useRef, useState } from 'react';
import { type AccessProfile } from '../AccessProfiles';
import { type BasicDirectory } from '../BasicAuth';
import { type RenewalSettings } from '../Certificates';
import { type ImportedFields, type ImportedBackend, type ImportedRoute } from '../ConfigImport';
import { hostnames } from '../Hostnames';
import { type Infrastructure } from '../Infrastructures';
import { useProxyHealth } from '../ProxyHealth';
import { type Frontend, type Pool, type Layout } from '../ProxyLayout';
import { manualAccessSections, summarizeAccess, type AccessFilter } from '../accessVisibility';
import { api, setCsrf } from '../api';
import { defaultDoc } from './constants';
import {
  type User,
  type Stats,
  type Instance,
  type Host,
  type Rule,
  type Doc,
  type Cert,
  type Revision,
  type Cap,
  type Metric,
  type MetricStorage,
  type PendingChanges,
  type ApplyRequest,
} from '../types';

export function useAppState() {
  const [user, setUser] = useState<User | null>(null),
    [checking, setChecking] = useState(true),
    [view, setView] = useState(location.hash.slice(1) || 'overview'),
    [mobile, setMobile] = useState(false);
  const [instances, setInstances] = useState<Instance[]>([]),
    [selected, setSelected] = useState<number | null>(null),
    [doc, setDoc] = useState<Doc>(defaultDoc),
    [cap, setCap] = useState<Cap | null>(null),
    [stats, setStats] = useState<Stats | null>(null),
    [metrics, setMetrics] = useState<Metric[]>([]),
    [hours, setHours] = useState(1),
    [certs, setCerts] = useState<Cert[]>([]);
  const [config, setConfig] = useState(''),
    [active, setActive] = useState(''),
    [hash, setHash] = useState(''),
    [revisions, setRevisions] = useState<Revision[]>([]),
    [rev, setRev] = useState<number | null>(null),
    [message, setMessage] = useState(''),
    [validation, setValidation] = useState(''),
    [generatedVersion, setGeneratedVersion] = useState<number | null>(null),
    [loaded, setLoaded] = useState(false);
  const [audit, setAudit] = useState<any[]>([]),
    [users, setUsers] = useState<User[]>([]),
    [search, setSearch] = useState(''),
    [toast, setToast] = useState<{ text: string; error: boolean } | null>(null),
    [busy, setBusy] = useState(false),
    [modal, setModalState] = useState<string | null>(null),
    [confirm, setConfirmState] = useState<{
      title: string;
      text: string;
      fn: () => Promise<void>;
      danger?: boolean;
    } | null>(null);
  const [hostEdit, setHostEdit] = useState<Host | null>(null),
    [ruleEdit, setRuleEdit] = useState<Rule | null>(null),
    [instanceEdit, setInstanceEdit] = useState<Instance | null>(null),
    [resourceError, setResourceError] = useState('');
  const [accessProfileEdit, setAccessProfileEdit] = useState<AccessProfile | null>(null);
  const [accessFilter, setAccessFilter] = useState<AccessFilter>('all');
  const [ruleDomainMode, setRuleDomainMode] = useState(false);
  const [importedBackendEdit, setImportedBackendEdit] = useState<ImportedBackend | null>(null),
    [importedRouteEdit, setImportedRouteEdit] = useState<ImportedRoute | null>(null),
    [importedRouteIndex, setImportedRouteIndex] = useState<number | null>(null);
  const [renewalSettings, setRenewalSettings] = useState<RenewalSettings | null>(null),
    [certificateEdit, setCertificateEdit] = useState<Cert | null>(null);
  const [layout, setLayout] = useState<Layout | null>(null),
    [frontendEdit, setFrontendEdit] = useState<Frontend | null>(null),
    [poolEdit, setPoolEdit] = useState<Pool | null>(null),
    [certificateFrontend, setCertificateFrontend] = useState('');
  const [authDirectory, setAuthDirectory] = useState<BasicDirectory | null>(null);
  const [changes, setChanges] = useState<PendingChanges | null>(null),
    [changesTick, setChangesTick] = useState(0),
    [applyRequest, setApplyRequest] = useState<ApplyRequest | null>(null);
  const [topologyRefresh, setTopologyRefresh] = useState(0);
  const [updateCommand, setUpdateCommand] = useState(''),
    [metricStorage, setMetricStorage] = useState<MetricStorage | null>(null);
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]),
    [scope, setScope] = useState(''),
    [modalTarget, setModalTarget] = useState<Instance | null>(null),
    [loadedFor, setLoadedFor] = useState('');
  const scopeRef = useRef(scope),
    interactionLocked = useRef(false);
  scopeRef.current = scope;
  interactionLocked.current = busy || !!modal || !!confirm;
  const inScope = (i: Instance, value = scope) =>
    !value || (value === 'none' ? i.infrastructure_id == null : String(i.infrastructure_id) === value);
  const scoped = instances.filter((i) => inScope(i));
  const resourceReady =
    loaded &&
    instances.some((i) => i.id === selected && inScope(i)) &&
    loadedFor === `${view}:${selected}` &&
    !resourceError;
  const proxyHealth = useProxyHealth(
    selected,
    doc.version,
    view === 'hosts' && resourceReady && !!user && !user.must_change_password,
    api,
  );
  function setConfirm(value: typeof confirm) {
    interactionLocked.current = !!value || busy || !!modal;
    setConfirmState(value);
  }
  function setModal(value: string | null) {
    if (value) setToast(null);
    if (
      value &&
      [
        'host',
        'rule',
        'listeners',
        'cert',
        'production-cert',
        'adopt-cert',
        'renewal-settings',
        'certificate-policy',
        'frontend',
        'backend',
        'frontend-certificates',
        'pem',
        'import',
        'imported-backend',
        'imported-route',
      ].includes(value)
    ) {
      if (!resourceReady || !current) return;
      setModalTarget({ ...current });
    } else setModalTarget(null);
    interactionLocked.current = !!value || busy || !!confirm;
    setModalState(value);
  }
  const current = instances.find((i) => i.id === selected),
    canEdit = user?.role === 'admin' || user?.role === 'operator',
    isAdmin = user?.role === 'admin';
  function notify(text: string, error = false) {
    setToast({ text, error });
  }
  async function run(fn: () => Promise<void>, success?: string) {
    interactionLocked.current = true;
    setBusy(true);
    try {
      await fn();
      if (success) notify(success);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  function commitNavigate(v: string) {
    setView(v);
    location.hash = v;
    setMobile(false);
    setSearch('');
  }
  const dirty = view === 'config' && loaded && !!hash && config !== active && !rev;
  function navigate(v: string) {
    if (interactionLocked.current) {
      notify('Bitte zuerst den geöffneten Dialog oder die laufende Aktion abschließen.');
      return;
    }
    if (v !== view && dirty) {
      setConfirm({
        title: 'Ungespeicherten Entwurf verlassen',
        text: 'Änderungen im Konfigurationseditor werden verworfen. Speichere sie vorher als Version, wenn du sie später weiterbearbeiten möchtest.',
        fn: async () => commitNavigate(v),
      });
    } else commitNavigate(v);
  }
  function chooseInstance(id: number) {
    if (interactionLocked.current || id === selected || !scoped.some((i) => i.id === id)) return;
    if (dirty)
      setConfirm({
        title: 'Instanz wechseln',
        text: 'Ungespeicherte Änderungen im Konfigurationseditor werden verworfen.',
        fn: async () => setSelected(id),
      });
    else setSelected(id);
  }

  function chooseScope(value: string) {
    if (interactionLocked.current) return;
    const apply = async () => {
      scopeRef.current = value;
      setScope(value);
      setSelected((old) =>
        instances.some((i) => i.id === old && inScope(i, value))
          ? old
          : (instances.find((i) => inScope(i, value))?.id ?? null),
      );
    };
    if (dirty)
      setConfirm({
        title: 'Infrastruktur wechseln',
        text: 'Ungespeicherte Änderungen im Konfigurationseditor werden verworfen.',
        fn: apply,
      });
    else void apply();
  }
  async function loadInstances() {
    const [values, groups] = await Promise.all([
      api<Instance[]>('/instances'),
      api<Infrastructure[]>('/infrastructures'),
    ]);
    const normalized = values.map((i) => ({
      ...i,
      tags: i.tags || [],
      location: i.location || '',
      metadata_version: i.metadata_version || 0,
      infrastructure_id: i.infrastructure_id ?? null,
      infrastructure_name: i.infrastructure_name ?? null,
    }));
    setInstances(normalized);
    setInfrastructures(groups);
    setSelected((old) =>
      interactionLocked.current && normalized.some((i) => i.id === old)
        ? old
        : normalized.some((i) => i.id === old && inScope(i, scopeRef.current))
          ? old
          : (normalized.find((i) => inScope(i, scopeRef.current))?.id ?? null),
    );
  }

  async function loadBasicAuth() {
    setAuthDirectory(await api<BasicDirectory>('/basic-auth'));
  }
  async function loadSettings() {
    const [people, storage] = await Promise.all([
      api<User[]>('/users'),
      api<MetricStorage>('/metrics/storage'),
    ]);
    setUsers(people);
    setMetricStorage(storage);
  }
  async function reloadPage() {
    await loadInstances();
    if (view === 'topology') setTopologyRefresh((v) => v + 1);
    if (view === 'basic-auth' && canEdit) await loadBasicAuth();
    else if (view === 'settings' && isAdmin) await loadSettings();
    else if (selected) await loadResource(selected, view);
  }
  const selectedRef = useRef(selected),
    viewRef = useRef(view),
    pendingGenerated = useRef<{
      id: number;
      config: string;
      base_hash: string;
      document_version?: number;
    } | null>(null);
  selectedRef.current = selected;
  viewRef.current = view;
  async function loadResource(id: number, v: string) {
    const paths: string[] = [];
    if (v === 'overview' || v === 'statistics')
      paths.push(`/instances/${id}/stats`, `/instances/${id}/metrics?hours=${hours}`);
    if (v === 'access-profiles' && canEdit) paths.push(`/instances/${id}/document`);
    if (['hosts', 'proxies', 'rules', 'config', 'certificates'].includes(v)) {
      paths.push(`/instances/${id}/capabilities`);
      if (canEdit) paths.push(`/instances/${id}/document`);
    }
    if (['hosts', 'proxies'].includes(v) && canEdit) paths.push('/basic-auth');
    if (v === 'config') paths.push(`/instances/${id}/config`, `/instances/${id}/revisions`);
    if (['certificates', 'hosts', 'proxies'].includes(v)) paths.push(`/instances/${id}/certificates`);
    if (['hosts', 'proxies', 'rules'].includes(v)) paths.push(`/instances/${id}/proxy-layout`);
    const results = await Promise.allSettled(paths.map((p) => api(p)));
    const capabilities = results[paths.indexOf(`/instances/${id}/capabilities`)];
    if (
      v === 'certificates' &&
      capabilities?.status === 'fulfilled' &&
      capabilities.value.certificate_management
    ) {
      const settings = await api<RenewalSettings>(`/instances/${id}/certificates/renewal-settings`);
      if (selectedRef.current === id && viewRef.current === v) setRenewalSettings(settings);
    }
    if (selectedRef.current !== id || viewRef.current !== v) return;
    for (const r of results) if (r.status === 'rejected') throw r.reason;
    setResourceError('');
    setLoadedFor(`${v}:${id}`);
    results.forEach((r, index) => {
      if (r.status !== 'fulfilled') return;
      const path = paths[index],
        data = r.value;
      if (path.endsWith('/stats')) {
        setStats(data);
        setInstances((old) =>
          old.map((i) => (i.id === id ? { ...i, stats: data, collected_at: new Date().toISOString() } : i)),
        );
      } else if (path.includes('/metrics?')) setMetrics(data);
      else if (path.endsWith('/capabilities')) setCap(data);
      else if (path.endsWith('/document')) setDoc(data);
      else if (path === '/basic-auth') setAuthDirectory(data);
      else if (path.endsWith('/config')) {
        setActive(data.config);
        const pending = pendingGenerated.current;
        setConfig(pending?.id === id ? pending.config : data.config);
        setHash(pending?.id === id ? pending.base_hash : data.hash);
        setGeneratedVersion(pending?.id === id ? (pending.document_version ?? null) : null);
        pendingGenerated.current = null;
        setRev(null);
        setValidation('');
      } else if (path.endsWith('/revisions')) setRevisions(data);
      else if (path.endsWith('/certificates')) setCerts(data);
      else if (path.endsWith('/proxy-layout')) setLayout(data);
    });
  }
  useEffect(() => {
    api('/auth/me')
      .then((r) => {
        setCsrf(r.csrf);
        setUser(r.user);
      })
      .catch(() => {})
      .finally(() => setChecking(false));
    const expired = () => {
      setCsrf('');
      setUser(null);
      notify('Die Sitzung ist abgelaufen. Bitte erneut anmelden.', true);
    };
    window.addEventListener('session-expired', expired);
    const hashChange = () => {
      const next = location.hash.slice(1) || 'overview';
      if (interactionLocked.current && next !== viewRef.current) {
        history.replaceState(null, '', '#' + viewRef.current);
        return;
      }
      setView(next);
    };
    window.addEventListener('hashchange', hashChange);
    return () => {
      window.removeEventListener('session-expired', expired);
      window.removeEventListener('hashchange', hashChange);
    };
  }, []);
  useEffect(() => {
    if (user && !user.must_change_password) run(loadInstances);
  }, [user?.id, user?.must_change_password]);
  useEffect(() => {
    setLoaded(false);
    setLoadedFor('');
    setResourceError('');
    setStats(null);
    setMetrics([]);
    setCerts([]);
    setLayout(null);
    setRenewalSettings(null);
    setCap(null);
    setDoc(defaultDoc);
    setConfig('');
    setHash('');
    setValidation('');
    setRev(null);
    if (!user || user.must_change_password) return;
    let live = true;
    const job = async () => {
      try {
        if (view === 'basic-auth' && canEdit) await loadBasicAuth();
        else if (view === 'audit') setAudit(await api('/audit'));
        else if (view === 'settings' && isAdmin) await loadSettings();
        else if (selected) await loadResource(selected, view);
      } catch (e) {
        if (live) setResourceError((e as Error).message);
      } finally {
        if (live) setLoaded(true);
      }
    };
    job();
    return () => {
      live = false;
    };
  }, [selected, view, hours, user?.id, user?.must_change_password]);
  useEffect(() => {
    if (!user || user.must_change_password) return;
    const interval = setInterval(() => {
      loadInstances().catch(() => {});
      if (selected && ['overview', 'statistics'].includes(view))
        loadResource(selected, view).catch((e) => setResourceError(e.message));
    }, 30000);
    return () => clearInterval(interval);
  }, [user, selected, view, hours]);
  useEffect(() => setAccessFilter('all'), [selected]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    if (toast && !toast.error) {
      const timer = setTimeout(() => setToast(null), 6000);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  useEffect(() => {
    if (!interactionLocked.current)
      setSelected((old) =>
        instances.some((i) => i.id === old && inScope(i)) ? old : (scoped[0]?.id ?? null),
      );
  }, [scope, instances, modal, busy, confirm]);
  useEffect(() => {
    if (modalTarget && selected !== modalTarget.id) {
      setModal(null);
      notify(
        'Der Zielserver ist nicht mehr ausgewählt. Bitte den Dialog auf dem gewünschten Server erneut öffnen.',
        true,
      );
    }
  }, [selected]);
  async function saveDoc(value: Doc, id = modalTarget?.id ?? selected) {
    if (!id || id !== selectedRef.current)
      throw new Error('Zielserver wurde geändert. Bitte den Entwurf erneut laden.');
    const saved = await api<Doc>(`/instances/${id}/document`, 'PUT', value);
    if (selectedRef.current === id) setDoc(saved);
  }
  async function saveImportedFields(changes: ImportedFields & { backends?: Pool[] }) {
    interactionLocked.current = true;
    setBusy(true);
    try {
      await saveDoc({ ...doc, ...changes });
    } finally {
      setBusy(false);
    }
  }
  async function modalRequest(path: string, method = 'GET', body?: unknown) {
    if (!modalTarget || selectedRef.current !== modalTarget.id)
      throw new Error('Zielserver wurde geändert. Bitte den Dialog erneut öffnen.');
    return api(path, method, body);
  }

  async function controlledSave(fn: () => Promise<void>, success: string) {
    interactionLocked.current = true;
    setBusy(true);
    try {
      await fn();
      notify(success);
    } finally {
      setBusy(false);
    }
  }
  async function checkRenewal(name?: string, force = false) {
    const result = await api(`/instances/${selected}/certificates/renew`, 'POST', {
      name: name || null,
      force,
    });
    setCerts(await api(`/instances/${selected}/certificates`));
    if (cap?.certificate_management)
      setRenewalSettings(await api(`/instances/${selected}/certificates/renewal-settings`));
    notify(
      `${result.checked} Zertifikate geprüft, ${result.renewed.length} erneuert.` +
        (result.errors?.length
          ? ' ' + result.errors.map((e: any) => e.name + ': ' + e.error).join(' · ')
          : ''),
      !!result.errors?.length,
    );
  }
  async function generate() {
    if (!selected) return;
    const result = await api(`/instances/${selected}/generate`, 'POST');
    pendingGenerated.current = { id: selected, ...result };
    commitNavigate('config');
    notify('Konfiguration erzeugt. Änderungen prüfen und anschließend anwenden.');
  }
  const generateConfirm = () =>
    setConfirm({
      title: 'Konfiguration aus Entwurf erzeugen',
      text:
        'Zielserver: ' +
        current?.name +
        '. ' +
        (doc.imported_config != null
          ? 'Die übernommenen Domain-Routen und Backend-Ziele werden in die eingelesene Konfiguration eingesetzt. Übrige Einstellungen bleiben erhalten. Vergleiche den Entwurf vor dem Anwenden.'
          : 'Der grafische Entwurf erzeugt eine vollständige Konfiguration. Bestehende manuelle Einstellungen werden dabei nicht übernommen. Du kannst beide Fassungen vergleichen, bevor du sie anwendest.'),
      fn: generate,
    });
  async function finishApply(id: number, targetView: string, result: any) {
    const success = 'Konfiguration angewendet und Reload bestätigt.';
    setChangesTick((t) => t + 1);
    try {
      await loadResource(id, targetView);
      await loadInstances();
      notify(
        result.sync_warning
          ? success + ' ' + result.sync_warning
          : success + ' Ansicht automatisch abgeglichen.',
        !!result.sync_warning,
      );
    } catch (e) {
      notify(success + ' Die Ansicht konnte noch nicht aktualisiert werden: ' + (e as Error).message, true);
    }
  }
  /** Opens the single apply dialog (diff, validation, note) for the draft or a text config. */
  function openApply(request: Partial<ApplyRequest> = {}) {
    const id = request.id ?? selected;
    if (!id) return;
    setApplyRequest({ ...request, id });
    setModal('apply');
  }
  async function revertChange(kind: string, key: string) {
    if (!selected || !changes) return;
    const saved = await api<Doc>(`/instances/${selected}/changes/revert`, 'POST', {
      kind,
      key,
      version: changes.version,
    });
    if (selectedRef.current === selected) setDoc(saved);
    setChangesTick((t) => t + 1);
  }
  function service(action: string) {
    setConfirm({
      title:
        action === 'stop'
          ? 'HAProxy stoppen'
          : action === 'restart'
            ? 'HAProxy neu starten'
            : action === 'start'
              ? 'HAProxy starten'
              : 'Konfiguration neu laden',
      text:
        action === 'stop' || action === 'restart'
          ? 'Aktive Verbindungen können dabei unterbrochen werden. Die Aktion betrifft ' + current?.name + '.'
          : 'Die Aktion wird auf ' + current?.name + ' ausgeführt.',
      danger: action === 'stop',
      fn: async () => {
        await api(`/instances/${selected}/service/${action}`, 'POST');
        await reloadPage();
        notify('Dienstaktion erfolgreich.');
      },
    });
  }
  async function removeBackend(name: string, onDeleted?: () => void) {
    if (!selected || busy || !resourceReady) return;
    const id = selected;
    await run(async () => {
      const preview = await api<{
        name: string;
        version: number;
        domains: string[];
        listeners: string[];
        references: string[];
      }>(`/instances/${id}/backend-delete-preview`, 'POST', { name, version: doc.version });
      if (selectedRef.current !== id) throw new Error('Zielserver wurde geändert. Bitte erneut laden.');
      const list = (values: string[]) =>
        values.slice(0, 20).join(', ') + (values.length > 20 ? ` und ${values.length - 20} weitere` : '');
      const impact = [
        preview.domains.length
          ? `${preview.domains.length} Domain(s) werden aus dem Entwurf entfernt: ${list(preview.domains)}.`
          : '',
        preview.listeners.length
          ? `Diese Listener werden ebenfalls entfernt: ${list(preview.listeners)}.`
          : '',
        preview.references.length
          ? `Diese Weiterleitungen werden entfernt: ${list(preview.references)}. HTTP-Anfragen ohne passende Route erhalten die HAProxy-Standardantwort, TCP-Verbindungen ohne Ziel werden beendet.`
          : '',
      ]
        .filter(Boolean)
        .join(' ');
      setConfirm({
        title: 'Backend ' + name + ' löschen',
        text:
          'Zielserver: ' +
          current?.name +
          '. ' +
          impact +
          ' Der Pool wird im Entwurf gelöscht. Die laufende Konfiguration ändert sich erst nach Prüfen & anwenden.',
        danger: true,
        fn: async () => {
          if (selectedRef.current !== id) throw new Error('Zielserver wurde geändert. Bitte erneut laden.');
          const saved = await api<Doc>(`/instances/${id}/backend-delete`, 'POST', {
            name,
            version: preview.version,
          });
          setDoc(saved);
          pendingGenerated.current = null;
          onDeleted?.();
          if (viewRef.current === 'proxies') {
            try {
              setLayout(await api(`/instances/${id}/proxy-layout`));
            } catch {
              notify('Backend gelöscht. Übersicht bitte neu laden.', true);
              return;
            }
          }
          notify('Backend im Entwurf gelöscht. Zum Aktivieren prüfen & anwenden.');
        },
      });
    });
  }
  async function removeFrontend(name: string, onDeleted?: () => void) {
    if (!selected || busy || !resourceReady) return;
    const id = selected;
    await run(async () => {
      const preview = await api<{
        name: string;
        kind: string;
        version: number;
        domains: string[];
        rules: string[];
        listeners: string[];
        remaining_listeners: string[];
        references: string[];
        backend_removed: boolean;
      }>(`/instances/${id}/frontend-delete-preview`, 'POST', { name, version: doc.version });
      if (selectedRef.current !== id) throw new Error('Zielserver wurde geändert. Bitte erneut laden.');
      const list = (values: string[]) =>
        values.slice(0, 20).join(', ') + (values.length > 20 ? ` und ${values.length - 20} weitere` : '');
      const impact = [
        !preview.remaining_listeners.length
          ? 'Es bleibt kein Listener im Entwurf. Vor Prüfen & anwenden ein neues Frontend anlegen.'
          : '',
        preview.domains.length
          ? `${preview.domains.length} Domain(s) werden aus dem Entwurf entfernt: ${list(preview.domains)}.`
          : '',
        preview.rules.length ? `Diese Regeln werden entfernt: ${list(preview.rules)}.` : '',
        preview.listeners.length > 1 ? `Diese Listener werden entfernt: ${list(preview.listeners)}.` : '',
        preview.backend_removed
          ? 'Dieser listen-Block ist gleichzeitig ein Backend; beide Teile werden entfernt.'
          : 'Eigenständige Backend-Pools bleiben erhalten.',
        preview.references.length
          ? `Diese Weiterleitungen werden entfernt: ${list(preview.references)}.`
          : '',
      ]
        .filter(Boolean)
        .join(' ');
      setConfirm({
        title: 'Frontend ' + name + ' löschen',
        text:
          'Zielserver: ' +
          current?.name +
          '. ' +
          impact +
          ' Der Listener mit seinen Zuordnungen und Regeln wird im Entwurf gelöscht. Nach Prüfen & anwenden nimmt HAProxy auf seinen Ports keine Verbindungen mehr an, sofern dort kein anderer Listener lauscht.',
        danger: true,
        fn: async () => {
          if (selectedRef.current !== id) throw new Error('Zielserver wurde geändert. Bitte erneut laden.');
          const saved = await api<Doc>(`/instances/${id}/frontend-delete`, 'POST', {
            name,
            version: preview.version,
          });
          setDoc(saved);
          pendingGenerated.current = null;
          onDeleted?.();
          try {
            setLayout(await api(`/instances/${id}/proxy-layout`));
          } catch {
            notify('Frontend gelöscht. Übersicht bitte neu laden.', true);
            return;
          }
          notify('Frontend im Entwurf gelöscht. Zum Aktivieren prüfen & anwenden.');
        },
      });
    });
  }
  /** Removing a draft item is reversible (pending changes → undo), so no confirmation dialog. */
  function confirmDelete(title: string, fn: () => Promise<void>) {
    void run(
      fn,
      title.replace(/ löschen$/, ' entfernt') +
        '. Noch nicht aktiv – rückgängig über „Änderungen“ in der Titelleiste.',
    );
  }
  async function saveDraft() {
    const result = await api(`/instances/${selected}/revisions`, 'POST', {
      config,
      base_hash: hash,
      message,
    });
    setRev(result.id);
    setRevisions(await api(`/instances/${selected}/revisions`));
    return result.id;
  }
  useEffect(() => {
    if (!user || user.must_change_password || !selected || !canEdit) {
      setChanges(null);
      return;
    }
    let alive = true;
    api<PendingChanges>(`/instances/${selected}/changes`)
      .then((r) => alive && setChanges(r))
      .catch(() => alive && setChanges(null));
    return () => {
      alive = false;
    };
  }, [selected, doc.version, changesTick, canEdit, user]);
  const online = scoped.filter(
    (i) =>
      i.stats?.online &&
      i.collected_at &&
      Date.now() - Date.parse(i.collected_at) < (i.fresh_for_seconds || 90) * 1000,
  );
  const total = (key: keyof Stats) =>
    online.length ? online.reduce((sum, i) => sum + Number(i.stats?.[key] || 0), 0) : undefined;
  const manualAccess = manualAccessSections(doc.imported_config);
  const hostAccess = Object.fromEntries(
    doc.hosts.map((h) => [
      h.id,
      summarizeAccess(
        h,
        doc.access_profiles,
        manualAccess.has('*') ||
          manualAccess.has(h.frontend || 'public_http') ||
          manualAccess.has(hostBackend(h)),
      ),
    ]),
  );
  const routeAccess = Object.fromEntries(
    (doc.imported_routes || []).map((r) => [
      r.id,
      summarizeAccess(
        r,
        doc.access_profiles,
        manualAccess.has('*') || manualAccess.has(r.frontend) || manualAccess.has(r.backend),
      ),
    ]),
  );
  const visibleRoute = (r: ImportedRoute) =>
    (accessFilter === 'all' || routeAccess[r.id]?.kind === accessFilter) &&
    (hostnames(r).some((name) => name.toLowerCase().includes(search.toLowerCase())) ||
      r.backend.toLowerCase().includes(search.toLowerCase()));
  const filteredHosts = doc.hosts.filter(
    (h) =>
      (accessFilter === 'all' || hostAccess[h.id].kind === accessFilter) &&
      (hostnames(h).some((name) => name.toLowerCase().includes(search.toLowerCase())) ||
        h.servers.some((s) => s.address.toLowerCase().includes(search.toLowerCase()))),
  );

  const requiresInstance = ![
    'overview',
    'servers',
    'infrastructures',
    'audit',
    'settings',
    'basic-auth',
  ].includes(view);
  return {
    user,
    setUser,
    checking,
    setChecking,
    view,
    setView,
    mobile,
    setMobile,
    instances,
    setInstances,
    selected,
    setSelected,
    doc,
    setDoc,
    cap,
    setCap,
    stats,
    setStats,
    metrics,
    setMetrics,
    hours,
    setHours,
    certs,
    setCerts,
    config,
    setConfig,
    active,
    setActive,
    hash,
    setHash,
    revisions,
    setRevisions,
    rev,
    setRev,
    message,
    setMessage,
    validation,
    setValidation,
    generatedVersion,
    setGeneratedVersion,
    loaded,
    setLoaded,
    audit,
    setAudit,
    users,
    setUsers,
    search,
    setSearch,
    toast,
    setToast,
    busy,
    setBusy,
    modal,
    setModalState,
    confirm,
    setConfirmState,
    hostEdit,
    setHostEdit,
    ruleEdit,
    setRuleEdit,
    instanceEdit,
    setInstanceEdit,
    resourceError,
    setResourceError,
    accessProfileEdit,
    setAccessProfileEdit,
    accessFilter,
    setAccessFilter,
    ruleDomainMode,
    setRuleDomainMode,
    importedBackendEdit,
    setImportedBackendEdit,
    importedRouteEdit,
    setImportedRouteEdit,
    importedRouteIndex,
    setImportedRouteIndex,
    renewalSettings,
    setRenewalSettings,
    certificateEdit,
    setCertificateEdit,
    layout,
    setLayout,
    frontendEdit,
    setFrontendEdit,
    poolEdit,
    setPoolEdit,
    certificateFrontend,
    setCertificateFrontend,
    authDirectory,
    setAuthDirectory,
    topologyRefresh,
    setTopologyRefresh,
    updateCommand,
    setUpdateCommand,
    metricStorage,
    setMetricStorage,
    infrastructures,
    setInfrastructures,
    scope,
    setScope,
    modalTarget,
    setModalTarget,
    loadedFor,
    setLoadedFor,
    scopeRef,
    interactionLocked,
    inScope,
    scoped,
    resourceReady,
    proxyHealth,
    setConfirm,
    setModal,
    current,
    canEdit,
    isAdmin,
    notify,
    run,
    commitNavigate,
    dirty,
    navigate,
    chooseInstance,
    chooseScope,
    loadInstances,
    loadBasicAuth,
    loadSettings,
    reloadPage,
    selectedRef,
    viewRef,
    pendingGenerated,
    loadResource,
    saveDoc,
    saveImportedFields,
    modalRequest,
    controlledSave,
    checkRenewal,
    generate,
    generateConfirm,
    finishApply,
    openApply,
    revertChange,
    changes,
    setChangesTick,
    applyRequest,
    setApplyRequest,
    service,
    removeBackend,
    removeFrontend,
    confirmDelete,
    saveDraft,
    online,
    total,
    manualAccess,
    hostAccess,
    routeAccess,
    visibleRoute,
    filteredHosts,
    requiresInstance,
  };
}

export type AppState = ReturnType<typeof useAppState>;
export type AppProps = { app: AppState & { user: User } };

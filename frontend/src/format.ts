export const fmt = (v?: number | null) => (v == null ? '—' : new Intl.NumberFormat('de-DE').format(v));
export const bytes = (v?: number) =>
  v == null
    ? '—'
    : v >= 1e9
      ? (v / 1e9).toFixed(2) + ' GB'
      : v >= 1e6
        ? (v / 1e6).toFixed(1) + ' MB'
        : v >= 1e3
          ? (v / 1e3).toFixed(1) + ' kB'
          : v + ' B';
export const date = (v?: string) =>
  v
    ? new Intl.DateTimeFormat('de-DE', {
        dateStyle: 'medium',
        timeStyle: 'short',
        timeZone: 'Europe/Berlin',
      }).format(new Date(v))
    : '—';
export const uid = () =>
  crypto.randomUUID
    ? crypto.randomUUID().replaceAll('-', '').slice(0, 12)
    : Math.random().toString(36).slice(2, 14);

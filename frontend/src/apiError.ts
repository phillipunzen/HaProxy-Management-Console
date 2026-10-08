const fallback = 'Anfrage fehlgeschlagen.';
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): value is ObjectValue => typeof value === 'object' && value !== null && !Array.isArray(value);

function detailText(value: unknown, depth = 0): string {
  if (depth > 6) return '';
  if (typeof value === 'string') return value.trim();
  if (Array.isArray(value)) return value.map(item => {
    const message = detailText(item, depth + 1);
    if (!message) return '';
    const location = object(item) && Array.isArray(item.loc)
      ? item.loc.slice(1).filter(part => typeof part === 'string' || typeof part === 'number').join('.') : '';
    return location ? `${location}: ${message}` : message;
  }).filter(Boolean).join('\n');
  if (object(value)) {
    for (const key of ['message', 'msg', 'detail']) {
      const message = detailText(value[key], depth + 1);
      if (message) return message;
    }
  }
  return '';
}

export function apiError(data: unknown): Error & { code?: string } {
  const body = object(data) ? data : {};
  const legacyDetail = object(body.detail) ? body.detail : {};
  const code = typeof body.code === 'string' ? body.code : typeof legacyDetail.code === 'string' ? legacyDetail.code : undefined;
  return Object.assign(new Error(detailText(body.detail) || detailText(body.message) || fallback), { code });
}

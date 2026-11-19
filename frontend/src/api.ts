import { apiError } from './apiError';

let csrf = '';
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await fetch('/api' + path, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    ...(method === 'GET' ? {} : { body: JSON.stringify(body ?? {}) }),
  });
  let data: any;
  try {
    data = await res.json();
  } catch {
    throw new Error('Der Server antwortet nicht korrekt. Bitte später erneut versuchen.');
  }
  if (!res.ok) {
    if (res.status === 401 && path !== '/auth/login') {
      window.dispatchEvent(new Event('session-expired'));
    }
    throw apiError(data);
  }
  return data;
}

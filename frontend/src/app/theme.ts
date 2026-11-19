import { useEffect, useState } from 'react';

export type ThemeChoice = 'system' | 'light' | 'dark';

const KEY = 'haproxy-control-theme';

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key) as T | null;
    return value && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode) – keep the in-memory choice */
  }
}

/** Light/dark theme: follows the OS by default, can be pinned per browser. */
export function useTheme() {
  const [theme, setTheme] = useState<ThemeChoice>(() =>
    read(KEY, ['system', 'light', 'dark'] as const, 'system'),
  );
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') delete root.dataset.theme;
    else root.dataset.theme = theme;
    write(KEY, theme);
  }, [theme]);
  const next = () => setTheme((t) => (t === 'system' ? 'light' : t === 'light' ? 'dark' : 'system'));
  return { theme, setTheme, next };
}

/** Sidebar collapsed to an icon strip (JetBrains tool window stripe). */
export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(
    () => read('haproxy-control-sidebar', ['open', 'collapsed'] as const, 'open') === 'collapsed',
  );
  useEffect(() => write('haproxy-control-sidebar', collapsed ? 'collapsed' : 'open'), [collapsed]);
  return { collapsed, toggle: () => setCollapsed((c) => !c) };
}

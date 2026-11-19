// Per-viewer conveniences kept in the browser (the open panel, a query history). Storage can be
// missing or refused (a private window); then nothing is remembered and nothing breaks.

export function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Only a convenience.
  }
}

export function recall(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

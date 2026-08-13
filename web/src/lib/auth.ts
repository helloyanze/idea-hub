export const CREDENTIALS_KEY = "ihub_credentials";

export interface Credentials {
  user: string;
  pass: string;
}

let cached: Credentials | null | undefined;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((listener) => listener());
}

export function getCredentials(): Credentials | null {
  if (cached !== undefined) {
    return cached;
  }

  const stored = localStorage.getItem(CREDENTIALS_KEY);
  if (stored === null) {
    cached = null;
    return cached;
  }

  try {
    cached = JSON.parse(stored) as Credentials;
  } catch {
    cached = null;
  }

  return cached;
}

export function setCredentials(user: string, pass: string) {
  cached = { user, pass };
  localStorage.setItem(CREDENTIALS_KEY, JSON.stringify(cached));
  notify();
}

export function clearCredentials() {
  cached = null;
  localStorage.removeItem(CREDENTIALS_KEY);
  notify();
}

export function subscribeCredentials(listener: () => void): () => void {
  listeners.add(listener);

  return () => {
    listeners.delete(listener);
  };
}

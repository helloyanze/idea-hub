import { clearCredentials, getCredentials } from "@/lib/auth";

interface ApiErrorPayload {
  code: string;
  message: string;
}

interface ApiEnvelope<T> {
  data?: T;
  error?: ApiErrorPayload;
}

export class ApiError extends Error {
  public code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

function encodeBasicCredentials(user: string, pass: string): string {
  const bytes = new TextEncoder().encode(`${user}:${pass}`);
  const binary = String.fromCharCode(...bytes);
  return btoa(binary);
}

function mergeHeaders(
  suppliedHeaders: HeadersInit | undefined,
  authorization: string | undefined,
): Record<string, string> {
  const source = new Headers(suppliedHeaders);

  if (!source.has("Content-Type")) {
    source.set("Content-Type", "application/json");
  }
  if (authorization !== undefined) {
    source.set("Authorization", authorization);
  }

  const headers: Record<string, string> = {};
  source.forEach((value, key) => {
    if (key.toLowerCase() === "content-type") {
      headers["Content-Type"] = value;
    } else if (key.toLowerCase() === "authorization") {
      headers.Authorization = value;
    } else {
      headers[key] = value;
    }
  });

  return headers;
}

async function readEnvelope<T>(response: Response): Promise<ApiEnvelope<T>> {
  const text = await response.text();
  if (text.length === 0) {
    return {};
  }

  try {
    return JSON.parse(text) as ApiEnvelope<T>;
  } catch {
    return {};
  }
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const base =
    import.meta.env.VITE_API_BASE ||
    (import.meta.env.DEV ? "http://localhost:8000" : "");
  const credentials = getCredentials();
  const authorization = credentials
    ? `Basic ${encodeBasicCredentials(credentials.user, credentials.pass)}`
    : undefined;
  const headers = mergeHeaders(options?.headers, authorization);
  const response = await fetch(base + path, { ...options, headers });
  const envelope = await readEnvelope<T>(response);

  if (response.status === 401) {
    clearCredentials();
    throw new ApiError(
      "UNAUTHORIZED",
      envelope.error?.message ?? "Unauthorized",
    );
  }

  if (envelope.error) {
    throw new ApiError(envelope.error.code, envelope.error.message);
  }

  return envelope.data as T;
}

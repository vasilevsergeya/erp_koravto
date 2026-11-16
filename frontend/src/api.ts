export type User = {
  id: string;
  email: string;
  role: string;
  csrf_token: string;
  must_change_password: boolean;
  session_expires_at?: string;
};
export type Setting = {
  key: string;
  type: string;
  is_secret: boolean;
  is_set: boolean;
  value: string | number;
};
export type TranslationPreferences = {
  translate_incoming: boolean;
  translate_outgoing: boolean;
  translation_client_language: string;
  translation_manager_language: string;
};
export type Conversation = {
  inbound_seq: number;
  translation?: TranslationPreferences | null;
  id: string;
  customer_id: string | null;
  name: string;
  display_name: string;
  onec_status: string;
  onec_stale: boolean;
  phone: string;
  chat_type: string;
  channel_id: string;
  state: string;
  updated_at: string;
  assignee_id: string | null;
  needs_human: boolean;
  ai_mode: string;
  order_numbers: string[];
  unread_count: number;
  last_message: string;
  last_status: string;
  task_updated: boolean;
  has_ai_draft?: boolean;
};
export type Message = {
  version: number;
  author_id: string | null;
  task_id: string | null;
  task_number: number | null;
  external: boolean;
  sent_at: string | null;
  delivery_text: string | null;
  translation?: {
    state: string;
    text: string | null;
    error: string | null;
    source: string;
    target: string;
  } | null;
  action?: {
    id: string;
    kind: string;
    state: string;
    error: string | null;
  } | null;
  id: string;
  direction: string;
  author_type: string;
  text: string;
  kind: string;
  status: string;
  edited: boolean;
  history?: { body: string; at: string }[];
  content?: { map_url?: string; name?: string; phones?: string[] };
  deleted: boolean;
  created_at: string;
  attachment_url: string | null;
  attachment_name: string | null;
  attachment_mime: string | null;
  author_name: string | null;
  is_private_note: boolean;
  error_code: string | null;
  error_text: string | null;
  quoted_message_id: string | null;
  quoted_text: string;
};
let csrf = "";
let sessionUser = "";
let expired = false;
let revision = 0;
let generation = 0;
let refreshing: Promise<void> | null = null;
export const setCsrf = (value: string) => {
  csrf = value;
};
export function setSession(user: User | null) {
  csrf = user?.csrf_token || "";
  sessionUser = user?.id || "";
  expired = false;
  revision++;
  generation++;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export class SessionExpiredError extends ApiError {
  constructor() { super("Сессия завершена. Войдите снова; черновик сохранён.", 401); }
}

function expireSession(): never {
  if (!expired) {
    expired = true;
    csrf = "";
    window.dispatchEvent(new Event("crm:session-expired"));
  }
  throw new SessionExpiredError();
}

async function readResponse(response: Response) {
  const data = await response.json().catch(() => ({ detail: "Не удалось прочитать ответ сервера" }));
  if (!response.ok) throw new ApiError(
    typeof data.detail === "string" ? data.detail : "Ошибка запроса", response.status,
  );
  return data;
}

export async function refreshSession(): Promise<void> {
  if (expired) throw new SessionExpiredError();
  if (refreshing) return refreshing;
  const expectedUser = sessionUser;
  const startedAt = revision;
  const startedGeneration = generation;
  refreshing = (async () => {
    const perform = () => fetch("/api/auth/refresh", {
      method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrf },
    });
    let response = await perform();
    if (generation !== startedGeneration) throw new SessionExpiredError();
    if (response.status === 403) {
      // A login in another tab can replace the cookie. Read the current CSRF safely.
      const me = await fetch("/api/auth/me", { credentials: "same-origin" });
      if (generation !== startedGeneration) throw new SessionExpiredError();
      if (me.status === 401) return expireSession();
      const user: User = await readResponse(me);
      if (expectedUser && user.id !== expectedUser) return expireSession();
      if (revision !== startedAt) throw new SessionExpiredError();
      csrf = user.csrf_token;
      response = await perform();
    }
    if (generation !== startedGeneration) throw new SessionExpiredError();
    if (response.status === 401) return expireSession();
    const user: User = await readResponse(response);
    if (expectedUser && user.id !== expectedUser) return expireSession();
    if (revision !== startedAt || expired) throw new SessionExpiredError();
    csrf = user.csrf_token;
    revision++;
    window.dispatchEvent(new CustomEvent("crm:session-updated", { detail: user }));
  })().finally(() => { refreshing = null; });
  return refreshing;
}

async function request(path: string, options: () => RequestInit): Promise<Response> {
  const protectedRequest = path.startsWith("/api/") && !path.startsWith("/api/auth/");
  if (protectedRequest && expired) throw new SessionExpiredError();
  const startedAt = revision;
  const startedGeneration = generation;
  let response = await fetch(path, options());
  if (protectedRequest && generation !== startedGeneration) throw new SessionExpiredError();
  if (response.status === 401 && protectedRequest) {
    if (expired) throw new SessionExpiredError();
    // Late responses from the same burst reuse the completed refresh as well.
    if (revision === startedAt) await refreshSession();
    if (expired || generation !== startedGeneration) throw new SessionExpiredError();
    response = await fetch(path, options());
    if (generation !== startedGeneration) throw new SessionExpiredError();
    if (response.status === 401) return expireSession();
  }
  return response;
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await request(path, () => ({
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  }));
  return readResponse(response) as Promise<T>;
}

export async function uploadFile(
  file: File,
  path = "/api/uploads",
): Promise<{ id: string; filename: string; size: number }> {
  const body = new FormData();
  body.append("file", file);
  const response = await request(path, () => ({
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf },
    body,
  }));
  return readResponse(response);
}

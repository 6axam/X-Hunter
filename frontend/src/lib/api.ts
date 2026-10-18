import type { Draft, Finding, Marketplace, Me, ResearchEvent, ResearchItem, ResearchSession, Search } from "../types";

let csrfToken = sessionStorage.getItem("olx-hunter-csrf");

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (csrfToken && !["GET", "HEAD"].includes(init.method ?? "GET")) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.code ?? "request_failed", body.message ?? "Ошибка запроса");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function authenticate(initData: string): Promise<void> {
  if (!initData) return;
  const result = await request<{ csrf_token: string }>("/auth/telegram", {
    method: "POST",
    body: JSON.stringify({ init_data: initData }),
  });
  csrfToken = result.csrf_token;
  sessionStorage.setItem("olx-hunter-csrf", csrfToken);
}

export async function downloadResearchDiagnostics(id: string): Promise<void> {
  const response = await fetch(`/api/v1/research-sessions/${id}/diagnostics.zip`, {
    credentials: "include",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.code ?? "request_failed", body.message ?? "Ошибка экспорта");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `x-hunter-research-${id}-diagnostics.zip`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export const api = {
  me: () => request<Me>("/me"),
  searches: () => request<Search[]>("/searches"),
  importSearch: (url: string) =>
    request<Draft>("/search-imports", { method: "POST", body: JSON.stringify({ url }) }),
  patchDraft: (id: string, body: Partial<Draft>) =>
    request<Draft>(`/search-drafts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  createSearch: (draftId: string) =>
    request<Search>("/searches", {
      method: "POST",
      body: JSON.stringify({ draft_id: draftId, active: true, interval_minutes: 15 }),
    }),
  patchSearch: (search: Search, body: Partial<Search>) =>
    request<Search>(`/searches/${search.id}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: search.revision, ...body }),
    }),
  scan: (id: string) => request<{ status: string; error_code: string | null }>(`/searches/${id}/scans`, { method: "POST" }),
  marketplaces: () => request<{ marketplaces: Marketplace[] }>("/marketplaces"),
  researchSessions: () => request<ResearchSession[]>("/research-sessions"),
  createResearch: (body: { query: string; marketplaces: string[]; max_items: number; budget_usd: string; model?: string }) => request<ResearchSession>("/research-sessions", { method: "POST", body: JSON.stringify(body) }),
  research: (id: string) => request<ResearchSession>(`/research-sessions/${id}`),
  researchItems: (id: string) => request<ResearchItem[]>(`/research-sessions/${id}/items`),
  researchEvents: (id: string) => request<ResearchEvent[]>(`/research-sessions/${id}/events`),
  researchAction: (id: string, action: "pause" | "resume" | "cancel") => request<ResearchSession>(`/research-sessions/${id}/actions`, { method: "POST", body: JSON.stringify({ action }) }),
  findings: (saved = false) => request<Finding[]>(`/findings${saved ? "?saved=true" : ""}`),
  finding: (id: string) => request<Finding>(`/findings/${id}`),
  patchFinding: (id: string, body: Partial<Pick<Finding, "saved" | "hidden" | "seen" | "note">>) =>
    request(`/findings/${id}/state`, { method: "PATCH", body: JSON.stringify(body) }),
};


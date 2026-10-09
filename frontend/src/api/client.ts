import type { JobDetail, JobStatus, JobSummary, Profile, SkillGaps, Stats } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    // FastAPI errors look like {"detail": "..."} (or a list for 422 validation errors).
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  listJobs: (status?: JobStatus) => request<JobSummary[]>(`/jobs${status ? `?status=${status}` : ""}`),
  getJob: (id: string) => request<JobDetail>(`/jobs/${id}`),
  stats: () => request<Stats>("/jobs/stats"),
  approve: (id: string) => post<{ id: string; status: JobStatus }>(`/jobs/${id}/approve`),
  reject: (id: string, reason?: string) => post<{ id: string; status: JobStatus }>(`/jobs/${id}/reject`, { reason }),
  search: (source: string, limit: number) => post<Record<string, number>>("/search", { source, limit }),
  apply: (max_count: number) => post<Record<string, number>>("/apply", { max_count }),
  skillGaps: () => request<SkillGaps>("/skill-gaps"),
  profile: () => request<Profile>("/profile"),
};

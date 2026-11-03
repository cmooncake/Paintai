export interface Painter {
  painter_id: string;
  name: string;
  style: string;
}
export interface Critique {
  scores: Record<string, number>;
  overall: number;
  feedback: string;
  improvements: string[];
}
export interface Creation {
  id: string;
  painter_id: string;
  topic: string;
  created_at: string;
  status: string;
  image_url: string | null;
  critique: Critique | null;
  simulated: boolean;
  simulation: Record<string, boolean> | null;
  can_evaluate: boolean;
  plan: { prompt: string; composition: string; palette: string } | null;
  evaluation_history: {
    status: string;
    finished_at: string | null;
    critique: Critique | null;
  }[];
}
export interface Job {
  id: string;
  painter_id: string;
  kind: string;
  topic: string;
  entry_id: string | null;
  status: string;
  stage: string;
  error: string | null;
  created_at: string;
}
export interface System {
  ready: boolean;
  simulated: boolean;
  roles: Record<string, { ready: boolean; simulated: boolean }>;
}
const origin =
  (import.meta as ImportMeta & { env: Record<string, string> }).env
    .VITE_API_BASE_URL || "";
export const asset = (url: string) => origin.replace(/\/$/, "") + url;
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(asset("/api/v1" + path), options);
  } catch {
    throw new Error("暂时连接不到画室，请确认后台服务已启动。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "请检查填写的内容，再试一次。",
    );
  }
  return response.json();
}
export function post<T>(path: string, body: unknown, key?: string) {
  return request<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(key ? { "Idempotency-Key": key } : {}),
    },
    body: JSON.stringify(body),
  });
}
export const active = (job: Job) => ["queued", "running"].includes(job.status);

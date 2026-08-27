import type {
  APIErrorDetail,
  ChatRequest,
  ChatResponse,
  HealthResponse,
} from "@/types/api";

const configuredBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
export const API_BASE_URL = normalizeApiBaseUrl(configuredBaseUrl);
const MAX_STREAM_BYTES = 256_000;
const MAX_EVENT_BYTES = 128_000;
const MAX_ANSWER_LENGTH = 20_000;
const MAX_SOURCES = 12;
const SOURCE_HOSTS: Record<"DarGlobal" | "Wasalt", ReadonlySet<string>> = {
  DarGlobal: new Set(["darglobal.co.uk", "www.darglobal.co.uk", "cdn.darglobal.co.uk"]),
  Wasalt: new Set(["wasalt.sa", "www.wasalt.sa", "cdn.wasalt.sa"]),
};

const EVENT_STATUS: Record<string, number> = {
  llm_invalid_response: 502,
  llm_rate_limited: 503,
  llm_unavailable: 503,
  llm_timeout: 504,
  request_timeout: 504,
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "request_failed",
    readonly requestId?: string,
    readonly retryAfter?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) throw new ApiError("The API request failed.", response.status);
  return (await response.json()) as T;
}

export function getApiHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health", { signal, cache: "no-store" });
}

interface StreamHandlers {
  onStart: (requestId: string) => void;
}

interface SSEEvent {
  event: string;
  data: string;
}

export async function streamChat(
  request: ChatRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify(request),
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Unable to reach the service.", 0, "network_error");
  }

  if (!response.ok) {
    let detail: APIErrorDetail | undefined;
    try {
      detail = ((await response.json()) as { error?: APIErrorDetail }).error;
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      "The chat request failed.",
      response.status,
      detail?.code,
      detail?.request_id && isValidRequestId(detail.request_id) ? detail.request_id : undefined,
      response.headers.get("Retry-After") ?? undefined,
    );
  }

  if (!response.body) throw new ApiError("The stream was unavailable.", 0, "network_error");
  const contentType = response.headers.get("Content-Type")?.split(";", 1)[0].trim();
  if (contentType !== "text/event-stream") {
    throw new ApiError("The stream response was invalid.", 502, "invalid_stream");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedBytes = 0;
  let completed: ChatResponse | undefined;

  const consume = (block: string) => {
    const parsed = parseSSEBlock(block);
    if (!parsed) return;
    let payload: unknown;
    try {
      payload = JSON.parse(parsed.data);
    } catch {
      throw new ApiError("The stream contained invalid data.", 502, "invalid_stream");
    }

    if (parsed.event === "start") {
      const requestId = readRequestId(payload, "request_id");
      if (requestId) handlers.onStart(requestId);
    } else if (parsed.event === "complete") {
      completed = validateChatResponse(payload);
    } else if (parsed.event === "error") {
      const envelope = isRecord(payload) && isRecord(payload.error) ? payload.error : undefined;
      const code = readString(envelope, "code") ?? "llm_unavailable";
      throw new ApiError(
        "The provider request failed.",
        EVENT_STATUS[code] ?? 503,
        code,
        readRequestId(envelope, "request_id"),
      );
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    receivedBytes += value?.byteLength ?? 0;
    if (receivedBytes > MAX_STREAM_BYTES) {
      await reader.cancel();
      throw new ApiError("The stream response was too large.", 502, "invalid_stream");
    }
    buffer += decoder.decode(value, { stream: !done });
    if (buffer.length > MAX_EVENT_BYTES && !/\r?\n\r?\n/.test(buffer)) {
      await reader.cancel();
      throw new ApiError("The stream event was too large.", 502, "invalid_stream");
    }
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() ?? "";
    parts.forEach(consume);
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  if (!completed) throw new ApiError("The stream ended before completion.", 502, "invalid_stream");
  return completed;
}

export function parseSSEBlock(block: string): SSEEvent | undefined {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? { event, data: data.join("\n") } : undefined;
}

function validateChatResponse(payload: unknown): ChatResponse {
  if (
    !isRecord(payload) ||
    typeof payload.answer !== "string" ||
    payload.answer.length < 1 ||
    payload.answer.length > MAX_ANSWER_LENGTH ||
    typeof payload.refused !== "boolean" ||
    !Array.isArray(payload.sources) ||
    payload.sources.length > MAX_SOURCES ||
    typeof payload.request_id !== "string" ||
    !isValidRequestId(payload.request_id) ||
    !payload.sources.every(isValidSource) ||
    (payload.refused && payload.sources.length > 0)
  ) {
    throw new ApiError("The stream response was invalid.", 502, "invalid_stream");
  }
  return payload as unknown as ChatResponse;
}

function isValidSource(value: unknown): boolean {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !/^S[1-9][0-9]*$/.test(value.id) ||
    typeof value.title !== "string" ||
    value.title.length < 1 ||
    value.title.length > 500 ||
    typeof value.url !== "string" ||
    value.url.length > 2048 ||
    (value.source !== "DarGlobal" && value.source !== "Wasalt")
  ) return false;
  try {
    const parsed = new URL(value.url);
    return parsed.protocol === "https:"
      && !parsed.username
      && !parsed.password
      && SOURCE_HOSTS[value.source].has(parsed.hostname.toLowerCase());
  } catch {
    return false;
  }
}

function isValidRequestId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(value);
}

function normalizeApiBaseUrl(value: string): string {
  const parsed = new URL(value);
  if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL must be an HTTP(S) origin without credentials");
  }
  if (parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL must not include a path, query, or fragment");
  }
  return parsed.origin;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readString(value: unknown, key: string): string | undefined {
  return isRecord(value) && typeof value[key] === "string" ? value[key] : undefined;
}

function readRequestId(value: unknown, key: string): string | undefined {
  const requestId = readString(value, key);
  return requestId && isValidRequestId(requestId) ? requestId : undefined;
}

export interface HealthResponse {
  status: "ok";
}

export type SourceFilter = "darglobal" | "wasalt";

export interface ApiSource {
  id: string;
  title: string;
  url: string;
  source: "DarGlobal" | "Wasalt";
}

export interface ChatRequest {
  message: string;
  source?: SourceFilter;
}

export interface ChatResponse {
  answer: string;
  refused: boolean;
  sources: ApiSource[];
  request_id: string;
}

export interface APIErrorDetail {
  code: string;
  message: string;
  request_id: string;
}

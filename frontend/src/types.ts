export type Search = {
  id: string;
  name: string;
  current_url: string;
  specification: Record<string, unknown>;
  rules: Record<string, unknown>;
  ai_settings: Record<string, unknown>;
  event_settings: { price_drop_enabled?: boolean; price_drop_percent?: number };
  active: boolean;
  revision: number;
  interval_minutes: number;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Draft = {
  id: string;
  page_type: "search" | "listing";
  name: string;
  parsed_spec: Record<string, unknown>;
  rules: Record<string, unknown>;
  ai_settings: Record<string, unknown>;
};

export type Finding = {
  id: string;
  title: string;
  description: string | null;
  price: string | null;
  currency: string | null;
  city: string | null;
  condition: string | null;
  attributes: Record<string, string>;
  published_at: string | null;
  canonical_url: string;
  decision: "match" | "reject" | "uncertain";
  reasons: Array<Record<string, unknown>>;
  saved: boolean;
  hidden: boolean;
  seen: boolean;
  note: string | null;
  cover_media_id: string | null;
  ai_value_label: string | null;
  ai_summary: string | null;
  searches: Array<{ id: string; name: string }>;
  media?: Array<{ id: string; status: string; position: number }>;
};

export type Me = {
  telegram_id: number;
  timezone: string;
  capabilities: {
    telegram_configured: boolean;
    openrouter_configured: boolean;
    source_http_enabled: boolean;
  };
};

export type Marketplace = { id: string; label: string; available: boolean; reason: string | null };

export type ResearchSession = {
  id: string; query: string; marketplaces: string[]; model: string | null; status: string; stage: string;
  max_items: number; cursor: number; processed_count: number; accepted_count: number; rejected_count: number;
  rejected_before_analysis_count: number; collected_count: number;
  search_plan: { product_type?: string; brand?: string | null; model_family?: string | null; max_price?: number | null; currency?: string | null; purpose?: string | null; search_queries?: string[]; hard_requirements?: string[]; soft_requirements?: string[]; clarification_question?: string | null };
  executed_searches: Array<{ query: string; url: string; price_to?: string | null; pages: Array<{ page: number; state: string; url: string; final_url?: string | null; results: number; error_code?: string | null }> }>;
  compact_memory: Record<string, unknown>; budget_usd: string; estimated_cost_usd: string; actual_cost_usd: string;
  planning_estimated_cost_usd: string; planning_actual_cost_usd: string; planning_input_tokens: number; planning_output_tokens: number;
  error_code: string | null; started_at: string | null; finished_at: string | null; created_at: string; updated_at: string;
};

export type ResearchItem = {
  id: string; listing_id: string; sequence: number; status: string; score: number | null; confidence: string | null;
  recommendation: string | null; listing_snapshot: { title?: string; price?: string | null; currency?: string | null; city?: string | null; url?: string };
  analysis: { summary?: string; strengths?: string[]; risks?: string[]; missing_information?: string[]; market_assessment?: string; evidence?: Array<{ quote: string }> };
  input_tokens: number | null; output_tokens: number | null; cached_input_tokens: number | null;
  estimated_cost_usd: string | null; actual_cost_usd: string | null; error_code: string | null; created_at: string;
};

export type ResearchEvent = { id: string; kind: string; message: string; payload: Record<string, unknown>; created_at: string };

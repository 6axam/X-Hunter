export type Search = {
  id: string;
  name: string;
  current_url: string;
  specification: Record<string, unknown>;
  rules: Record<string, unknown>;
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
};

export type Finding = {
  id: string;
  title: string;
  description: string | null;
  price: string | null;
  currency: string | null;
  city: string | null;
  canonical_url: string;
  decision: "match" | "reject" | "uncertain";
  reasons: Array<Record<string, unknown>>;
  saved: boolean;
  hidden: boolean;
  seen: boolean;
  note: string | null;
  cover_media_id: string | null;
  searches: Array<{ id: string; name: string }>;
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


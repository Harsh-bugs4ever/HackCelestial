export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export type Driver = { label: string; detail: string; weight: number };

export type EngineName = "demand" | "maintenance" | "workforce" | "guest";

export type ActionCard = {
  id: number;
  engine: EngineName;
  kind: string;
  title: string;
  detail: string;
  recommendation: string;
  confidence: number;
  impact_inr: number;
  impact_kind: "revenue" | "cost_avoided" | "cost";
  impact_note: string;
  urgency: "critical" | "high" | "normal" | "low";
  why: Driver[];
  cross_domain: string[];
  payload: Record<string, unknown>;
  status: string;
  created_at: string | null;
  decided_at: string | null;
  decided_by: string;
  executed_at: string | null;
  execution_result: Record<string, unknown>;
};

export type Dashboard = {
  resort: string;
  date: string;
  occupancy: {
    rooms_sold: number;
    rooms_available: number;
    pct: number;
    adr: number;
    revpar: number;
    room_revenue: number;
    fnb_revenue: number;
  };
  movements: { arrivals: number; departures: number };
  requests: {
    open: number;
    high_priority: number;
    by_department: Record<string, number>;
  };
  asset_health: {
    critical: number;
    watch: number;
    healthy: number;
    worst: AssetHealth[];
  };
  staffing: { gaps: number; short_by: number; detail: StaffingGap[] };
  sentiment: {
    overall: number;
    by_department: Record<string, DeptSentiment>;
  };
  actions: {
    pending: number;
    critical: number;
    total_impact_inr: number;
    top: ActionCard[];
  };
};

export type AssetHealth = {
  id: string;
  name: string;
  kind: string;
  location: string;
  criticality: number;
  rooms_served: number;
  risk: number;
  days_to_failure: number;
  status: "critical" | "watch" | "healthy";
  vibration_mm_s: number;
  temperature_c: number;
  power_kw: number;
};

export type StaffingGap = {
  date: string;
  slot: string;
  role: string;
  required: number;
  rostered: number;
  short_by: number;
  occupancy: number;
};

export type DeptSentiment = {
  recent: number;
  baseline: number;
  delta: number;
  recent_count: number;
  negative_share: number;
};

export type ForecastPayload = {
  history: { date: string; occupancy: number; adr: number }[];
  forecast: {
    date: string;
    occupancy: number;
    rooms_sold: number;
    confidence: number;
  }[];
  by_category: Record<
    string,
    { date: string; occupancy: number; lower: number; upper: number }[]
  >;
  model: string;
};

export type Simulation = {
  horizon_days: number;
  levers: Record<string, number>;
  baseline: {
    revenue_inr: number;
    staff_cost_inr: number;
    profit_inr: number;
    mean_occupancy: number;
  };
  scenario: {
    revenue_inr: number;
    staff_cost_inr: number;
    profit_inr: number;
    mean_occupancy: number;
  };
  delta: {
    revenue_inr: number;
    profit_inr: number;
    occupancy_points: number;
    guest_satisfaction: number;
  };
  assumptions: Record<string, number | string>;
};

export type LearningSummary = {
  engines: Record<
    string,
    {
      proposed: number;
      approved: number;
      dismissed: number;
      snoozed: number;
      predicted_inr: number;
      realised_inr: number;
      outcomes: number;
      correct: number;
      acceptance_rate: number | null;
      accuracy: number | null;
      realisation_rate: number | null;
    }
  >;
  totals: {
    decisions: number;
    approved: number;
    acceptance_rate: number | null;
    outcomes_scored: number;
    realised_inr: number;
    predicted_inr: number;
  };
  signal: Record<string, number>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} - ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  dashboard: () => request<Dashboard>("/dashboard"),

  actions: (status = "pending", engine?: string) =>
    request<{ count: number; total_impact_inr: number; cards: ActionCard[] }>(
      `/actions?status=${status}${engine ? `&engine=${engine}` : ""}`,
    ),

  decide: (id: number, decision: "approve" | "snooze" | "dismiss") =>
    request<ActionCard>(`/actions/${id}/${decision}`, {
      method: "POST",
      body: JSON.stringify({ by: "manager" }),
    }),

  history: () =>
    request<{ count: number; cards: ActionCard[] }>("/actions-feed/history"),

  runEngines: async (engine?: string) => {
    const result = await request<{ results: { engine: string; ok: boolean; cards?: number; error?: string }[] }>(
      `/engines/run${engine ? `?engine=${encodeURIComponent(engine)}` : ""}`,
      { method: "POST" },
    );
    const failed = result.results.filter((run) => !run.ok);
    if (failed.length) {
      throw new Error(failed.map((run) => `${run.engine}: ${run.error ?? "Run failed"}`).join("; "));
    }
    return result;
  },

  engineStatus: () =>
    request<
      Record<
        string,
        {
          last_run: string | null;
          ok: boolean | null;
          error: string;
          cards_last_run: number;
          pending_cards: number;
        }
      >
    >("/engines/status"),

  forecast: (days = 30) => request<ForecastPayload>(`/forecast?days=${days}`),

  assets: () =>
    request<{
      assets: AssetHealth[];
      work_orders: {
        id: number;
        asset_id: string;
        scheduled_for: string;
        window_reason: string;
        priority: string;
        status: string;
      }[];
    }>("/assets"),

  telemetry: (id: string) =>
    request<{
      asset: { id: string; name: string; kind: string; location: string };
      risk: number;
      method: string;
      days_to_failure: number;
      anomaly_rate: number;
      readings: {
        ts: string;
        vibration_mm_s: number;
        temperature_c: number;
        power_kw: number;
        pressure_bar: number;
      }[];
    }>(`/assets/${id}/telemetry`),

  roster: (days = 7) =>
    request<{
      requirement: {
        date: string;
        slot: string;
        role: string;
        required: number;
        rostered: number;
        occupancy: number;
      }[];
      cross_domain_inputs: string[];
      sentiment_lift: Record<string, number>;
      backlog_pressure: Record<string, number>;
    }>(`/roster?days=${days}`),

  sentiment: () =>
    request<{
      by_department: Record<string, DeptSentiment>;
      staffing_lift_published: Record<string, number>;
      recent_reviews: {
        id: number;
        source: string;
        posted_at: string;
        rating: number;
        department: string;
        sentiment: number;
        topics: string[];
        text: string;
      }[];
    }>("/sentiment"),

  guests: () =>
    request<{
      guests: {
        id: number;
        name: string;
        tier: string;
        home_city: string;
        stay_count: number;
        lifetime_value: number;
        dna_summary: string;
        preferences: Record<string, unknown>;
        dna_vector_preview: number[];
      }[];
    }>("/guests"),

  concierge: (question: string, guest_id?: number) =>
    request<{
      answer: string;
      source: string;
      grounded_in: { title: string; score: number }[];
      guest: { id: number; name: string; dna: string } | null;
    }>("/concierge", {
      method: "POST",
      body: JSON.stringify({ question, guest_id }),
    }),

  simulate: (body: {
    rate_change_pct: number;
    staffing_change_pct: number;
    promotion_discount_pct: number;
    horizon_days: number;
  }) =>
    request<Simulation>("/simulate", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  learning: () => request<LearningSummary>("/learning"),

  observe: () =>
    request<{ outcomes_scored: number; summary: LearningSummary }>(
      "/learning/observe",
      { method: "POST" },
    ),

  inventory: () =>
    request<{
      items: {
        id: string;
        name: string;
        unit: string;
        on_hand: number;
        par_level: number;
        department: string;
        below_par: boolean;
      }[];
    }>("/inventory"),

  decisions: (limit = 50) =>
    request<{
      decisions: {
        id: number;
        action_card_id: number;
        engine: string;
        decision: string;
        predicted_impact_inr: number;
        confidence: number;
        decided_by: string;
        at: string | null;
        realised_impact_inr: number | null;
      }[];
    }>(`/decisions?limit=${limit}`),

  /** Generic request for one-off calls */
  request,
};

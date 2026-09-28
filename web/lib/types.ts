/** The admin API's response shapes (server/src/abtest/models.py), as the dashboard reads them. */

export type MetricKind = "conversion" | "mean";
export type Direction = "increase" | "decrease";
export type Status = "draft" | "running" | "stopped";
export type AnalysisType = "fixed_horizon" | "sequential";
export type Role = "primary" | "secondary" | "guardrail";
export type Verdict =
  | "significant_win"
  | "significant_loss"
  | "not_significant"
  | "srm_untrustworthy"
  | "insufficient_data";

export interface Metric {
  key: string;
  name: string;
  kind: MetricKind;
  event_name: string;
  direction: Direction;
  window_hours: number;
  created_at: string;
}

export interface Flag {
  key: string;
  description: string;
  enabled: boolean;
  rollout_bp: number;
  created_at: string;
  updated_at: string;
}

export interface Variant {
  key: string;
  name: string;
  weight_bp: number;
  is_control: boolean;
  position: number;
}

export interface ExperimentMetric {
  metric_key: string;
  kind: MetricKind;
  role: Role;
  expected_baseline: number | null;
}

export interface Change {
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface LatestResults {
  computed_at: string;
  users: number;
  srm_flagged: boolean;
}

export interface Experiment {
  key: string;
  name: string;
  hypothesis: string;
  status: Status;
  traffic_bp: number;
  analysis_type: AnalysisType;
  alpha: number;
  mde_relative: number | null;
  started_at: string | null;
  stopped_at: string | null;
  stop_reason: string | null;
  created_at: string;
  updated_at: string;
  variants: Variant[];
  metrics: ExperimentMetric[];
  latest_results: LatestResults | null;
}

export interface ExperimentDetail extends Experiment {
  changes: Change[];
}

export interface VariantSummary {
  key: string;
  is_control: boolean;
  weight_bp: number;
  users: number;
  conversions: number | null;
  total: number;
  total_sq: number;
}

export interface Comparison {
  variant_key: string;
  abs_diff: number | null;
  rel_lift: number | null;
  ci_low: number | null;
  ci_high: number | null;
  rel_ci_low: number | null;
  rel_ci_high: number | null;
  p_value: number | null;
  significant: boolean;
  insufficient_data: string | null;
  verdict: Verdict;
}

export interface SnapshotData {
  cutoff: string;
  analysis_type: AnalysisType;
  alpha: number;
  tau: number | null;
  metric_key: string;
  metric_kind: MetricKind;
  direction: Direction;
  window_hours: number;
  users: number;
  conflicted_users: number;
  mismatched_users: number;
  srm: { p_value: number | null; flagged: boolean; insufficient_data: string | null };
  variants: VariantSummary[];
  comparisons: Comparison[];
}

export interface SeriesPoint {
  computed_at: string;
  users: number;
  srm_flagged: boolean;
  comparisons: {
    variant_key: string;
    rel_lift: number | null;
    rel_ci_low: number | null;
    rel_ci_high: number | null;
    p_value: number | null;
  }[];
}

export interface Results {
  experiment_key: string;
  metric_key: string;
  latest: { computed_at: string; data: SnapshotData } | null;
  series: SeriesPoint[];
}

export interface ApiKey {
  id: string;
  kind: "client" | "server";
  key_prefix: string;
  created_at: string;
  revoked_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

"use server";

import { redirect } from "next/navigation";

import { type ActionError, toActionError } from "@/lib/actions";
import { api } from "@/lib/api";
import { requireSignedIn } from "@/lib/auth";

export interface NewExperiment {
  key: string;
  name: string;
  hypothesis: string;
  traffic_bp: number;
  analysis_type: "sequential" | "fixed_horizon";
  alpha: number;
  mde_relative: number | null;
  variants: { key: string; name: string; weight_bp: number; is_control: boolean }[];
  metrics: { metric_key: string; role: string; expected_baseline: number | null }[];
}

/** Creates the draft; the API validates everything again. */
export async function createExperiment(body: NewExperiment): Promise<ActionError> {
  await requireSignedIn();
  try {
    await api.createExperiment(body);
  } catch (error) {
    return toActionError(error);
  }
  redirect(`/experiments/${encodeURIComponent(body.key)}`);
}

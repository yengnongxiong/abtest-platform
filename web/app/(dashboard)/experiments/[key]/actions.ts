"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { type ActionError, toActionError } from "@/lib/actions";
import { api } from "@/lib/api";
import { requireSignedIn } from "@/lib/auth";

async function run(key: string, call: () => Promise<unknown>): Promise<ActionError | null> {
  await requireSignedIn();
  try {
    await call();
  } catch (error) {
    return toActionError(error);
  }
  revalidatePath(`/experiments/${key}`);
  return null;
}

export async function startExperiment(key: string): Promise<ActionError | null> {
  return run(key, () => api.startExperiment(key));
}

export async function stopExperiment(key: string, reason: string): Promise<ActionError | null> {
  return run(key, () => api.stopExperiment(key, reason));
}

export async function recomputeResults(key: string): Promise<ActionError | null> {
  return run(key, () => api.recompute(key));
}

export async function cloneExperiment(key: string, newKey: string): Promise<ActionError> {
  await requireSignedIn();
  try {
    await api.cloneExperiment(key, newKey);
  } catch (error) {
    return toActionError(error);
  }
  redirect(`/experiments/${encodeURIComponent(newKey)}`);
}

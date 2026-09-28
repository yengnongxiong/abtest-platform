"use server";

import { revalidatePath } from "next/cache";

import { type ActionError, toActionError } from "@/lib/actions";
import { api } from "@/lib/api";
import { requireSignedIn } from "@/lib/auth";

export async function createFlag(_state: ActionError | null, form: FormData): Promise<ActionError | null> {
  await requireSignedIn();
  try {
    await api.createFlag({
      key: form.get("key"),
      description: form.get("description") ?? "",
      enabled: false,
      rollout_bp: Math.round(Number(form.get("rollout_percent")) * 100),
    });
  } catch (error) {
    return toActionError(error);
  }
  revalidatePath("/flags");
  return null;
}

export async function updateFlag(key: string, form: FormData): Promise<void> {
  await requireSignedIn();
  const enabled = form.get("enabled");
  const rollout = form.get("rollout_percent");
  await api.updateFlag(key, {
    ...(enabled !== null ? { enabled: enabled === "true" } : {}),
    ...(rollout !== null ? { rollout_bp: Math.round(Number(rollout) * 100) } : {}),
  });
  revalidatePath("/flags");
}

export async function deleteFlag(key: string): Promise<void> {
  await requireSignedIn();
  await api.deleteFlag(key);
  revalidatePath("/flags");
}

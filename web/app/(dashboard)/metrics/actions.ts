"use server";

import { revalidatePath } from "next/cache";

import { type ActionError, toActionError } from "@/lib/actions";
import { api } from "@/lib/api";
import { requireSignedIn } from "@/lib/auth";

export async function createMetric(_state: ActionError | null, form: FormData): Promise<ActionError | null> {
  await requireSignedIn();
  try {
    await api.createMetric({
      key: form.get("key"),
      name: form.get("name"),
      kind: form.get("kind"),
      event_name: form.get("event_name"),
      direction: form.get("direction"),
      window_hours: Number(form.get("window_hours")),
    });
  } catch (error) {
    return toActionError(error);
  }
  revalidatePath("/metrics");
  return null;
}

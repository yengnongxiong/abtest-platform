"use server";

import { revalidatePath } from "next/cache";

import { type ActionError, toActionError } from "@/lib/actions";
import { api } from "@/lib/api";
import { requireSignedIn } from "@/lib/auth";

export type CreateKeyState = { created: { kind: string; key: string } } | { error: ActionError } | null;

export async function createKey(_state: CreateKeyState, form: FormData): Promise<CreateKeyState> {
  await requireSignedIn();
  const kind = form.get("kind") === "server" ? "server" : "client";
  try {
    const created = await api.createApiKey(kind);
    revalidatePath("/settings");
    return { created: { kind: created.kind, key: created.key } };
  } catch (error) {
    return { error: toActionError(error) };
  }
}

export async function revokeKey(id: string): Promise<void> {
  await requireSignedIn();
  await api.revokeApiKey(id);
  revalidatePath("/settings");
}

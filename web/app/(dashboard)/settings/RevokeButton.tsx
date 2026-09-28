"use client";

import { useActionState } from "react";

import { FormError } from "@/components/FormError";

import { revokeKey } from "./actions";

/** Revokes one key, and says why when the API refuses (the project's last server key). */
export function RevokeButton({ id }: { id: string }) {
  const [error, action, pending] = useActionState(revokeKey.bind(null, id), null);
  return (
    <form action={action} className="space-y-2">
      <button type="submit" disabled={pending} className="text-accent hover:underline disabled:opacity-60">
        Revoke
      </button>
      <FormError error={error} />
    </form>
  );
}

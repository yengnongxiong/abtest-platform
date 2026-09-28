import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { revokeKey } from "./actions";
import { CreateKeyForm } from "./CreateKeyForm";

export default async function SettingsPage() {
  const keys = await api.listApiKeys();
  return (
    <>
      <h1 className="text-2xl font-semibold">API keys</h1>
      <p className="mt-1 text-muted">
        Only the start of each key is stored where it can be read; the rest is kept as a one-way hash.
      </p>
      <table className="mt-6 w-full text-left text-sm">
        <thead className="border-b border-rule text-muted">
          <tr>
            <th className="py-2 pr-4 font-medium">Key</th>
            <th className="py-2 pr-4 font-medium">Kind</th>
            <th className="py-2 pr-4 font-medium">Created</th>
            <th className="py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key.id} className="border-b border-rule">
              <td className="tabular py-2 pr-4">{key.key_prefix}…</td>
              <td className="py-2 pr-4">{key.kind === "client" ? "Client" : "Server"}</td>
              <td className="py-2 pr-4">{formatDate(key.created_at)}</td>
              <td className="py-2">
                {key.revoked_at ? (
                  <span className="text-muted">Revoked {formatDate(key.revoked_at)}</span>
                ) : (
                  <form action={revokeKey.bind(null, key.id)}>
                    <button type="submit" className="text-accent hover:underline">
                      Revoke
                    </button>
                  </form>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 className="mt-10 mb-4 text-lg font-semibold">New key</h2>
      <CreateKeyForm />
    </>
  );
}

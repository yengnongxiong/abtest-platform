import { logout } from "@/app/login/actions";
import { Nav } from "@/components/Nav";

export default function DashboardLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-rule bg-surface px-4 py-3 md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:border-r md:border-b-0 md:py-6">
        <div className="flex items-center justify-between gap-4 md:block">
          <p className="font-serif text-xl md:mb-6 md:px-3">abtest</p>
          <form action={logout} className="md:hidden">
            <button type="submit" className="text-sm text-muted hover:text-ink">
              Sign out
            </button>
          </form>
        </div>
        <div className="mt-2 overflow-x-auto md:mt-0">
          <Nav />
        </div>
        <form action={logout} className="mt-8 hidden px-3 md:block">
          <button type="submit" className="text-sm text-muted hover:text-ink">
            Sign out
          </button>
        </form>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-10">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
}

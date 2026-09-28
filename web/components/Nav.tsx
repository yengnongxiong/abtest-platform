"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/experiments", label: "Experiments" },
  { href: "/metrics", label: "Metrics" },
  { href: "/flags", label: "Flags" },
  { href: "/settings", label: "API keys" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex gap-1 md:flex-col">
      {LINKS.map(({ href, label }) => {
        const current = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={current ? "page" : undefined}
            className={`rounded-md px-3 py-2 text-sm ${
              current ? "bg-accent-soft font-medium text-accent" : "text-muted hover:text-ink"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

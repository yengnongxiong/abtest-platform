# web

The dashboard, in Next.js (App Router) with Tailwind and Recharts, at http://localhost:3000 under `make dev`. A PM creates metrics, flags, and experiments here and reads the results. Pages render on the server, which calls the admin API with a server key the browser never sees ([ADR-019](../docs/decisions.md#adr-019-dashboard-auth-one-password-a-signed-cookie-and-the-server-key-kept-on-the-server-m8)).

Read first:
- [app/(dashboard)/experiments/[key]/page.tsx](app/(dashboard)/experiments/[key]/page.tsx): the results page, with the verdict, the table, the chart, and the sample ratio mismatch banner
- [app/(dashboard)/experiments/new/NewExperimentForm.tsx](app/(dashboard)/experiments/new/NewExperimentForm.tsx): the pre-registration form and its live sample-size estimate
- [lib/format.ts](lib/format.ts): every sentence the dashboard shows, built from templates and tested

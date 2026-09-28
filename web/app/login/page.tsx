import { LoginForm } from "./LoginForm";

export default function LoginPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-4">
      <h1 className="font-serif text-3xl">abtest</h1>
      <p className="mt-2 mb-8 text-muted">Sign in to manage experiments and read their results.</p>
      <LoginForm />
    </main>
  );
}

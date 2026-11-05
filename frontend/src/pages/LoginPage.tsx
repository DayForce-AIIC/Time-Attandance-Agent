import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";

import { BRAND } from "../brand";
import { BrandMark } from "../components/BrandMark";
export function LoginPage() {
  const { signIn, configured } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email.trim(), password);
    } catch {
      setError(`Sign-in failed. Check your credentials or contact your ${BRAND.name} administrator.`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="login-title">
        <BrandMark className="auth-brand-mark" />
        <p className="eyebrow">HR workspace</p>
        <h1 id="login-title">Sign in to {BRAND.name}</h1>
        <p className="auth-description">Use the account provided by your {BRAND.name} administrator.</p>
        {!configured ? (
          <div className="auth-error" role="alert">
            Supabase Auth is selected, but the frontend project URL or publishable key is missing.
            Configure the `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` environment variables.
          </div>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <label htmlFor="auth-email">Work email</label>
            <input
              id="auth-email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
            <label htmlFor="auth-password">Password</label>
            <input
              id="auth-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            {error ? <p className="auth-error" role="alert">{error}</p> : null}
            <button className="button button-primary auth-submit" type="submit" disabled={submitting}>
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}
        <p className="auth-footnote">Accounts are provisioned by an administrator. Public sign-up is disabled.</p>
      </section>
    </main>
  );
}

import { useState } from "react";

import { api } from "../lib/api";

export default function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { password });
      onAuthenticated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centred">
      <form className="card" onSubmit={submit}>
        <h3>HR sign in</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Shared password, set as HR_PASSWORD in the backend environment.
        </p>
        {error && <div className="notice error">{error}</div>}
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          autoFocus
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={busy || !password}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}

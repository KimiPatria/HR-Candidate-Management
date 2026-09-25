import {
  Button,
  Callout,
  Card,
  FormGroup,
  H4,
  InputGroup,
  Intent,
} from "@blueprintjs/core";
import { useState } from "react";

import Explain from "../components/Explain";
import { api } from "../lib/api";

export default function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
      <Card elevation={2}>
        <form onSubmit={submit}>
          <H4>HR sign in</H4>
          {error && (
            <Callout intent={Intent.DANGER} icon="error" style={{ marginBottom: 16 }}>
              {error}
            </Callout>
          )}
          <FormGroup
            label={
              <Explain text="Shared password, set as HR_PASSWORD in the backend environment.">
                Password
              </Explain>
            }
            labelFor="password"
          >
            <InputGroup
              id="password"
              type={showPassword ? "text" : "password"}
              value={password}
              autoFocus
              size="large"
              leftIcon="lock"
              onValueChange={setPassword}
              rightElement={
                <Button
                  variant="minimal"
                  icon={showPassword ? "eye-off" : "eye-open"}
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                />
              }
            />
          </FormGroup>
          <Button
            type="submit"
            intent={Intent.PRIMARY}
            fill
            size="large"
            loading={busy}
            disabled={!password}
            text="Sign in"
          />
        </form>
      </Card>
    </div>
  );
}

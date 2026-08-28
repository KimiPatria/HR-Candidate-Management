import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { api, ApiError } from "./lib/api";
import CandidatesPage from "./pages/CandidatesPage";
import InterviewPage from "./pages/InterviewPage";
import LoginPage from "./pages/LoginPage";
import SetupPage from "./pages/SetupPage";

type AuthState = "checking" | "in" | "out";

export default function App() {
  const [auth, setAuth] = useState<AuthState>("checking");
  const location = useLocation();

  // The candidate route is public - do not bounce it to the HR login.
  const isCandidateRoute = location.pathname.startsWith("/join/");

  useEffect(() => {
    if (isCandidateRoute) return;
    let cancelled = false;
    api
      .get("/auth/me")
      .then(() => !cancelled && setAuth("in"))
      .catch((err) => {
        if (cancelled) return;
        setAuth(err instanceof ApiError && err.status === 401 ? "out" : "out");
      });
    return () => {
      cancelled = true;
    };
  }, [isCandidateRoute, location.pathname]);

  if (isCandidateRoute) {
    return (
      <Routes>
        <Route path="/join/:token" element={<InterviewPage />} />
      </Routes>
    );
  }

  if (auth === "checking") {
    return <div className="centred muted">Loading...</div>;
  }

  if (auth === "out") {
    return <LoginPage onAuthenticated={() => setAuth("in")} />;
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">AI Candidate Interviewer</span>
        <nav>
          <NavLink to="/setup" className={({ isActive }) => (isActive ? "active" : "")}>
            Interview setup
          </NavLink>
          <NavLink to="/candidates" className={({ isActive }) => (isActive ? "active" : "")}>
            Candidates
          </NavLink>
        </nav>
        <button
          className="secondary"
          onClick={async () => {
            await api.post("/auth/logout");
            setAuth("out");
          }}
        >
          Sign out
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/setup" replace />} />
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/candidates" element={<CandidatesPage />} />
          <Route path="*" element={<Navigate to="/setup" replace />} />
        </Routes>
      </main>
    </div>
  );
}

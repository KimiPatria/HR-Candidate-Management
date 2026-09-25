import {
  Alignment,
  Button,
  Navbar,
  NavbarDivider,
  NavbarGroup,
  NavbarHeading,
  NonIdealState,
  Spinner,
} from "@blueprintjs/core";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { api, ApiError } from "./lib/api";
import CandidateDetailPage from "./pages/CandidateDetailPage";
import CandidatesPage from "./pages/CandidatesPage";
import InterviewPage from "./pages/InterviewPage";
import InterviewsPage from "./pages/InterviewsPage";
import InterviewWorkspace from "./pages/InterviewWorkspace";
import KnowledgePage from "./pages/KnowledgePage";
import LoginPage from "./pages/LoginPage";

type AuthState = "checking" | "in" | "out";

// Three nouns, in the order the work happens. "Company knowledge" sits between them
// rather than inside an interview because it belongs to the company, not to any one
// position - see pages/KnowledgePage.tsx.
const NAV_ITEMS = [
  { to: "/interviews", label: "Interviews", icon: "briefcase" },
  { to: "/knowledge", label: "Company knowledge", icon: "book" },
  { to: "/candidates", label: "Candidates", icon: "people" },
] as const;

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
    return (
      <div className="centred">
        <NonIdealState icon={<Spinner />} title="Loading" />
      </div>
    );
  }

  if (auth === "out") {
    return <LoginPage onAuthenticated={() => setAuth("in")} />;
  }

  return (
    <div className="shell">
      <AppNavbar
        onSignOut={async () => {
          await api.post("/auth/logout");
          setAuth("out");
        }}
      />
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/interviews" replace />} />
          <Route path="/interviews" element={<InterviewsPage />} />
          <Route path="/interviews/:interviewId" element={<InterviewWorkspace />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/candidates" element={<CandidatesPage />} />
          <Route path="/candidates/:sessionId" element={<CandidateDetailPage />} />
          {/* /setup was the old single HR page; keep the link working. */}
          <Route path="/setup" element={<Navigate to="/interviews" replace />} />
          <Route path="*" element={<Navigate to="/interviews" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function AppNavbar({ onSignOut }: { onSignOut: () => Promise<void> }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  return (
    <Navbar>
      <NavbarGroup align={Alignment.START}>
        <NavbarHeading>AI Candidate Interviewer</NavbarHeading>
        <NavbarDivider />
        {NAV_ITEMS.map((item) => (
          <Button
            key={item.to}
            variant="minimal"
            icon={item.icon}
            text={item.label}
            active={pathname.startsWith(item.to)}
            onClick={() => navigate(item.to)}
          />
        ))}
      </NavbarGroup>
      <NavbarGroup align={Alignment.END}>
        <Button variant="minimal" icon="log-out" text="Sign out" onClick={onSignOut} />
      </NavbarGroup>
    </Navbar>
  );
}

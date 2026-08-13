import { useEffect, useSyncExternalStore } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Routes,
  Route,
} from "react-router-dom";

import { apiFetch } from "@/api/client";
import { LoginForm } from "@/components/LoginForm";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  getCredentials,
  subscribeCredentials,
} from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";
import { SourcesPage } from "@/pages/SourcesPage";
import { UnderConstruction } from "@/pages/UnderConstruction";

const queryClient = new QueryClient();

const navigation = [
  { label: "看板", to: "/kanban" },
  { label: "热点", to: "/hotspots" },
  { label: "来源", to: "/sources" },
  { label: "通知", to: "/notifications" },
  { label: "统计", to: "/stats" },
];

function AuthGate() {
  const credentials = useSyncExternalStore(
    subscribeCredentials,
    getCredentials,
  );

  if (credentials === null) {
    return <LoginForm />;
  }

  return <Shell />;
}

function Shell() {
  useEffect(() => {
    void apiFetch("/api/v1/health").catch(() => {});
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
        <h1 className="text-lg font-semibold">Idea Hub</h1>
        <ThemeToggle />
      </header>
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="主导航"
          className="flex w-48 shrink-0 flex-col gap-1 border-r p-3"
        >
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                [
                  "rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                ].join(" ")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1">
          <Routes>
            <Route path="/" element={<Navigate replace to="/kanban" />} />
            <Route
              path="/kanban"
              element={<UnderConstruction description="看板页面将在后续任务中实现" />}
            />
            <Route
              path="/hotspots"
              element={<UnderConstruction description="热点页面将在后续任务中实现" />}
            />
            <Route
              path="/sources"
              element={<SourcesPage />}
            />
            <Route
              path="/notifications"
              element={<UnderConstruction description="通知页面将在后续任务中实现" />}
            />
            <Route
              path="/stats"
              element={<UnderConstruction description="统计页面将在后续任务中实现" />}
            />
            <Route
              path="/tasks/:id"
              element={<UnderConstruction description="任务详情将在后续任务中实现" />}
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthGate />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export default App;

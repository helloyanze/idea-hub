import { useEffect, useState, useSyncExternalStore } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Routes,
  Route,
  useNavigate,
} from "react-router-dom";

import { apiFetch } from "@/api/client";
import { LoginForm } from "@/components/LoginForm";
import { SearchBox } from "@/components/SearchBox";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  getCredentials,
  subscribeCredentials,
} from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";
import { HotspotsPage } from "@/pages/HotspotsPage";
import { KanbanPage } from "@/pages/KanbanPage";
import { NotificationsPage } from "@/pages/NotificationsPage";
import { SourcesPage } from "@/pages/SourcesPage";
import { StatsPage } from "@/pages/StatsPage";
import { TaskDetailPage } from "@/pages/TaskDetailPage";

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
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const unreadCountQuery = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: () =>
      apiFetch<{ count: number }>("/api/v1/notifications/unread-count"),
    refetchInterval: 30000,
    retry: false,
  });

  useEffect(() => {
    void apiFetch("/api/v1/health").catch(() => {});
  }, []);

  const unreadCount = unreadCountQuery.data?.count;

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b px-4">
        <h1 className="text-lg font-semibold">Idea Hub</h1>
        <div className="flex min-w-0 items-center gap-2">
          <div className="w-48 sm:w-64">
            <SearchBox
              label="全局搜索"
              placeholder="搜索任务..."
              value={search}
              onChange={setSearch}
              onSearch={(value) =>
                navigate(
                  value.trim()
                    ? `/kanban?q=${encodeURIComponent(value.trim())}`
                    : "/kanban",
                )
              }
            />
          </div>
          <ThemeToggle />
        </div>
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
              <>
                {item.label}
                {item.to === "/notifications" &&
                typeof unreadCount === "number" &&
                unreadCount > 0 ? (
                  <span
                    data-testid="nav-unread-badge"
                    className="ml-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-red-100 px-1.5 text-xs font-semibold text-red-800"
                  >
                    {unreadCount}
                  </span>
                ) : null}
              </>
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1">
          <Routes>
            <Route path="/" element={<Navigate replace to="/kanban" />} />
            <Route path="/kanban" element={<KanbanPage />} />
            <Route path="/hotspots" element={<HotspotsPage />} />
            <Route path="/sources" element={<SourcesPage />} />
            <Route path="/notifications" element={<NotificationsPage />} />
            <Route path="/stats" element={<StatsPage />} />
            <Route path="/tasks/:id" element={<TaskDetailPage />} />
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

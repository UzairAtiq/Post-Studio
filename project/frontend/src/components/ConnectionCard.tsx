import React, { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchHealth,
  fetchLinkedInStatus,
  fetchSlackStatus,
  connectSlack,
} from "@/api";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Link2,
  Eye,
  EyeOff,
  MessageSquare,
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Loader2,
} from "lucide-react";

export const ConnectionCard: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"linkedin" | "slack">("linkedin");
  const [showPersonUrn, setShowPersonUrn] = useState(false);
  const [slackEmailInput, setSlackEmailInput] = useState("");
  const [slackMessage, setSlackMessage] = useState<{ text: string; isError: boolean } | null>(null);

  const queryClient = useQueryClient();

  const { data: health, isLoading: isHealthLoading } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 30000,
  });

  const { data: linkedInStatus, isLoading: isLinkedInLoading } = useQuery({
    queryKey: ["linkedin-status"],
    queryFn: fetchLinkedInStatus,
    refetchInterval: 30000,
  });

  const { data: slackStatus, isLoading: isSlackLoading } = useQuery({
    queryKey: ["slack-status"],
    queryFn: fetchSlackStatus,
    refetchInterval: 30000,
  });

  // Prefill slack email input if status has email and input is not yet set
  useEffect(() => {
    if (slackStatus?.email && !slackEmailInput) {
      setSlackEmailInput(slackStatus.email);
    }
  }, [slackStatus?.email, slackEmailInput]);

  const connectMutation = useMutation({
    mutationFn: (email: string) => connectSlack(email),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["slack-status"] });
      if (data.connected) {
        setSlackMessage({ text: data.message, isError: false });
      } else {
        setSlackMessage({ text: data.message, isError: true });
      }
    },
    onError: (err: Error) => {
      setSlackMessage({ text: err.message || "Failed to link Slack account.", isError: true });
    },
  });

  const isLinkedInConnected = linkedInStatus?.authenticated ?? false;
  const isSlackConnected = slackStatus?.connected ?? false;

  const formattedExpiry = React.useMemo(() => {
    if (
      !linkedInStatus?.expires_at ||
      linkedInStatus.expires_at === "None" ||
      linkedInStatus.expires_at === "null"
    ) {
      return "—";
    }
    try {
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(linkedInStatus.expires_at));
    } catch {
      return linkedInStatus.expires_at;
    }
  }, [linkedInStatus?.expires_at]);

  const databaseType = health?.database === "postgresql" ? "PostgreSQL" : "SQLite";
  const redirectUri =
    linkedInStatus?.redirect_uri ||
    (typeof window !== "undefined"
      ? `${window.location.origin}/linkedin/callback`
      : "/linkedin/callback");

  const handleSlackSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSlackMessage(null);
    connectMutation.mutate(slackEmailInput.trim());
  };

  return (
    <div className="bg-darkcard text-white rounded-3xl p-6 shadow-soft flex flex-col justify-between space-y-4 h-full">
      {/* Top Bar with Navigation Tabs */}
      <div className="flex items-center justify-between border-b border-neutral-800/80 pb-3">
        <div className="flex items-center space-x-1.5 bg-neutral-900 p-1 rounded-2xl border border-neutral-800/80">
          <button
            type="button"
            onClick={() => setActiveTab("linkedin")}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all ${
              activeTab === "linkedin"
                ? "bg-neutral-800 text-white shadow-sm"
                : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isLinkedInConnected ? "bg-accent" : "bg-neutral-600"
              }`}
            />
            <Link2 className="w-3.5 h-3.5" />
            <span>LinkedIn</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("slack")}
            className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all ${
              activeTab === "slack"
                ? "bg-neutral-800 text-white shadow-sm"
                : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isSlackConnected ? "bg-accent" : "bg-neutral-600"
              }`}
            />
            <MessageSquare className="w-3.5 h-3.5" />
            <span>Slack</span>
          </button>
        </div>

        {/* Global Connection Badge */}
        {activeTab === "linkedin" ? (
          isLinkedInLoading ? (
            <Skeleton className="h-6 w-20 bg-neutral-800" />
          ) : (
            <span
              className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                isLinkedInConnected
                  ? "bg-accent text-neutral-950 font-bold"
                  : "bg-neutral-800 text-neutral-300"
              }`}
            >
              {isLinkedInConnected ? "Active" : "Disconnected"}
            </span>
          )
        ) : isSlackLoading ? (
          <Skeleton className="h-6 w-20 bg-neutral-800" />
        ) : (
          <span
            className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold ${
              isSlackConnected
                ? "bg-accent text-neutral-950 font-bold"
                : "bg-neutral-800 text-neutral-300"
            }`}
          >
            {isSlackConnected ? "Active" : "Disconnected"}
          </span>
        )}
      </div>

      {/* Main Tab Content */}
      {activeTab === "linkedin" ? (
        <>
          <div>
            <h2 className="font-bold text-base tracking-tight text-white leading-tight">
              LinkedIn Authorization
            </h2>
            <p className="text-xs text-neutral-400 font-normal mt-0.5">
              OAuth 2.0 authorization & publish credentials
            </p>
          </div>

          <div className="space-y-2.5 bg-neutral-900/80 rounded-2xl p-4 border border-neutral-800/80 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-neutral-400">Person URN</span>
              {isLinkedInLoading ? (
                <Skeleton className="h-4 w-32 bg-neutral-800" />
              ) : linkedInStatus?.person_urn && linkedInStatus.person_urn !== "—" ? (
                <div className="flex items-center space-x-1.5 max-w-[200px]">
                  <span
                    className="font-mono text-neutral-200 truncate"
                    title={showPersonUrn ? linkedInStatus.person_urn : "Click eye icon to reveal"}
                  >
                    {showPersonUrn ? linkedInStatus.person_urn : "••••••••••••••••"}
                  </span>
                  <button
                    type="button"
                    onClick={() => setShowPersonUrn((prev) => !prev)}
                    className="text-neutral-400 hover:text-neutral-200 transition-colors p-0.5 rounded focus:outline-none"
                    title={showPersonUrn ? "Hide Person URN" : "Show Person URN"}
                    aria-label={showPersonUrn ? "Hide Person URN" : "Show Person URN"}
                  >
                    {showPersonUrn ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>
                </div>
              ) : (
                <span className="font-mono text-neutral-400">—</span>
              )}
            </div>

            <div className="flex items-center justify-between">
              <span className="text-neutral-400">Token Expiry</span>
              {isLinkedInLoading ? (
                <Skeleton className="h-4 w-24 bg-neutral-800" />
              ) : (
                <span className="text-neutral-200">{formattedExpiry}</span>
              )}
            </div>

            <div className="flex items-center justify-between">
              <span className="text-neutral-400">Database</span>
              {isHealthLoading ? (
                <Skeleton className="h-4 w-16 bg-neutral-800" />
              ) : (
                <span className="text-neutral-200">{databaseType}</span>
              )}
            </div>

            <div className="flex items-center justify-between">
              <span className="text-neutral-400">Redirect URI</span>
              {isLinkedInLoading ? (
                <Skeleton className="h-4 w-36 bg-neutral-800" />
              ) : (
                <span className="font-mono text-neutral-300 text-[11px] truncate max-w-[180px]" title={redirectUri}>
                  {redirectUri}
                </span>
              )}
            </div>
          </div>

          <div className="pt-1">
            <a
              href="/linkedin/login"
              className="inline-flex w-full items-center justify-center space-x-2 rounded-full bg-accent px-5 py-2.5 text-xs font-semibold text-neutral-950 shadow-sm transition-all hover:bg-accent-hover active:scale-[0.98]"
            >
              <Link2 className="w-3.5 h-3.5" />
              <span>Connect LinkedIn</span>
            </a>
          </div>
        </>
      ) : (
        <>
          <div>
            <h2 className="font-bold text-base tracking-tight text-white leading-tight">
              Slack Approval Connection
            </h2>
            <p className="text-xs text-neutral-400 font-normal mt-0.5">
              Interactive 1-click approval routing via Slack Direct Messages
            </p>
          </div>

          <div className="space-y-3 bg-neutral-900/80 rounded-2xl p-4 border border-neutral-800/80 text-xs flex-1 flex flex-col justify-between">
            {isSlackConnected ? (
              <div className="space-y-2.5">
                <div className="flex items-center space-x-2 text-accent text-xs font-medium">
                  <CheckCircle2 className="w-4 h-4 shrink-0" />
                  <span>Slack DM approvals are active and ready.</span>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-neutral-400">Slack Member ID</span>
                  <span className="font-mono text-neutral-200">{slackStatus?.slack_user_id}</span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-neutral-400">Linked Email</span>
                  <span className="text-neutral-200 truncate max-w-[180px]">
                    {slackStatus?.email || "—"}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-neutral-400">Delivery Mode</span>
                  <span className="text-neutral-200">Direct Message (DM)</span>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSlackSubmit} className="space-y-2.5">
                <div>
                  <label htmlFor="slack-email" className="block text-[11px] font-medium text-neutral-300 mb-1">
                    Enter your Slack account email:
                  </label>
                  <input
                    id="slack-email"
                    type="email"
                    required
                    placeholder="name@company.com"
                    value={slackEmailInput}
                    onChange={(e) => setSlackEmailInput(e.target.value)}
                    className="w-full rounded-xl bg-neutral-800/90 border border-neutral-700/80 px-3 py-1.5 text-xs text-white placeholder-neutral-500 focus:outline-none focus:ring-1 focus:ring-accent focus:border-accent"
                  />
                </div>

                {slackMessage && (
                  <div
                    className={`flex items-start space-x-1.5 p-2 rounded-xl text-[11px] ${
                      slackMessage.isError
                        ? "bg-rose-500/10 text-rose-300 border border-rose-500/20"
                        : "bg-accent/10 text-accent border border-accent/20"
                    }`}
                  >
                    {slackMessage.isError ? (
                      <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    ) : (
                      <CheckCircle2 className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    )}
                    <span className="leading-tight">{slackMessage.text}</span>
                  </div>
                )}
              </form>
            )}

            {/* Bottom Actions for Slack */}
            <div className="pt-2 flex flex-col space-y-2">
              {!isSlackConnected ? (
                <div className="flex items-center space-x-2">
                  <button
                    type="button"
                    onClick={handleSlackSubmit}
                    disabled={connectMutation.isPending || !slackEmailInput.trim()}
                    className="flex-1 inline-flex items-center justify-center space-x-1.5 rounded-full bg-accent px-4 py-2 text-xs font-semibold text-neutral-950 shadow-sm transition-all hover:bg-accent-hover disabled:opacity-50 active:scale-[0.98]"
                  >
                    {connectMutation.isPending ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        <span>Connecting...</span>
                      </>
                    ) : (
                      <>
                        <MessageSquare className="w-3.5 h-3.5" />
                        <span>Connect Slack</span>
                      </>
                    )}
                  </button>

                  {slackStatus?.invite_url && (
                    <a
                      href={slackStatus.invite_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center justify-center space-x-1 rounded-full bg-neutral-800 hover:bg-neutral-700 px-3 py-2 text-xs font-medium text-neutral-300 transition-colors"
                      title="Join Slack Workspace"
                    >
                      <span>Join</span>
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  )}
                </div>
              ) : (
                <div className="flex items-center space-x-2">
                  <button
                    type="button"
                    onClick={() => {
                      setSlackMessage(null);
                      connectMutation.mutate(slackEmailInput.trim());
                    }}
                    disabled={connectMutation.isPending}
                    className="flex-1 inline-flex items-center justify-center space-x-1.5 rounded-full bg-neutral-800 hover:bg-neutral-700 px-4 py-2 text-xs font-semibold text-neutral-200 transition-colors"
                  >
                    {connectMutation.isPending ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <MessageSquare className="w-3.5 h-3.5" />
                    )}
                    <span>Re-link / Refresh</span>
                  </button>

                  {slackStatus?.invite_url && (
                    <a
                      href={slackStatus.invite_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center justify-center space-x-1 rounded-full bg-neutral-800 hover:bg-neutral-700 px-3 py-2 text-xs font-medium text-neutral-300 transition-colors"
                      title="Open Slack Workspace"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

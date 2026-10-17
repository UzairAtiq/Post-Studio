import React, { useState, useRef, useEffect } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { generatePost, type GeneratePostResponse, type LLMConfig } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sparkles,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
  Settings2,
  Key,
  RotateCcw,
  ArrowLeft,
  Eye,
  EyeOff,
} from "lucide-react";

export const GenerateCard: React.FC = () => {
  const [topic, setTopic] = useState("");
  const [copied, setCopied] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);

  // BYOK LLM State from localStorage
  const [apiKey, setApiKey] = useState(() => localStorage.getItem("custom_llm_api_key") || "");
  const [baseUrl, setBaseUrl] = useState(() => localStorage.getItem("custom_llm_base_url") || "");
  const [model, setModel] = useState(() => localStorage.getItem("custom_llm_model") || "");

  const postResultRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (apiKey.trim()) {
      localStorage.setItem("custom_llm_api_key", apiKey.trim());
    } else {
      localStorage.removeItem("custom_llm_api_key");
    }
  }, [apiKey]);

  useEffect(() => {
    if (baseUrl.trim()) {
      localStorage.setItem("custom_llm_base_url", baseUrl.trim());
    } else {
      localStorage.removeItem("custom_llm_base_url");
    }
  }, [baseUrl]);

  useEffect(() => {
    if (model.trim()) {
      localStorage.setItem("custom_llm_model", model.trim());
    } else {
      localStorage.removeItem("custom_llm_model");
    }
  }, [model]);

  const hasCustomLLM = Boolean(apiKey.trim() || baseUrl.trim() || model.trim());

  const handleResetLLM = () => {
    setApiKey("");
    setBaseUrl("");
    setModel("");
    localStorage.removeItem("custom_llm_api_key");
    localStorage.removeItem("custom_llm_base_url");
    localStorage.removeItem("custom_llm_model");
  };

  const generateMutation = useMutation({
    mutationFn: (topicText: string) => {
      const llmConfig: LLMConfig | undefined = hasCustomLLM
        ? {
            apiKey: apiKey.trim() || undefined,
            baseUrl: baseUrl.trim() || undefined,
            model: model.trim() || undefined,
          }
        : undefined;
      return generatePost(topicText, llmConfig);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["posts"] });
    },
  });

  const generatedPost: GeneratePostResponse | undefined = generateMutation.data;

  useGSAP(
    () => {
      if (generateMutation.isSuccess && generatedPost && postResultRef.current) {
        gsap.fromTo(
          postResultRef.current,
          {
            opacity: 0,
            y: 10,
            scale: 0.98,
          },
          {
            opacity: 1,
            y: 0,
            scale: 1,
            duration: 0.45,
            ease: "power2.out",
          }
        );
      }
    },
    { dependencies: [generateMutation.isSuccess, generatedPost?.post_id] }
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const prompt = topic.trim() || "AI advancements and modern software engineering";
    generateMutation.mutate(prompt);
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-white dark:bg-darkcard dark:border dark:border-neutral-800/80 rounded-3xl p-6 shadow-soft flex flex-col justify-between h-full overflow-hidden transition-colors text-neutral-900 dark:text-white">
      {isSettingsOpen ? (
        <div className="flex flex-col h-full justify-between space-y-3">
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => setIsSettingsOpen(false)}
                  className="p-1 rounded-full hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors"
                >
                  <ArrowLeft className="w-4 h-4 text-neutral-600 dark:text-neutral-300" />
                </button>
                <div className="w-6 h-6 rounded-full bg-purpleAccent/10 text-purpleAccent flex items-center justify-center">
                  <Key className="w-3.5 h-3.5" />
                </div>
                <h3 className="font-bold text-sm text-neutral-900 dark:text-white">
                  Custom LLM (BYOK)
                </h3>
              </div>
              {hasCustomLLM && (
                <button
                  type="button"
                  onClick={handleResetLLM}
                  className="inline-flex items-center space-x-1 text-[11px] text-danger hover:underline font-medium"
                >
                  <RotateCcw className="w-3 h-3" />
                  <span>Reset</span>
                </button>
              )}
            </div>
            <p className="text-[11px] text-mutedText dark:text-neutral-400 font-normal mb-3">
              Use your own credits with any OpenAI-compatible provider (OpenAI, OpenRouter, Groq, DeepSeek, Ollama).
            </p>

            <div className="space-y-2.5">
              <div>
                <label className="block text-[11px] font-semibold text-neutral-700 dark:text-neutral-300 mb-1">
                  API Key
                </label>
                <div className="relative">
                  <Input
                    type={showApiKey ? "text" : "password"}
                    placeholder="sk-... or your custom API key"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    className="h-8 text-xs pr-8 dark:bg-neutral-900 dark:border-neutral-800"
                  />
                  <button
                    type="button"
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200"
                  >
                    {showApiKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-neutral-700 dark:text-neutral-300 mb-1">
                  Host / Base URL (optional)
                </label>
                <Input
                  type="text"
                  placeholder="https://api.openai.com/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  className="h-8 text-xs dark:bg-neutral-900 dark:border-neutral-800"
                />
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-neutral-700 dark:text-neutral-300 mb-1">
                  Model (optional)
                </label>
                <Input
                  type="text"
                  placeholder="e.g. gpt-4o-mini, deepseek-chat"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="h-8 text-xs dark:bg-neutral-900 dark:border-neutral-800"
                />
              </div>
            </div>
          </div>

          <div className="pt-2 border-t border-neutral-100 dark:border-neutral-800/80">
            <Button
              type="button"
              onClick={() => setIsSettingsOpen(false)}
              className="w-full bg-neutral-900 hover:bg-neutral-800 text-white dark:bg-neutral-100 dark:hover:bg-white dark:text-neutral-900 h-8 text-xs font-semibold shadow-sm"
            >
              Done & Save
            </Button>
            <p className="text-[10px] text-center text-mutedText dark:text-neutral-500 mt-1.5">
              Saved in browser localStorage. Leave blank to use server Groq credits.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="space-y-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center space-x-2 mb-1">
                  <div className="w-7 h-7 rounded-full bg-purpleAccent/10 text-purpleAccent flex items-center justify-center">
                    <Sparkles className="w-4 h-4" />
                  </div>
                  <h2 className="font-bold text-lg tracking-tight text-neutral-900 dark:text-white leading-tight">
                    Send to AI
                  </h2>
                </div>
                <p className="text-xs text-mutedText dark:text-neutral-400 font-normal">
                  Generate an engaging LinkedIn post and send it directly to Slack for approval.
                </p>
              </div>

              <button
                type="button"
                onClick={() => setIsSettingsOpen(true)}
                className={`inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border transition-colors shrink-0 ml-2 ${
                  hasCustomLLM
                    ? "bg-purpleAccent/10 text-purpleAccent border-purpleAccent/30 hover:bg-purpleAccent/20"
                    : "bg-neutral-100 dark:bg-neutral-800/80 text-neutral-600 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-200/70"
                }`}
                title="Configure custom OpenAI-compatible API key & host"
              >
                <Settings2 className="w-3 h-3" />
                <span>{hasCustomLLM ? (model ? model.slice(0, 12) : "Custom LLM") : "LLM Settings"}</span>
                {hasCustomLLM && (
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                )}
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label htmlFor="topic-input" className="block text-xs font-semibold text-neutral-700 dark:text-neutral-300 mb-1.5">
                  Post Topic
                </label>
                <Input
                  id="topic-input"
                  type="text"
                  placeholder="e.g. AI advancements and modern software engineering"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  disabled={generateMutation.isPending}
                  className="dark:bg-neutral-900 dark:border-neutral-800 h-9 text-xs"
                />
              </div>

              <Button
                type="submit"
                disabled={generateMutation.isPending}
                className="w-full bg-accent text-neutral-950 hover:bg-accent-hover font-bold shadow-sm h-9 text-xs"
              >
                {generateMutation.isPending ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 mr-2 animate-spin" />
                    <span>Generating with AI...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="w-3.5 h-3.5 mr-2" />
                    <span>Generate Post</span>
                  </>
                )}
              </Button>
            </form>
          </div>

          {/* Result / Output section with fixed remaining height */}
          <div className="flex-1 min-h-0 pt-3 flex flex-col">
            {generateMutation.isSuccess && generatedPost ? (
              <div
                ref={postResultRef}
                className="flex-1 min-h-0 flex flex-col rounded-2xl bg-neutral-50 dark:bg-neutral-900/80 border border-neutral-200 dark:border-neutral-800/80 p-3.5 space-y-2 overflow-hidden"
              >
                <div className="flex items-center justify-between text-xs shrink-0">
                  <div className="flex items-center space-x-1.5 text-neutral-800 dark:text-neutral-200 font-semibold truncate">
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                    <span className="truncate">Post #{generatedPost.post_id} sent to Slack</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleCopy(generatedPost.generated_content)}
                    className="inline-flex items-center space-x-1 text-[11px] text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 transition-colors shrink-0 ml-2"
                  >
                    {copied ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-500" />
                        <span className="text-emerald-500 font-medium">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto text-xs text-neutral-800 dark:text-neutral-200 whitespace-pre-wrap leading-relaxed pr-1 select-text">
                  {generatedPost.generated_content}
                </div>
              </div>
            ) : generateMutation.isError ? (
              <div className="flex-1 min-h-0 rounded-2xl bg-danger/10 border border-danger/30 p-3 flex items-start space-x-2 text-xs text-danger overflow-y-auto">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{generateMutation.error?.message || "Failed to generate post. Please check your connection."}</span>
              </div>
            ) : (
              <div className="flex-1 min-h-0 rounded-2xl border border-dashed border-neutral-200 dark:border-neutral-800/80 p-3 flex flex-col items-center justify-center text-center">
                {generateMutation.isPending ? (
                  <div className="space-y-1.5 flex flex-col items-center">
                    <Loader2 className="w-4 h-4 text-purpleAccent animate-spin" />
                    <p className="text-xs text-neutral-600 dark:text-neutral-400">
                      Generating post with AI...
                    </p>
                  </div>
                ) : (
                  <p className="text-xs text-mutedText dark:text-neutral-500">
                    Generated post & Slack status will appear here
                  </p>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};

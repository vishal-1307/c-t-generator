"use client";

import { useEffect, useRef, useState } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  Copy,
  CornerDownLeft,
  Loader2,
  Maximize2,
  Minimize2,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAcademicContext } from "@/lib/academicContext";
import type { AssistantChatMessage } from "@/lib/types";

const SUGGESTED_PROMPTS = [
  "How is Section 2401 scheduled?",
  "Are there any gaps or clashes?",
  "Do we need to add more teachers?",
  "Show faculty workloads",
  "Which rooms are used for labs?",
];

export function AssistantPanel() {
  const [isOpen, setIsOpen] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [messages, setMessages] = useState<AssistantChatMessage[]>([
    {
      role: "assistant",
      content:
        "👋 Welcome! I am your **AI Timetable Assistant**, powered by Gemini and backed by the deterministic CP-SAT engine.\n\nI can help you review section schedules, check for gaps, inspect teacher workloads, and explain scheduling choices. What would you like to explore?",
    },
  ]);
  const [inputValue, setInputValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [suggestedActions, setSuggestedActions] = useState<string[]>(SUGGESTED_PROMPTS);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const { currentId } = useAcademicContext();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    if (isOpen) {
      scrollToBottom();
      inputRef.current?.focus();
    }
  }, [isOpen, messages, loading]);

  const handleSend = async (textToSend?: string) => {
    const text = (textToSend ?? inputValue).trim();
    if (!text || loading) return;

    const userMsg: AssistantChatMessage = { role: "user", content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInputValue("");
    setLoading(true);

    try {
      const res = await api.assistant.chat(
        text,
        currentId ?? undefined,
        undefined,
        newMessages.slice(-6)
      );

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.reply },
      ]);
      if (res.suggested_actions && res.suggested_actions.length > 0) {
        setSuggestedActions(res.suggested_actions);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "⚠️ Sorry, I encountered an issue fetching timetable details. Please ensure the backend is running and try again.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const renderFormattedContent = (content: string) => {
    // Basic formatted parser for markdown headers, bold, bullets
    const lines = content.split("\n");
    return lines.map((line, idx) => {
      if (line.startsWith("### ")) {
        return (
          <h4 key={idx} className="mt-2 mb-1 text-sm font-semibold text-ink">
            {line.replace("### ", "")}
          </h4>
        );
      }
      if (line.startsWith("- ")) {
        const text = line.substring(2);
        return (
          <div key={idx} className="my-0.5 flex items-start gap-1.5 text-xs leading-relaxed text-ink-muted">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-brand-primary" />
            <span>{renderInlineFormatting(text)}</span>
          </div>
        );
      }
      if (line.trim() === "") {
        return <div key={idx} className="h-1.5" />;
      }
      return (
        <p key={idx} className="my-0.5 text-xs leading-relaxed text-ink-muted">
          {renderInlineFormatting(line)}
        </p>
      );
    });
  };

  const renderInlineFormatting = (text: string) => {
    // Simple inline bold and code highlighter
    const parts = text.split(/(\*\*.*?\*\*|`.*?`|\*.*?\*)/g);
    return parts.map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return (
          <strong key={i} className="font-semibold text-ink">
            {part.slice(2, -2)}
          </strong>
        );
      }
      if (part.startsWith("`") && part.endsWith("`")) {
        return (
          <code
            key={i}
            className="rounded bg-surface-sunken px-1 py-0.5 font-mono text-[11px] text-ink"
          >
            {part.slice(1, -1)}
          </code>
        );
      }
      if (part.startsWith("*") && part.endsWith("*")) {
        return (
          <em key={i} className="italic text-ink-muted">
            {part.slice(1, -1)}
          </em>
        );
      }
      return part;
    });
  };

  return (
    <>
      {/* Floating launcher trigger */}
      {!isOpen && (
        <div className="fixed right-4 bottom-16 z-40 sm:right-6 sm:bottom-6">
          <button
            type="button"
            onClick={() => setIsOpen(true)}
            className="group flex items-center gap-2.5 rounded-full bg-slate-900 px-4 py-2.5 text-white shadow-xl shadow-slate-900/20 ring-1 ring-white/20 transition-all duration-200 hover:scale-105 hover:bg-slate-800 active:scale-95"
            aria-label="Open AI Timetable Assistant"
          >
            <span className="relative flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-tr from-indigo-500 to-sky-400 text-white shadow-sm">
              <Sparkles className="h-3.5 w-3.5 animate-pulse" />
            </span>
            <span className="text-xs font-medium tracking-wide">
              AI Assistant
            </span>
            <span className="rounded-full bg-indigo-500/30 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-indigo-300">
              Gemini
            </span>
          </button>
        </div>
      )}

      {/* Floating Chat Drawer/Dialog */}
      {isOpen && (
        <div
          className={`fixed right-3 bottom-3 z-50 flex flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl transition-all duration-300 sm:right-6 sm:bottom-6 ${
            isExpanded
              ? "h-[85vh] w-[95vw] sm:w-[600px]"
              : "h-[540px] w-[92vw] sm:w-[410px]"
          }`}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-line bg-surface-sunken/60 px-4 py-3 backdrop-blur-sm">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-tr from-indigo-600 to-sky-500 text-white shadow-sm">
                <Sparkles className="h-4 w-4" />
              </div>
              <div>
                <div className="flex items-center gap-1.5">
                  <h3 className="text-xs font-semibold text-ink">
                    Timetable Assistant
                  </h3>
                  <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[9px] font-medium text-indigo-600 dark:bg-indigo-950/50 dark:text-indigo-400">
                    Gemini AI
                  </span>
                </div>
                <p className="text-[10px] text-ink-muted">
                  Conflict-free schedule intelligence
                </p>
              </div>
            </div>

            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setIsExpanded(!isExpanded)}
                className="rounded-lg p-1.5 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
                title={isExpanded ? "Standard size" : "Expand window"}
              >
                {isExpanded ? (
                  <Minimize2 className="h-3.5 w-3.5" />
                ) : (
                  <Maximize2 className="h-3.5 w-3.5" />
                )}
              </button>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded-lg p-1.5 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
                title="Close Assistant"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Quick Context Status Banner */}
          <div className="flex items-center justify-between border-b border-line/60 bg-surface px-4 py-1.5 text-[10px] text-ink-muted">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              OR-Tools CP-SAT: 100% Compact
            </span>
            <span>0 Conflicts</span>
          </div>

          {/* Chat Messages */}
          <div className="flex-1 space-y-3 overflow-y-auto p-4 text-xs">
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`flex gap-2 ${
                  msg.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                {msg.role === "assistant" && (
                  <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-indigo-100 text-indigo-600 dark:bg-indigo-950 dark:text-indigo-400">
                    <Bot className="h-3.5 w-3.5" />
                  </div>
                )}
                <div
                  className={`group relative max-w-[85%] rounded-2xl px-3.5 py-2.5 ${
                    msg.role === "user"
                      ? "bg-slate-900 text-white"
                      : "border border-line/70 bg-surface-sunken/40 text-ink"
                  }`}
                >
                  {msg.role === "assistant" ? (
                    <div>{renderFormattedContent(msg.content)}</div>
                  ) : (
                    <div className="text-xs leading-relaxed text-white">
                      {msg.content}
                    </div>
                  )}

                  {msg.role === "assistant" && (
                    <button
                      type="button"
                      onClick={() => copyToClipboard(msg.content, i)}
                      className="absolute top-2 right-2 rounded p-1 text-ink-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100"
                      title="Copy response"
                    >
                      {copiedIndex === i ? (
                        <Check className="h-3 w-3 text-emerald-500" />
                      ) : (
                        <Copy className="h-3 w-3" />
                      )}
                    </button>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex items-center gap-2 text-ink-muted">
                <div className="flex h-6 w-6 items-center justify-center rounded-md bg-indigo-100 text-indigo-600 dark:bg-indigo-950 dark:text-indigo-400">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                </div>
                <div className="rounded-2xl border border-line bg-surface-sunken/40 px-3 py-2 text-xs text-ink-muted">
                  Consulting schedule data...
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Quick Prompts Chips */}
          <div className="border-t border-line/60 bg-surface-sunken/30 px-3 py-2">
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
              {suggestedActions.slice(0, 4).map((action, i) => (
                <button
                  key={i}
                  type="button"
                  disabled={loading}
                  onClick={() => handleSend(action)}
                  className="shrink-0 rounded-full border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition-colors hover:border-line-strong hover:bg-surface-hover hover:text-ink disabled:opacity-50"
                >
                  {action}
                </button>
              ))}
            </div>
          </div>

          {/* Input Area */}
          <div className="border-t border-line bg-surface p-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
              className="flex items-center gap-2"
            >
              <input
                ref={inputRef}
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder="Ask about sections, faculty workload, gaps..."
                disabled={loading}
                className="flex-1 rounded-xl border border-line bg-surface-sunken/50 px-3 py-2 text-xs text-ink outline-none transition focus:border-brand-primary focus:bg-surface"
              />
              <button
                type="submit"
                disabled={!inputValue.trim() || loading}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white transition hover:bg-slate-800 disabled:opacity-40"
                aria-label="Send query"
              >
                <CornerDownLeft className="h-3.5 w-3.5" />
              </button>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

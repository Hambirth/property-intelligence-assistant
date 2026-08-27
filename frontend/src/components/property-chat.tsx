"use client";

import { FormEvent, KeyboardEvent, ReactNode, RefObject, useEffect, useRef, useState } from "react";

import { ApiError, streamChat } from "@/lib/api";
import type { ApiSource, ChatResponse, SourceFilter } from "@/types/api";

const MAX_MESSAGE_LENGTH = 2000;

const PROMPTS: Record<SourceFilter | "all", string[]> = {
  all: [
    "Tell me about The Astera.",
    "Which DarGlobal residences feature interiors by luxury brands?",
    "What is the price of the 103 SQM three-bedroom apartment in Al Naeem?",
    "How many bedrooms are in the 480,000 SAR apartment in Al Seef, Dammam?",
    "Compare the DarGlobal Mouawad residence with the 291 SQM Wasalt villa.",
    "What information is available about W Residences Dubai?",
  ],
  darglobal: [
    "Tell me about The Astera.",
    "Which DarGlobal residences feature interiors by luxury brands?",
    "What Missoni project is near Finca Cortesin on the Costa del Sol?",
    "Which Riyadh project is designed with Mouawad?",
    "What information is available about W Residences Dubai?",
    "Compare the design partnerships of The Astera and Tierra Viva.",
  ],
  wasalt: [
    "What is the price of the 103 SQM three-bedroom apartment in Al Naeem?",
    "How many bedrooms are in the 480,000 SAR apartment in Al Seef, Dammam?",
    "What are the details of the 470,000 SAR Al Wahah apartment?",
    "Which 238 SQM south-facing villa costs 900,000 SAR?",
    "How many bedrooms are in the 291 SQM north-facing Wasalt villa?",
    "Compare the 470,000 SAR Al Wahah apartment with the 480,000 SAR Al Seef apartment.",
  ],
};

type LoadingStage = "searching" | "preparing";

interface UserMessage {
  id: string;
  role: "user";
  text: string;
  source: SourceFilter | null;
}

interface AssistantMessage {
  id: string;
  role: "assistant";
  status: "loading" | "complete" | "error";
  stage?: LoadingStage;
  answer?: string;
  refused?: boolean;
  sources?: ApiSource[];
  requestId?: string;
  error?: string;
  kind?: "grounded" | "greeting" | "identity";
}

type Message = UserMessage | AssistantMessage;

const sourceLabels: Record<SourceFilter | "all", string> = {
  all: "All sources",
  darglobal: "DarGlobal",
  wasalt: "Wasalt",
};

export function PropertyChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [source, setSource] = useState<SourceFilter | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [validation, setValidation] = useState("");
  const [isDataOpen, setIsDataOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dataDetailsRef = useRef<HTMLDetailsElement>(null);
  const latestAnswerRef = useRef<HTMLElement>(null);
  const hasConversation = messages.length > 0;

  useEffect(() => {
    if (!hasConversation) return;
    const frame = requestAnimationFrame(() => {
      latestAnswerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, hasConversation]);

  useEffect(() => {
    const closeFromOutside = (event: PointerEvent) => {
      if (isDataOpen && !dataDetailsRef.current?.contains(event.target as Node)) setIsDataOpen(false);
    };
    const closeFromKeyboard = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setIsDataOpen(false);
    };
    document.addEventListener("pointerdown", closeFromOutside);
    document.addEventListener("keydown", closeFromKeyboard);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      document.removeEventListener("keydown", closeFromKeyboard);
    };
  }, [isDataOpen]);

  const resetConversation = () => {
    setMessages([]);
    setDraft("");
    setValidation("");
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const submitQuestion = async (question: string) => {
    const normalized = question.trim();
    if (!normalized) {
      setValidation("Enter a question before sending.");
      textareaRef.current?.focus();
      return;
    }
    if (normalized.length > MAX_MESSAGE_LENGTH) {
      setValidation(`Questions must be ${MAX_MESSAGE_LENGTH.toLocaleString()} characters or fewer.`);
      return;
    }
    if (isSubmitting) return;

    const messageId = crypto.randomUUID();
    const answerId = crypto.randomUUID();
    setValidation("");
    setDraft("");

    const localResponse = getLocalResponse(normalized);
    if (localResponse) {
      setMessages((current) => [
        ...current,
        { id: messageId, role: "user", text: normalized, source },
        {
          id: answerId,
          role: "assistant",
          status: "complete",
          kind: localResponse.kind,
          answer: localResponse.answer,
          refused: false,
          sources: [],
        },
      ]);
      requestAnimationFrame(() => textareaRef.current?.focus());
      return;
    }

    setIsSubmitting(true);
    setMessages((current) => [
      ...current,
      { id: messageId, role: "user", text: normalized, source },
      { id: answerId, role: "assistant", status: "loading", stage: "searching" },
    ]);

    try {
      const response = await streamChat(
        { message: normalized, ...(source ? { source } : {}) },
        {
          onStart: (requestId) => {
            setMessages((current) => updateAssistant(current, answerId, {
              stage: "preparing",
              requestId,
            }));
          },
        },
      );
      setMessages((current) => completeAssistant(current, answerId, response));
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      const apiError = error instanceof ApiError ? error : new ApiError("Network error", 0);
      setMessages((current) => updateAssistant(current, answerId, {
        status: "error",
        error: errorMessage(apiError),
        requestId: apiError.requestId,
      }));
    } finally {
      setIsSubmitting(false);
      requestAnimationFrame(() => textareaRef.current?.focus());
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submitQuestion(draft);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion(draft);
    }
  };

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="wordmark" href="#main" aria-label="Property Intelligence home">
          <span className="wordmark-mark" aria-hidden="true">PI</span>
          <span>
            <strong>Property Intelligence</strong>
            <small>Verified property research</small>
          </span>
        </a>
        <div className="header-actions">
          <span className="corpus-status"><i aria-hidden="true" /> Corpus online</span>
          {hasConversation && (
            <button className="text-button" type="button" onClick={resetConversation}>New conversation</button>
          )}
          <details
            className="data-details"
            ref={dataDetailsRef}
            open={isDataOpen}
            onToggle={(event) => setIsDataOpen(event.currentTarget.open)}
          >
            <summary>About the data</summary>
            <div className="data-popover">
              <p className="section-number">Data transparency</p>
              <h2>Public sources, clearly attributed.</h2>
              <p>Answers are generated from an indexed assignment corpus of publicly available DarGlobal and Wasalt materials.</p>
              <p>Some pages were manually imported after normal browser access because their sites restrict automation. Availability and pricing can change—verify important details at the cited source.</p>
            </div>
          </details>
        </div>
      </header>

      <main id="main" className={hasConversation ? "conversation-state" : "empty-state"}>
        {!hasConversation ? (
          <EmptyState source={source} setSource={setSource} onPrompt={submitQuestion} />
        ) : (
          <section className="conversation" aria-labelledby="conversation-title">
            <h1 id="conversation-title" className="sr-only">Source-grounded answers</h1>
            <div className="message-list" aria-live="polite" aria-relevant="additions text">
              {messages.map((message, index) => (
                <MessageView
                  key={message.id}
                  message={message}
                  onFollowUp={submitQuestion}
                  followUpsDisabled={isSubmitting}
                  messageRef={message.role === "assistant" && index === messages.length - 1 ? latestAnswerRef : undefined}
                />
              ))}
            </div>
          </section>
        )}

        <Composer
          value={draft}
          setValue={setDraft}
          onSubmit={onSubmit}
          onKeyDown={onKeyDown}
          disabled={isSubmitting}
          validation={validation}
          textareaRef={textareaRef}
          compact={hasConversation}
          source={source}
          setSource={setSource}
        />
      </main>

      <footer className="site-footer">
        <p>Property information is based on indexed public source material and may not reflect current availability or pricing. Verify details with the original source.</p>
        <span>Public corpus · Source-backed answers</span>
      </footer>
    </div>
  );
}

function EmptyState({
  source,
  setSource,
  onPrompt,
}: {
  source: SourceFilter | null;
  setSource: (source: SourceFilter | null) => void;
  onPrompt: (prompt: string) => Promise<void>;
}) {
  const prompts = PROMPTS[source ?? "all"];

  return (
    <>
      <section className="hero-layout" aria-labelledby="page-title">
        <div className="intro">
          <p className="eyebrow"><span aria-hidden="true" /> Grounded property research</p>
          <h1 id="page-title">Find the detail.<br /><em>See the source.</em></h1>
          <p className="intro-copy">Explore publicly available property intelligence from DarGlobal and Wasalt. Every supported answer is traced back to the original indexed material.</p>
          <div className="trust-row" aria-label="Product commitments">
            <span>Public corpus</span><span>Verified citations</span><span>No invented URLs</span>
          </div>
        </div>
        <aside className="hero-proof" aria-label="Indexed corpus summary">
          <div className="proof-orbit" aria-hidden="true"><span>PI</span></div>
          <p className="proof-kicker">Indexed assignment corpus</p>
          <strong className="proof-total">20</strong>
          <p className="proof-label">verified source documents</p>
          <div className="proof-sources">
            <div><span>DarGlobal</span><strong>10</strong><small>Official brochures</small></div>
            <div><span>Wasalt</span><strong>10</strong><small>Property pages</small></div>
          </div>
          <p className="proof-footnote"><i aria-hidden="true" /> Evidence checked before every answer</p>
        </aside>
      </section>

      <section className="question-panel" aria-labelledby="question-heading">
        <div className="panel-heading">
          <div><p className="section-number">01 / Research desk</p><h2 id="question-heading">What would you like to know?</h2></div>
          <SourceControl value={source} onChange={setSource} />
        </div>
        <div className="prompt-grid">
          {prompts.map((prompt, index) => (
            <button className="prompt-card" type="button" key={prompt} onClick={() => void onPrompt(prompt)}>
              <span>{String(index + 1).padStart(2, "0")}</span><span>{prompt}</span><span className="prompt-arrow" aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      </section>
    </>
  );
}

function SourceControl({ value, onChange, disabled = false }: {
  value: SourceFilter | null;
  onChange: (source: SourceFilter | null) => void;
  disabled?: boolean;
}) {
  const scope = value === "darglobal"
    ? "New questions search DarGlobal only"
    : value === "wasalt"
      ? "New questions search Wasalt only"
      : "New questions search all indexed sources";

  return (
    <div className="source-filter-group">
      <div className="source-control" aria-label="Search source">
        {([null, "darglobal", "wasalt"] as const).map((option) => (
          <button
            className={`source-option ${value === option ? "active" : ""}`}
            type="button"
            key={option ?? "all"}
            aria-pressed={value === option}
            disabled={disabled}
            onClick={() => onChange(option)}
          >
            <span className="source-option-check" aria-hidden="true">{value === option ? "✓" : ""}</span>
            {sourceLabels[option ?? "all"]}
          </button>
        ))}
      </div>
      <p className="source-scope" aria-live="polite">{scope}</p>
    </div>
  );
}

function Composer({ value, setValue, onSubmit, onKeyDown, disabled, validation, textareaRef, compact, source, setSource }: {
  value: string;
  setValue: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  disabled: boolean;
  validation: string;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  compact: boolean;
  source: SourceFilter | null;
  setSource: (source: SourceFilter | null) => void;
}) {
  return (
    <div className={compact ? "composer-dock" : "composer-area"}>
      {compact && (
        <details className="composer-scope-picker">
          <summary>Search: {sourceLabels[source ?? "all"]}</summary>
          <div className="composer-scope-popover">
            <SourceControl value={source} onChange={setSource} disabled={disabled} />
          </div>
        </details>
      )}
      <form className="composer" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="question">Ask about a property</label>
        <textarea
          ref={textareaRef}
          id="question"
          rows={1}
          maxLength={MAX_MESSAGE_LENGTH}
          value={value}
          disabled={disabled}
          aria-describedby="composer-help validation-message"
          aria-invalid={Boolean(validation)}
          placeholder="Ask about a property, location, price, or amenity…"
          onChange={(event) => {
            setValue(event.target.value);
            event.target.style.height = "auto";
            event.target.style.height = `${Math.min(event.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={onKeyDown}
        />
        <div className="composer-meta">
          {value.length > 1600 && <span>{value.length.toLocaleString()} / {MAX_MESSAGE_LENGTH.toLocaleString()}</span>}
          <button className="send-button" type="submit" disabled={disabled || !value.trim()} aria-label={disabled ? "Waiting for answer" : "Send question"}>
            <span className="send-label">{disabled ? "Working" : "Ask"}</span><span aria-hidden="true">↑</span>
          </button>
        </div>
      </form>
      <div className="composer-notes">
        <p id="composer-help">Enter to send · Shift + Enter for a new line</p>
        <p id="validation-message" className="validation-message" role={validation ? "alert" : undefined}>{validation}</p>
        {!compact && <p>Each question is evaluated independently against the available sources.</p>}
      </div>
    </div>
  );
}

function MessageView({
  message,
  messageRef,
  onFollowUp,
  followUpsDisabled,
}: {
  message: Message;
  messageRef?: RefObject<HTMLElement | null>;
  onFollowUp: (question: string) => Promise<void>;
  followUpsDisabled: boolean;
}) {
  if (message.role === "user") {
    return (
      <article className="message user-message">
        <div className="message-label"><span>You</span><span>{sourceLabels[message.source ?? "all"]}</span></div>
        <p>{message.text}</p>
      </article>
    );
  }

  return (
    <article ref={messageRef} className={`message assistant-message ${message.refused ? "refusal-message" : ""} ${message.status === "error" ? "error-message" : ""}`}>
      <div className="message-label">
        <span>Property Intelligence</span>
        <span>{assistantLabel(message)}</span>
      </div>
      {message.status === "loading" && (
        <div className="loading-state" role="status">
          <span className="loading-dots" aria-hidden="true"><i /><i /><i /></span>
          {message.stage === "preparing" ? "Preparing grounded answer…" : "Searching property sources…"}
        </div>
      )}
      {message.status === "error" && (
        <div role="alert">
          <h2>We couldn’t complete that request.</h2>
          <p>{message.error}</p>
          {message.requestId && <small>Reference: {message.requestId}</small>}
        </div>
      )}
      {message.status === "complete" && (
        <>
          <AnswerText text={message.answer ?? ""} />
          {message.refused && <p className="refusal-tip">Try asking about a specific project, location, property type, bedrooms, price, or amenity.</p>}
          {Boolean(message.sources?.length) && <SourceCards sources={message.sources ?? []} />}
          <FollowUpQuestions
            questions={getFollowUpQuestions(message)}
            disabled={followUpsDisabled}
            onSelect={onFollowUp}
          />
        </>
      )}
    </article>
  );
}

function FollowUpQuestions({
  questions,
  disabled,
  onSelect,
}: {
  questions: string[];
  disabled: boolean;
  onSelect: (question: string) => Promise<void>;
}) {
  if (questions.length === 0) return null;
  return (
    <section className="follow-ups" aria-label="Suggested follow-up questions">
      <p>Continue exploring</p>
      <div className="follow-up-list">
        {questions.map((question) => (
          <button type="button" key={question} disabled={disabled} onClick={() => void onSelect(question)}>
            <span>{question}</span><span aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function AnswerText({ text }: { text: string }) {
  const normalized = text.replace(/\\([\\`*{}\[\]()#+\-.!_>@])/g, "$1");
  const lines = normalized.split("\n");
  const blocks: Array<
    | { type: "paragraph"; text: string }
    | { type: "heading"; text: string; level: number }
    | { type: "unordered-list"; items: string[] }
    | { type: "ordered-list"; items: string[] }
  > = [];

  for (let index = 0; index < lines.length;) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      blocks.push({ type: "heading", text: heading[2], level: heading[1].length });
      index += 1;
      continue;
    }

    const unordered = /^[-*•]\s+/.test(line);
    const ordered = /^\d+[.)]\s+/.test(line);
    if (unordered || ordered) {
      const items: string[] = [];
      const pattern = unordered ? /^[-*•]\s+/ : /^\d+[.)]\s+/;
      while (index < lines.length && pattern.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(pattern, ""));
        index += 1;
      }
      blocks.push({ type: ordered ? "ordered-list" : "unordered-list", items });
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (index < lines.length) {
      const next = lines[index].trim();
      if (!next || /^(#{1,3})\s+/.test(next) || /^[-*•]\s+/.test(next) || /^\d+[.)]\s+/.test(next)) break;
      paragraph.push(next);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join(" ") });
  }

  return (
    <div className="answer-copy">
      {blocks.map((block, index) => {
        if (block.type === "unordered-list" || block.type === "ordered-list") {
          const List = block.type === "ordered-list" ? "ol" : "ul";
          return <List key={index}>{block.items.map((item, itemIndex) => <li key={`${item}-${itemIndex}`}>{renderInlineMarkdown(item)}</li>)}</List>;
        }
        if (block.type === "heading") {
          const Heading = block.level === 1 ? "h2" : block.level === 2 ? "h3" : "h4";
          return <Heading key={index}>{renderInlineMarkdown(block.text)}</Heading>;
        }
        return <p key={index}>{renderInlineMarkdown(block.text)}</p>;
      })}
    </div>
  );
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const tokens = text.split(/(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*]+\*|_[^_]+_)/g).filter(Boolean);
  return tokens.map((token, index) => {
    if ((token.startsWith("**") && token.endsWith("**")) || (token.startsWith("__") && token.endsWith("__"))) {
      return <strong key={index}>{token.slice(2, -2)}</strong>;
    }
    if (token.startsWith("`") && token.endsWith("`")) return <code key={index}>{token.slice(1, -1)}</code>;
    if ((token.startsWith("*") && token.endsWith("*")) || (token.startsWith("_") && token.endsWith("_"))) {
      return <em key={index}>{token.slice(1, -1)}</em>;
    }
    return token;
  });
}

function SourceCards({ sources }: { sources: ApiSource[] }) {
  const uniqueSources = sources.filter(
    (source, index) => sources.findIndex((candidate) => candidate.url === source.url) === index,
  );
  return (
    <section className="sources" aria-label="Sources">
      <div className="sources-heading"><h2>Sources</h2><span>{uniqueSources.length} verified {uniqueSources.length === 1 ? "reference" : "references"}</span></div>
      <div className="source-cards">
        {uniqueSources.map((item) => (
          <a className="source-card" href={item.url} target="_blank" rel="noopener noreferrer" key={`${item.id}-${item.url}`}>
            <span className={`source-brand source-${item.source.toLowerCase()}`}>{item.source}</span>
            <strong>{item.title}</strong>
            <span className="source-link">View original <span aria-hidden="true">↗</span></span>
          </a>
        ))}
      </div>
    </section>
  );
}

function updateAssistant(messages: Message[], id: string, patch: Partial<AssistantMessage>): Message[] {
  return messages.map((message) => message.id === id && message.role === "assistant" ? { ...message, ...patch } : message);
}

function completeAssistant(messages: Message[], id: string, response: ChatResponse): Message[] {
  return updateAssistant(messages, id, {
    status: "complete",
    kind: "grounded",
    answer: response.answer,
    refused: response.refused,
    sources: response.sources,
    requestId: response.request_id,
  });
}

function getLocalResponse(question: string): Pick<AssistantMessage, "kind" | "answer"> | undefined {
  if (/^(?:hi|hello|hey|hello there|good morning|good afternoon|good evening|salaam|salam|assalamu alaikum|مرحبا)[!.,?\s]*$/iu.test(question)) {
    return {
      kind: "greeting",
      answer: "Hello — welcome to Property Intelligence. Ask me about a DarGlobal project, a Wasalt listing, or compare properties in the indexed public sources.",
    };
  }
  if (/^(?:(?:(?:can|could|would) you )?(?:tell me about yourself|introduce yourself)|who are you|what are you|what can you do|how can you help(?: me)?)[!.,?\s]*$/iu.test(question)) {
    return {
      kind: "identity",
      answer: "I'm Property Intelligence, a focused research assistant for the indexed public DarGlobal and Wasalt corpus. I can answer questions about projects and listings, compare evidence when it is available, and link to original sources. I evaluate every question independently and will refuse rather than invent unsupported property facts.",
    };
  }
  return undefined;
}

function getFollowUpQuestions(message: AssistantMessage): string[] {
  if (message.kind === "greeting" || message.kind === "identity") return PROMPTS.all.slice(0, 3);
  if (message.refused || !message.sources?.length) {
    return [
      "Which DarGlobal residences feature luxury-brand interiors?",
      "What details are available for the Wasalt 238 SQM villa?",
      "Compare a DarGlobal residence with a Wasalt property.",
    ];
  }

  const uniqueSources = message.sources.filter(
    (item, index, items) => items.findIndex((candidate) => candidate.url === item.url) === index,
  );
  const first = conciseTitle(uniqueSources[0].title);
  const second = uniqueSources[1] ? conciseTitle(uniqueSources[1].title) : undefined;
  const organization = uniqueSources[0].source;

  return [
    `What other details are available about ${first}?`,
    second
      ? `How do ${first} and ${second} compare?`
      : `What location, price, and amenities are listed for ${first}?`,
    `What other ${organization} properties are available in the indexed sources?`,
  ];
}

function conciseTitle(title: string): string {
  const normalized = title.replace(/\s+/g, " ").trim();
  return normalized.length <= 80 ? normalized : `${normalized.slice(0, 77).trimEnd()}…`;
}

function assistantLabel(message: AssistantMessage): string {
  if (message.kind === "greeting") return "Getting started";
  if (message.kind === "identity") return "About this assistant";
  if (message.refused) return "No supported answer";
  return "Grounded response";
}

function errorMessage(error: ApiError): string {
  if (error.status === 429) {
    return `You've reached the temporary request limit. Please try again shortly.${formatRetryAfter(error.retryAfter)}`;
  }
  if (error.status === 503) return "The AI provider is temporarily unavailable. Please try again.";
  if (error.status === 504) return "The response took too long. Please try again.";
  if (error.status === 502) return "The AI service returned an invalid response. Please try again.";
  if (error.status === 422) return "That question could not be accepted. Shorten it and try again.";
  return "Unable to reach the service. Check your connection and try again.";
}

function formatRetryAfter(value?: string): string {
  if (!value) return "";
  const seconds = Number(value);
  return Number.isFinite(seconds) ? ` Try again in about ${Math.max(1, Math.ceil(seconds))} seconds.` : "";
}

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, streamChat } from "@/lib/api";

import { PropertyChat } from "./property-chat";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, streamChat: vi.fn() };
});

const streamChatMock = vi.mocked(streamChat);

const answer = {
  answer: "The Astera features interiors by Aston Martin.",
  refused: false,
  sources: [{ id: "S1", title: "The Astera", url: "https://www.darglobal.co.uk/the-astera", source: "DarGlobal" as const }],
  request_id: "req-success",
};

beforeEach(() => {
  streamChatMock.mockReset();
  streamChatMock.mockImplementation(async (_request, handlers) => {
    handlers.onStart("req-success");
    return answer;
  });
});

describe("PropertyChat", () => {
  it("renders the empty state, trust copy, suggestions, and disabled composer", () => {
    render(<PropertyChat />);
    expect(screen.getByRole("heading", { name: /Find the detail/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^\d{2}/ })).toHaveLength(6);
    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
    expect(screen.getByText(/Each question is evaluated independently/i)).toBeInTheDocument();
  });

  it("closes the data popover when clicking outside or pressing Escape", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    const disclosure = screen.getByText("About the data").closest("details");

    await user.click(screen.getByText("About the data"));
    expect(disclosure).toHaveAttribute("open");
    await user.click(document.body);
    expect(disclosure).not.toHaveAttribute("open");

    await user.click(screen.getByText("About the data"));
    await user.keyboard("{Escape}");
    expect(disclosure).not.toHaveAttribute("open");
  });

  it("answers a simple greeting locally without calling the grounded API", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.type(screen.getByLabelText("Ask about a property"), "hello{enter}");

    expect(await screen.findByText(/welcome to Property Intelligence/i)).toBeInTheDocument();
    expect(screen.getByText("Getting started")).toBeInTheDocument();
    expect(screen.queryByText("Grounded response")).not.toBeInTheDocument();
    expect(streamChatMock).not.toHaveBeenCalled();
  });

  it("explains its identity and limits locally without calling the grounded API", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.type(screen.getByLabelText("Ask about a property"), "can you tell me about yourself{enter}");

    expect(await screen.findByText(/focused research assistant/i)).toBeInTheDocument();
    expect(screen.getByText("About this assistant")).toBeInTheDocument();
    expect(screen.queryByText("Grounded response")).not.toBeInTheDocument();
    expect(streamChatMock).not.toHaveBeenCalled();
  });

  it("sends with Enter, completes the stream, and renders a secured citation", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    const input = screen.getByLabelText("Ask about a property");
    await user.type(input, "Tell me about The Astera.{enter}");

    await waitFor(() => expect(streamChatMock).toHaveBeenCalledWith(
      { message: "Tell me about The Astera." },
      expect.objectContaining({ onStart: expect.any(Function) }),
    ));
    expect(await screen.findByText(answer.answer)).toBeInTheDocument();
    const source = screen.getByRole("link", { name: /The Astera/i });
    expect(source).toHaveAttribute("href", answer.sources[0].url);
    expect(source).toHaveAttribute("target", "_blank");
    expect(source).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders context-aware follow-ups and submits one when clicked", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.type(screen.getByLabelText("Ask about a property"), "Tell me about The Astera.{enter}");

    const followUp = await screen.findByRole("button", {
      name: /What other details are available about The Astera/i,
    });
    expect(screen.getByRole("region", { name: "Suggested follow-up questions" })).toBeInTheDocument();
    await user.click(followUp);

    await waitFor(() => expect(streamChatMock).toHaveBeenNthCalledWith(
      2,
      { message: "What other details are available about The Astera?" },
      expect.objectContaining({ onStart: expect.any(Function) }),
    ));
    expect(screen.getByText("What other details are available about The Astera?", { selector: ".user-message p" })).toBeInTheDocument();
  });

  it("renders escaped Markdown as formatted answer content", async () => {
    streamChatMock.mockResolvedValue({
      ...answer,
      answer: "About \\*\\*W Residences Dubai Downtown\\*\\*:\n\n- \\*\\*Location:\\*\\* Downtown Dubai\n- \\*\\*Amenities:\\*\\* Rooftop pool",
    });
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.type(screen.getByLabelText("Ask about a property"), "Show the property{enter}");

    expect(await screen.findByText("W Residences Dubai Downtown", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("Location:", { selector: "strong" }).closest("li")).toHaveTextContent("Location: Downtown Dubai");
    expect(screen.queryByText(/\\\*\\\*/)).not.toBeInTheDocument();
  });

  it("keeps adversarial HTML and Markdown links inert", async () => {
    streamChatMock.mockResolvedValue({
      ...answer,
      answer: '<img src=x onerror="alert(1)"> [click me](javascript:alert(1)) **safe bold**',
    });
    const user = userEvent.setup();
    const { container } = render(<PropertyChat />);
    await user.type(screen.getByLabelText("Ask about a property"), "Show the property{enter}");

    expect(await screen.findByText("safe bold", { selector: "strong" })).toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(container.querySelector('a[href^="javascript:"]')).not.toBeInTheDocument();
    expect(screen.getByText(/<img src=x onerror/)).toBeInTheDocument();
  });

  it("keeps the response viewport clear and moves source controls into the composer", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.click(screen.getByRole("button", { name: /Tell me about The Astera/i }));

    expect(await screen.findByText(answer.answer)).toBeInTheDocument();
    expect(screen.queryByText("Research session")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Source-grounded answers" })).toHaveClass("sr-only");
    expect(screen.getByText("Search: All sources")).toBeInTheDocument();
  });

  it("deduplicates repeated chunk citations by the unchanged backend URL", async () => {
    streamChatMock.mockResolvedValue({ ...answer, sources: [answer.sources[0], { ...answer.sources[0], id: "S2" }] });
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.click(screen.getByRole("button", { name: /Tell me about The Astera/i }));

    expect(await screen.findByText("1 verified reference")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /The Astera/i })).toHaveLength(1);
  });

  it("renders refusal as a supported answer state, not an error", async () => {
    streamChatMock.mockResolvedValue({ answer: "I couldn't find that information in the available sources.", refused: true, sources: [], request_id: "req-refused" });
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.click(screen.getByRole("button", { name: /Tell me about The Astera/i }));

    expect(await screen.findByText(/couldn't find that information/i)).toBeInTheDocument();
    expect(screen.getByText(/specific project, location, property type/i)).toBeInTheDocument();
    expect(screen.queryByText(/couldn’t complete that request/i)).not.toBeInTheDocument();
  });

  it("applies the selected source filter", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.click(screen.getByRole("button", { name: "Wasalt" }));
    await user.click(screen.getByRole("button", { name: /103 SQM/i }));

    await waitFor(() => expect(streamChatMock).toHaveBeenCalledWith(
      expect.objectContaining({ source: "wasalt" }),
      expect.any(Object),
    ));
  });

  it("visibly updates the scope and suggested questions when the source changes", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);

    expect(screen.getByText("New questions search all indexed sources")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Wasalt" }));

    expect(screen.getByText("New questions search Wasalt only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /238 SQM south-facing villa/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Tell me about The Astera/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "DarGlobal" }));
    expect(screen.getByText("New questions search DarGlobal only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Finca Cortesin/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /103 SQM/i })).not.toBeInTheDocument();
  });

  it("uses Shift+Enter for a newline and Enter to submit", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    const input = screen.getByLabelText("Ask about a property");
    await user.type(input, "Line one{shift>}{enter}{/shift}Line two");
    expect(input).toHaveValue("Line one\nLine two");
    expect(streamChatMock).not.toHaveBeenCalled();
    await user.type(input, "{enter}");
    await waitFor(() => expect(streamChatMock).toHaveBeenCalledTimes(1));
  });

  it("rejects empty and over-limit keyboard submissions", async () => {
    const user = userEvent.setup();
    render(<PropertyChat />);
    const input = screen.getByLabelText("Ask about a property");
    await user.type(input, "{enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a question");
    fireEvent.change(input, { target: { value: "x".repeat(2001) } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("alert")).toHaveTextContent("2,000 characters or fewer");
    expect(streamChatMock).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError("limited", 429, "rate_limit_exceeded", "req-429", "9"), /temporary request limit.*9 seconds/i],
    [new ApiError("unavailable", 503, "llm_unavailable"), /provider is temporarily unavailable/i],
    [new ApiError("timeout", 504, "llm_timeout"), /response took too long/i],
    [new ApiError("invalid", 502, "llm_invalid_response"), /invalid response/i],
    [new ApiError("network", 0, "network_error"), /Unable to reach the service/i],
  ])("renders a professional error category", async (failure, copy) => {
    streamChatMock.mockRejectedValue(failure);
    const user = userEvent.setup();
    render(<PropertyChat />);
    await user.type(screen.getByLabelText("Ask about a property"), "Test question{enter}");
    expect(await screen.findByText(copy)).toBeInTheDocument();
  });
});

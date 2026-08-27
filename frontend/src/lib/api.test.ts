import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, parseSSEBlock, streamChat } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("structured SSE client", () => {
  it("parses named events and multiline data", () => {
    expect(parseSSEBlock('event: complete\ndata: {"answer":"A"}\ndata: ')).toEqual({
      event: "complete",
      data: '{"answer":"A"}\n',
    });
  });

  it("handles chunk boundaries, start, and complete without a fallback request", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: start\ndata: {"request_id":"req-test-1"}\n'));
        controller.enqueue(encoder.encode('\nevent: complete\ndata: {"answer":"The Astera has Aston Martin interiors.","refused":false,"sources":[{"id":"S1","title":"The Astera","url":"https://darglobal.co.uk/astera","source":"DarGlobal"}],"request_id":"req-test-1"}\n\n'));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const onStart = vi.fn();

    const result = await streamChat({ message: "Tell me about The Astera." }, { onStart });

    expect(onStart).toHaveBeenCalledWith("req-test-1");
    expect(result.sources[0].url).toBe("https://darglobal.co.uk/astera");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("preserves a 429 status and Retry-After header", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: { code: "rate_limit_exceeded", message: "limited", request_id: "req-rate-2" } }),
      { status: 429, headers: { "Retry-After": "12", "Content-Type": "application/json" } },
    )));

    await expect(streamChat({ message: "Question" }, { onStart: vi.fn() })).rejects.toMatchObject({
      status: 429,
      retryAfter: "12",
      requestId: "req-rate-2",
    } satisfies Partial<ApiError>);
  });

  it("maps a structured provider error event without exposing its message", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: error\ndata: {"error":{"code":"llm_timeout","message":"safe","request_id":"req-test-3"}}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })));

    await expect(streamChat({ message: "Question" }, { onStart: vi.fn() })).rejects.toMatchObject({
      status: 504,
      code: "llm_timeout",
      requestId: "req-test-3",
    } satisfies Partial<ApiError>);
  });

  it.each([
    "javascript:alert(1)",
    "https://evil.example/fake-source",
    "https://user:password@darglobal.co.uk/source",
  ])("rejects an unsafe backend citation URL: %s", async (url) => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          `event: complete\ndata: ${JSON.stringify({
            answer: "Unsafe citation",
            refused: false,
            sources: [{ id: "S1", title: "Fake", url, source: "DarGlobal" }],
            request_id: "req-unsafe-1",
          })}\n\n`,
        ));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })));

    await expect(streamChat({ message: "Question" }, { onStart: vi.fn() })).rejects.toMatchObject({
      status: 502,
      code: "invalid_stream",
    } satisfies Partial<ApiError>);
  });

  it("rejects a non-SSE success response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>not SSE</html>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    })));

    await expect(streamChat({ message: "Question" }, { onStart: vi.fn() })).rejects.toMatchObject({
      status: 502,
      code: "invalid_stream",
    } satisfies Partial<ApiError>);
  });
});

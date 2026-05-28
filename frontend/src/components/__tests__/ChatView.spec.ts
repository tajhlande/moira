import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi, beforeAll } from "vitest";
import { nextTick } from "vue";

function createSSEResponse(events: { event: string; data: string }[]) {
  const lines = events
    .map((e) => `event: ${e.event}\ndata: ${e.data}\n\n`)
    .join("");
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(lines));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

vi.mock("../../api/client", () => ({
  api: {
    createConversation: vi.fn(async () => ({
      id: "conv-1",
      title: "New Conversation",
      created_at: new Date().toISOString(),
    })),
    listConversations: vi.fn(async () => []),
    getConversation: vi.fn(async () => ({
      id: "conv-1",
      title: "New Conversation",
      created_at: new Date().toISOString(),
      messages: [],
    })),
    getModels: vi.fn(async () => ({
      models: [],
      assignments: {
        intelligence: { endpoint: "", model: "" },
        task: { endpoint: "", model: "" },
      },
    })),
    setModels: vi.fn(async (assignments) => assignments),
  },
}));

import { api } from "../../api/client";
import { useChatStore } from "../../stores/chat";
import ChatView from "../ChatView.vue";

async function flushUi() {
  await Promise.resolve();
  await nextTick();
  await Promise.resolve();
  await nextTick();
}

describe("ChatView", () => {
  it("renders chat messages using mocked SSE streaming", async () => {
    mockFetch.mockResolvedValueOnce(
      createSSEResponse([
        {
          event: "node_start",
          data: JSON.stringify({
            node: "planning",
            timestamp: "t1",
            started_at: new Date().toISOString(),
          }),
        },
        {
          event: "node_end",
          data: JSON.stringify({
            node: "planning",
            timestamp: "t2",
            budget_remaining: 48,
            elapsed_ms: 1200,
          }),
        },
        {
          event: "run_complete",
          data: JSON.stringify({
            report: {
              answer: "Mocked SSE response",
              citations: [],
              support: [],
              critiques: [],
              unverified_claims: [],
              budget_consumed: 21,
            },
            total_elapsed_ms: 5000,
          }),
        },
      ])
    );

    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(ChatView, {
      global: { plugins: [pinia] },
    });

    const store = useChatStore();
    await store.sendMessage("Hello from test");
    await flushUi();

    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Hello from test");
    expect(wrapper.text()).toContain("Mocked SSE response");
  });

  it("finalizes run with steps and timing after stream completes", async () => {
    mockFetch.mockResolvedValueOnce(
      createSSEResponse([
        {
          event: "node_start",
          data: JSON.stringify({
            node: "planning",
            started_at: new Date().toISOString(),
          }),
        },
        {
          event: "node_end",
          data: JSON.stringify({
            node: "planning",
            budget_remaining: 48,
            elapsed_ms: 1200,
          }),
        },
        {
          event: "run_complete",
          data: JSON.stringify({
            report: {
              answer: "Done",
              citations: [],
              support: [],
              critiques: [],
              unverified_claims: [],
              budget_consumed: 2,
            },
            total_elapsed_ms: 5000,
          }),
        },
      ])
    );

    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useChatStore();

    await store.sendMessage("test query");
    await flushUi();

    // After finalization, the run is stored in the runs map
    // AND the live state persists for immediate rendering
    expect(store.budgetRemaining).toBe(48);

    // Live state should still be present (not cleared)
    expect(store.executionSteps.length).toBe(1);
    expect(store.executionSteps[0].label).toBe("Planning");
    expect(store.executionSteps[0].elapsed_ms).toBe(1200);

    // The run should also be in the runs map
    const userMsg = store.messages.find((m) => m.role === "user");
    expect(userMsg).toBeDefined();
    const run = store.getRunForMessage(userMsg!.id);
    expect(run).not.toBeNull();
    expect(run!.execution_steps.length).toBe(1);
    expect(run!.report).not.toBeNull();
    expect(run!.report!.answer).toBe("Done");
    expect(run!.total_elapsed_ms).toBe(5000);
  });
});

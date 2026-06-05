import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { nextTick } from "vue";
import { createRouter, createMemoryHistory, type Router } from "vue-router";

let router: Router;

// jsdom does not implement HTMLElement.scrollTo, but ChatView triggers
// NScrollbar.scrollTo from a watcher when conversations change.
// Provide a test shim to avoid unhandled rejections in unit tests.
Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  value: vi.fn(),
  writable: true,
  configurable: true,
});

beforeEach(async () => {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/conversation/new",
        name: "new-conversation",
        component: { template: "<div/>" },
      },
      {
        path: "/conversation/:id",
        name: "conversation",
        component: { template: "<div/>" },
        props: true,
      },
      { path: "/", redirect: "/conversation/new" },
    ],
  });
  router.push("/conversation/new");
  await router.isReady();
  mockFetch.mockClear();
});

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
      runs: [],
    })),
    getModels: vi.fn(async () => ({
      models: [],
      assignments: {
        intelligence: { endpoint: "", model: "" },
        task: { endpoint: "", model: "" },
      },
    })),
    setModels: vi.fn(async (assignments) => assignments),
    startRun: vi.fn(async () => ({
      run_id: "run-1",
      user_message_id: 1,
    })),
    streamUrl: vi.fn(
      () => "http://localhost:8000/api/conversations/conv-1/stream",
    ),
    generateTitle: vi.fn(async () => {}),
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

function mockStartRunAndStream(events: { event: string; data: string }[]) {
  // api.startRun is mocked separately (returns JSON), so only the SSE GET
  // call from connectStream hits fetch. Set up a single SSE response.
  mockFetch.mockResolvedValueOnce(createSSEResponse(events));
}

function runSnapshot(
  partial: Partial<Record<string, unknown>>,
): { event: string; data: string } {
  return {
    event: "run_snapshot",
    data: JSON.stringify({
      id: "run-1",
      conversation_id: "conv-1",
      user_message_id: 1,
      status: "running",
      budget_limit: 50,
      budget_consumed: 0,
      error: "",
      report: null,
      execution_steps: [],
      state_version: 1,
      started_at: new Date().toISOString(),
      completed_at: "",
      updated_at: new Date().toISOString(),
      ...partial,
    }),
  };
}

describe("ChatView", () => {
  it("renders chat messages using mocked SSE streaming", async () => {
    mockStartRunAndStream([
      runSnapshot({
        state_version: 2,
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "running",
            cost: 0,
            budget_remaining: 50,
            tool_call_count: 0,
            step_version: 1,
            has_detail: false,
          },
        ],
      }),
      runSnapshot({
        state_version: 3,
        status: "completed",
        budget_consumed: 21,
        total_elapsed_ms: 5000,
        report: {
          answer: "Mocked SSE response",
          citations: [],
          support: [],
          critiques: [],
          unverified_claims: [],
          budget_consumed: 21,
        },
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1200,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
        ],
      }),
    ]);

    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(ChatView, {
      global: { plugins: [pinia, router] },
    });
    const store = useChatStore();
    await store.sendMessage("Hello from test");
    await flushUi();

    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Hello from test");
    expect(wrapper.text()).toContain("Mocked SSE response");
  });

  it("finalizes run with steps and timing after stream completes", async () => {
    mockStartRunAndStream([
      runSnapshot({
        state_version: 2,
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "running",
            cost: 0,
            budget_remaining: 50,
            tool_call_count: 0,
            step_version: 1,
            has_detail: false,
          },
        ],
      }),
      runSnapshot({
        state_version: 3,
        status: "completed",
        budget_consumed: 2,
        total_elapsed_ms: 5000,
        report: {
          answer: "Done",
          citations: [],
          support: [],
          critiques: [],
          unverified_claims: [],
          budget_consumed: 2,
        },
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1200,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
        ],
      }),
    ]);

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
    expect(store.executionSteps[0]!.label).toBe("Planning");
    expect(store.executionSteps[0]!.elapsed_ms).toBe(1200);

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

  it("ignores stale out-of-order run snapshots by state_version", async () => {
    mockStartRunAndStream([
      runSnapshot({
        state_version: 2,
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
        ],
      }),
      runSnapshot({
        state_version: 4,
        status: "completed",
        budget_consumed: 10,
        total_elapsed_ms: 8000,
        report: {
          answer: "Final answer",
          citations: [],
          support: [],
          critiques: [],
          unverified_claims: [],
          budget_consumed: 10,
        },
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
          {
            id: "2",
            node: "report_generation",
            label: "Generating Report",
            status: "completed",
            cost: 3,
            budget_remaining: 40,
            elapsed_ms: 2000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
        ],
      }),
      // stale snapshot should be ignored
      runSnapshot({
        state_version: 3,
        status: "running",
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
          {
            id: "2",
            node: "report_generation",
            label: "Generating Report",
            status: "running",
            cost: 0,
            budget_remaining: 40,
            tool_call_count: 0,
            step_version: 1,
            has_detail: false,
          },
        ],
      }),
    ]);

    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useChatStore();

    await store.sendMessage("state version ordering");
    await flushUi();

    // Both steps must be in the finalized run
    const userMsg = store.messages.find((m) => m.role === "user");
    const run = store.getRunForMessage(userMsg!.id);
    expect(run).not.toBeNull();
    expect(run!.execution_steps.length).toBe(2);
    expect(run!.execution_steps[0]!.label).toBe("Planning");
    expect(run!.execution_steps[1]!.label).toBe("Generating Report");
    expect(run!.status).toBe("completed");
    expect(run!.report).not.toBeNull();
    expect(run!.report!.answer).toBe("Final answer");
  });

  it("renders steps via RunArtifacts after finalizeRun, not just live state", async () => {
    // This test verifies the rendered DOM contains steps after the workflow completes.
    // The bug symptom: steps disappear when finalizeRun switches rendering from
    // live template to RunArtifacts component.
    mockStartRunAndStream([
      runSnapshot({
        state_version: 2,
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
          {
            id: "2",
            node: "report_generation",
            label: "Generating Report",
            status: "running",
            cost: 0,
            budget_remaining: 48,
            tool_call_count: 0,
            step_version: 1,
            has_detail: false,
          },
        ],
      }),
      runSnapshot({
        state_version: 3,
        status: "completed",
        budget_consumed: 10,
        total_elapsed_ms: 8000,
        report: {
          answer: "Rendered answer",
          citations: [],
          support: [],
          critiques: [],
          unverified_claims: [],
          budget_consumed: 10,
        },
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
          {
            id: "2",
            node: "report_generation",
            label: "Generating Report",
            status: "completed",
            cost: 3,
            budget_remaining: 40,
            elapsed_ms: 2000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
        ],
      }),
    ]);

    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(ChatView, {
      global: { plugins: [pinia, router] },
    });
    const store = useChatStore();
    await store.sendMessage("render test");
    await flushUi();

    // Verify the run was finalized and stored
    const userMsg = store.messages.find((m) => m.role === "user");
    const run = store.getRunForMessage(userMsg!.id);
    expect(run).not.toBeNull();
    expect(run!.execution_steps.length).toBe(2);

    const text = wrapper.text();
    // Steps must be visible in the rendered output
    expect(text).toContain("Planning");
    expect(text).toContain("Generating Report");
    // Report answer must be visible
    expect(text).toContain("Rendered answer");
    // Total elapsed must be visible
    expect(text).toContain("Total:");
  });

  it("steps persist in DOM after assistant message is added", async () => {
    // After streamMessage returns, sendMessage pushes an assistant message.
    // This changes the messages array and re-renders. Steps must survive this.
    mockStartRunAndStream([
      runSnapshot({
        state_version: 2,
        status: "completed",
        budget_consumed: 5,
        total_elapsed_ms: 3000,
        report: {
          answer: "Persisted answer",
          citations: [],
          support: [],
          critiques: [],
          unverified_claims: [],
          budget_consumed: 5,
        },
        execution_steps: [
          {
            id: "1",
            node: "planning",
            label: "Planning",
            status: "completed",
            cost: 2,
            budget_remaining: 48,
            elapsed_ms: 1000,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
        ],
      }),
    ]);

    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(ChatView, {
      global: { plugins: [pinia, router] },
    });
    const store = useChatStore();
    await store.sendMessage("persistence test");

    // Wait for multiple flush cycles to ensure Vue has processed all updates
    for (let i = 0; i < 5; i++) {
      await flushUi();
    }

    const text = wrapper.text();

    // User message must be present
    expect(text).toContain("persistence test");

    // Steps must still be visible after assistant message was added
    expect(text).toContain("Planning");

    // Report must be visible (from RunArtifacts)
    expect(text).toContain("Persisted answer");

    // Assistant message must also be present
    expect(text).toContain("Persisted answer");

    // Total elapsed
    expect(text).toContain("Total:");
  });

  it("stores stopped run status and zero-remaining budget limit correctly", async () => {
    mockStartRunAndStream([
      runSnapshot({
        state_version: 2,
        status: "stopped",
        budget_limit: 35,
        budget_consumed: 35,
        total_elapsed_ms: 2500,
        execution_steps: [
          {
            id: "1",
            node: "verification",
            label: "Verifying",
            status: "stopped",
            cost: 0,
            budget_remaining: 0,
            elapsed_ms: 1500,
            tool_call_count: 0,
            step_version: 2,
            has_detail: false,
          },
        ],
      }),
    ]);

    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useChatStore();

    await store.sendMessage("stop status test");
    await flushUi();

    const userMsg = store.messages.find((m) => m.role === "user");
    expect(userMsg).toBeDefined();
    const run = store.getRunForMessage(userMsg!.id);
    expect(run).not.toBeNull();
    expect(run!.status).toBe("stopped");
    expect(run!.budget_consumed).toBe(35);
    expect(run!.budget_limit).toBe(35);
  });

  it("ignores stale selectConversation responses when switching quickly", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useChatStore();

    const getConversationMock = api.getConversation as unknown as ReturnType<
      typeof vi.fn
    >;

    let resolveFirst: (value: any) => void = () => {};
    let resolveSecond: (value: any) => void = () => {};

    getConversationMock
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve;
          }),
      );

    const p1 = store.selectConversation("conv-1");
    const p2 = store.selectConversation("conv-2");

    resolveSecond({
      id: "conv-2",
      title: "Second",
      created_at: new Date().toISOString(),
      messages: [
        {
          id: 2,
          role: "user",
          content: "newer conversation",
          created_at: new Date().toISOString(),
        },
      ],
      runs: [],
    });
    await p2;

    resolveFirst({
      id: "conv-1",
      title: "First",
      created_at: new Date().toISOString(),
      messages: [
        {
          id: 1,
          role: "user",
          content: "stale conversation",
          created_at: new Date().toISOString(),
        },
      ],
      runs: [],
    });
    await p1;

    expect(store.currentConversationId).toBe("conv-2");
    expect(store.messages).toHaveLength(1);
    expect(store.messages[0]!.content).toBe("newer conversation");

    getConversationMock.mockImplementation(async () => ({
      id: "conv-1",
      title: "New Conversation",
      created_at: new Date().toISOString(),
      messages: [],
      runs: [],
    }));
  });
});

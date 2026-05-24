import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

vi.mock("../../api/client", () => ({
  api: {
    createSession: vi.fn(async () => ({
      id: "session-1",
      title: "New Session",
      created_at: new Date().toISOString(),
    })),
    listSessions: vi.fn(async () => []),
    getSession: vi.fn(async () => ({
      id: "session-1",
      title: "New Session",
      created_at: new Date().toISOString(),
      messages: [],
    })),
    sendMessage: vi.fn(async () => ({
      role: "assistant",
      content: "Mocked response",
      created_at: new Date().toISOString(),
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
  it("renders chat messages using mocked API", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(ChatView, {
      global: { plugins: [pinia] },
    });

    const store = useChatStore();
    await store.sendMessage("Hello from test");
    await flushUi();

    expect(api.createSession).toHaveBeenCalledTimes(1);
    expect(api.sendMessage).toHaveBeenCalledWith("session-1", "Hello from test");
    expect(wrapper.text()).toContain("Hello from test");
    expect(wrapper.text()).toContain("Mocked response");
  });
});

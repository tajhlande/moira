import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import StructuredOutputRenderer from "../StructuredOutputRenderer.vue";

function render(so: Record<string, unknown>) {
  return mount(StructuredOutputRenderer, {
    props: { so },
    global: {
      stubs: {},
    },
  });
}

describe("StructuredOutputRenderer", () => {
  it("renders text fields", () => {
    const wrapper = render({
      user_goal: "Learn about Python",
      topic: "programming",
    });
    expect(wrapper.text()).toContain("User Goal");
    expect(wrapper.text()).toContain("Learn about Python");
    expect(wrapper.text()).toContain("Topic");
    expect(wrapper.text()).toContain("programming");
  });

  it("renders pill-list fields", () => {
    const wrapper = render({
      entities: ["Python", "LLM"],
      concepts: ["programming"],
    });
    expect(wrapper.text()).toContain("Entities");
    expect(wrapper.text()).toContain("Python");
    expect(wrapper.text()).toContain("LLM");
    expect(wrapper.text()).toContain("Concepts");
    expect(wrapper.text()).toContain("programming");
    expect(wrapper.findAll(".tool-tag.default").length).toBeGreaterThanOrEqual(
      3,
    );
  });

  it("renders empty pill-list with None", () => {
    const wrapper = render({ entities: [] });
    expect(wrapper.text()).toContain("None");
  });

  it("renders badge fields", () => {
    const wrapper = render({ goal_met: true, route: "accept" });
    expect(wrapper.text()).toContain("Goal Met");
    expect(wrapper.text()).toContain("Yes");
    expect(wrapper.text()).toContain("Route");
    expect(wrapper.text()).toContain("accept");
    const badges = wrapper.findAll(".so-badge");
    expect(badges.length).toBe(2);
    expect(badges[0].classes()).toContain("success");
    expect(badges[1].classes()).toContain("success");
  });

  it("renders warning badge for retry routes", () => {
    const wrapper = render({ route: "retry" });
    const badge = wrapper.find(".so-badge");
    expect(badge.classes()).toContain("warning");
  });

  it("renders fact-cards for unknown_facts", () => {
    const wrapper = render({
      unknown_facts: [
        {
          id: "f1",
          subject: "Python",
          fact_needed: "What is it?",
          status: "unknown",
        },
        {
          id: "f2",
          subject: "LLM",
          fact_needed: "How does it work?",
          claim: "It generates text",
        },
      ],
    });
    expect(wrapper.text()).toContain("Unknown Facts");
    expect(wrapper.text()).toContain("Python");
    expect(wrapper.text()).toContain("What is it?");
    expect(wrapper.text()).toContain("LLM");
    expect(wrapper.text()).toContain("Claim");
    expect(wrapper.text()).toContain("It generates text");
    expect(wrapper.findAll(".so-card").length).toBe(2);
  });

  it("renders object-list for calls", () => {
    const wrapper = render({
      calls: [
        {
          tool: "web_search",
          args: { q: "Python" },
          target_fact_ids: ["f1"],
          rationale: "Search for info",
        },
      ],
    });
    expect(wrapper.text()).toContain("Planned Calls");
    expect(wrapper.text()).toContain("web_search");
    expect(wrapper.text()).toContain("Search for info");
    expect(wrapper.find(".so-kv-list").exists()).toBe(true);
    expect(wrapper.findAll(".tool-tag.default").length).toBeGreaterThanOrEqual(
      1,
    );
  });

  it("renders object-list for conclusions", () => {
    const wrapper = render({
      conclusions: [
        {
          id: "c1",
          conclusion: "Python is a language",
          supporting_fact_ids: ["f1"],
          reasoning: "Based on evidence",
          status: "verified",
        },
      ],
    });
    expect(wrapper.text()).toContain("Conclusions");
    expect(wrapper.text()).toContain("Python is a language");
    expect(wrapper.text()).toContain("Based on evidence");
  });

  it("renders object-list for fact_results", () => {
    const wrapper = render({
      fact_results: [
        { fact_id: "f1", result: "verified", evidence: "Found on python.org" },
      ],
    });
    expect(wrapper.text()).toContain("Fact Verification");
    expect(wrapper.text()).toContain("f1");
    expect(wrapper.text()).toContain("verified");
    expect(wrapper.text()).toContain("Found on python.org");
  });

  it("renders string-list for new_unknown_facts", () => {
    const wrapper = render({
      new_unknown_facts: ["Who created Python?"],
    });
    expect(wrapper.text()).toContain("New Unknown Facts");
    expect(wrapper.text()).toContain("Who created Python?");
  });

  it("renders fallback for unknown string field", () => {
    const wrapper = render({ custom_field: "hello" });
    expect(wrapper.text()).toContain("Custom Field");
    expect(wrapper.text()).toContain("hello");
  });

  it("renders fallback for unknown boolean field", () => {
    const wrapper = render({ custom_flag: false });
    expect(wrapper.text()).toContain("Custom Flag");
    expect(wrapper.text()).toContain("No");
    const badge = wrapper.find(".so-badge");
    expect(badge.classes()).toContain("error");
  });

  it("renders fallback for unknown object array field", () => {
    const wrapper = render({
      custom_items: [
        { name: "item1", score: 10 },
        { name: "item2", score: 20 },
      ],
    });
    expect(wrapper.text()).toContain("Custom Items");
    expect(wrapper.text()).toContain("item1");
    expect(wrapper.text()).toContain("item2");
    expect(wrapper.findAll(".so-card").length).toBe(2);
  });

  it("renders fallback for unknown string array field", () => {
    const wrapper = render({ tags: ["a", "b", "c"] });
    expect(wrapper.text()).toContain("Tags");
    expect(wrapper.text()).toContain("a");
    expect(wrapper.text()).toContain("b");
    expect(wrapper.text()).toContain("c");
  });

  it("renders empty object-list with None", () => {
    const wrapper = render({ calls: [] });
    expect(wrapper.text()).toContain("None");
  });

  it("renders full decomposition output", () => {
    const wrapper = render({
      user_goal: "Learn about Python",
      topic: "programming languages",
      entities: ["Python", "Guido van Rossum"],
      concepts: ["interpreted language", "high-level language"],
      unknown_facts: [
        { id: "f1", subject: "Python", fact_needed: "When was it created?" },
        {
          id: "f2",
          subject: "Guido van Rossum",
          fact_needed: "What is his role?",
        },
      ],
    });
    expect(wrapper.text()).toContain("User Goal");
    expect(wrapper.text()).toContain("Learn about Python");
    expect(wrapper.text()).toContain("Topic");
    expect(wrapper.text()).toContain("programming languages");
    expect(wrapper.text()).toContain("Python");
    expect(wrapper.text()).toContain("Guido van Rossum");
    expect(wrapper.text()).toContain("interpreted language");
    expect(wrapper.text()).toContain("When was it created?");
    expect(wrapper.findAll(".so-card").length).toBe(2);
  });
});

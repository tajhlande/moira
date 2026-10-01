import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SourceDepthBadge from "../SourceDepthBadge.vue";

describe("SourceDepthBadge", () => {
  it("renders a badge for each material class", () => {
    const cases: Array<[string, string, string]> = [
      ["snippet", "snippet", "snippet"],
      ["page", "page", "page"],
      ["clipped", "clipped", "page"],
      ["full", "full page", "full"],
      ["summary", "summary", "summary"],
    ];
    for (const [depth, label, kind] of cases) {
      const wrapper = mount(SourceDepthBadge, { props: { depth } });
      const badge = wrapper.find(".source-depth-badge");
      expect(badge.exists()).toBe(true);
      expect(badge.classes()).toContain(kind);
      expect(badge.text()).toBe(label);
      // The tooltip carries the upgrade-path explanation (UI parity rule).
      expect(badge.attributes("title")).toBeTruthy();
    }
  });

  it("renders nothing for missing depth (legacy citation = unknown)", () => {
    for (const depth of [undefined, null, "", "bogus"]) {
      const wrapper = mount(SourceDepthBadge, {
        props: { depth: depth as string | null | undefined },
      });
      expect(wrapper.find(".source-depth-badge").exists()).toBe(false);
    }
  });
});

import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { api, type ToolInfo, type ToolGroupInfo } from "../api/client";

export interface ToolParameter {
  name: string;
  type: string;
  required: boolean;
  description: string;
  default?: string | number | boolean;
}

export interface ToolGroup {
  name: string;
  displayName: string;
}

export interface ToolDefinition {
  name: string;
  description: string;
  groupName: string;
  groupDisplayName: string;
  isDefault: boolean;
  enabled: boolean;
  builtIn: boolean;
  implementation: string;
  argumentSchema: Record<string, unknown>;
  config: Record<string, unknown>;
  parameters: ToolParameter[];
}

function extractParameters(schema: Record<string, unknown>): ToolParameter[] {
  const props = schema?.properties as Record<string, Record<string, unknown>> | undefined;
  if (!props) return [];
  const required = (schema?.required as string[]) || [];
  return Object.entries(props).map(([name, prop]) => ({
    name,
    type: (prop.type as string) || "string",
    required: required.includes(name),
    description: (prop.description as string) || "",
    default: prop.default as string | number | boolean | undefined,
  }));
}

function apiToolToStore(
  info: ToolInfo,
  groupLookup: Map<string, ToolGroupInfo>,
): ToolDefinition {
  const group = groupLookup.get(info.group_name);
  return {
    name: info.name,
    description: info.description,
    groupName: info.group_name || "ungrouped",
    groupDisplayName: group?.display_name || info.group_name || "Ungrouped",
    isDefault: info.is_default,
    enabled: info.enabled,
    builtIn: info.built_in,
    implementation: info.implementation,
    argumentSchema: info.argument_schema,
    config: info.config,
    parameters: extractParameters(info.argument_schema),
  };
}

export const useToolsStore = defineStore("tools", () => {
  const tools = ref<ToolDefinition[]>([]);
  const groups = ref<Map<string, ToolDefinition[]>>(new Map());
  const selectedToolName = ref<string | null>(null);
  const loaded = ref(false);

  const selectedTool = computed(() => {
    if (!selectedToolName.value) return null;
    return tools.value.find((t) => t.name === selectedToolName.value) || null;
  });

  const toolCount = computed(() => tools.value.length);
  const groupCount = computed(() => groups.value.size);

  async function fetchTools() {
    if (loaded.value) return;
    try {
      const resp = await api.getTools();
      const groupLookup = new Map<string, ToolGroupInfo>();
      for (const g of resp.groups) {
        groupLookup.set(g.name, g);
      }
      tools.value = resp.tools.map((t) => apiToolToStore(t, groupLookup));

      const map = new Map<string, ToolDefinition[]>();
      for (const tool of tools.value) {
        const list = map.get(tool.groupName) || [];
        list.push(tool);
        map.set(tool.groupName, list);
      }
      groups.value = map;
      loaded.value = true;
    } catch {
      // Backend not available — store stays empty
    }
  }

  function selectTool(name: string) {
    selectedToolName.value = name;
  }

  function clearSelection() {
    selectedToolName.value = null;
  }

  async function patchTool(name: string, fields: Record<string, unknown>) {
    const updated = await api.patchTool(name, fields);
    const tool = tools.value.find((t) => t.name === name);
    if (tool) {
      const groupLookup = new Map<string, ToolGroupInfo>();
      // Re-derive the group display name from the current groups
      for (const [g, list] of groups.value) {
        if (list.length > 0) groupLookup.set(g, { name: g, display_name: list[0].groupDisplayName });
      }
      const patched = apiToolToStore(updated, groupLookup);
      Object.assign(tool, patched);
    }
  }

  async function toggleEnabled(name: string, enabled: boolean) {
    await patchTool(name, { enabled });
  }

  async function toggleDefault(name: string, isDefault: boolean) {
    await patchTool(name, { is_default: isDefault });
  }

  const defaultToolNames = computed(() =>
    tools.value.filter((t) => t.isDefault).map((t) => t.name),
  );

  return {
    tools,
    groups,
    selectedToolName,
    selectedTool,
    toolCount,
    groupCount,
    defaultToolNames,
    fetchTools,
    selectTool,
    clearSelection,
    toggleEnabled,
    toggleDefault,
    patchTool,
  };
});

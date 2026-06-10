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
  originalDescription: string;
}

function extractParameters(schema: Record<string, unknown>): ToolParameter[] {
  const props = schema?.properties as
    | Record<string, Record<string, unknown>>
    | undefined;
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
    originalDescription: info.original_description || "",
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
    await loadTools();
  }

  async function refreshTools() {
    loaded.value = false;
    await loadTools();
  }

  function _rebuildGroups() {
    const map = new Map<string, ToolDefinition[]>();
    for (const tool of tools.value) {
      const list = map.get(tool.groupName) || [];
      list.push(tool);
      map.set(tool.groupName, list);
    }
    groups.value = map;
  }

  async function loadTools() {
    try {
      const resp = await api.getTools();
      const groupLookup = new Map<string, ToolGroupInfo>();
      for (const g of resp.groups) {
        groupLookup.set(g.name, g);
      }
      tools.value = resp.tools.map((t) => apiToolToStore(t, groupLookup));
      _rebuildGroups();
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
      for (const [g, list] of groups.value) {
        if (list.length > 0)
          groupLookup.set(g, {
            name: g,
            display_name: list[0].groupDisplayName,
          });
      }
      Object.assign(tool, apiToolToStore(updated, groupLookup));
      _rebuildGroups();
    }
  }

  async function toggleEnabled(name: string, enabled: boolean) {
    await patchTool(name, { enabled });
  }

  async function bulkToggleEnabled(names: string[], enabled: boolean) {
    const updates = names.map((name) => ({ name, enabled }));
    const resp = await api.bulkPatchTools(updates);
    const groupLookup = new Map<string, ToolGroupInfo>();
    for (const [g, list] of groups.value) {
      if (list.length > 0)
        groupLookup.set(g, {
          name: g,
          display_name: list[0].groupDisplayName,
        });
    }
    for (const info of resp.updated) {
      const tool = tools.value.find((t) => t.name === info.name);
      if (tool) Object.assign(tool, apiToolToStore(info, groupLookup));
    }
    _rebuildGroups();
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
    refreshTools,
    selectTool,
    clearSelection,
    toggleEnabled,
    bulkToggleEnabled,
    toggleDefault,
    patchTool,
  };
});

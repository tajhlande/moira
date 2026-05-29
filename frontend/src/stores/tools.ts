import { defineStore } from "pinia";
import { ref, computed } from "vue";

export interface ToolParameter {
  name: string;
  type: string;
  required: boolean;
  description: string;
  default?: string | number | boolean;
}

export interface ToolDefinition {
  name: string;
  description: string;
  group: string;
  parameters: ToolParameter[];
  builtIn: boolean;
}

const STANDARD_TOOLS: ToolDefinition[] = [
  {
    name: "user_question",
    description:
      "Ask the user a follow-up question to clarify the research question or guide the answer. Presents multiple-choice options plus free-text response.",
    group: "Standard",
    builtIn: true,
    parameters: [
      {
        name: "question",
        type: "string",
        required: true,
        description: "The question to ask the user",
      },
      {
        name: "options",
        type: "string[]",
        required: true,
        description: "A/B/C/D multiple choice options for the user to select from",
      },
    ],
  },
  {
    name: "web_search",
    description:
      "Search the web for information. Returns a sorted list of URLs and relevance scores.",
    group: "Standard",
    builtIn: true,
    parameters: [
      {
        name: "query",
        type: "string",
        required: true,
        description: "The search query",
      },
      {
        name: "domains",
        type: "string[]",
        required: false,
        description: "Optional list of web domains to restrict the search to",
      },
      {
        name: "max_results",
        type: "number",
        required: false,
        description: "Maximum number of search results to return",
        default: 5,
      },
    ],
  },
  {
    name: "url_content",
    description:
      "Retrieve the content of a web page given its URL. Can return full HTML or text-only content.",
    group: "Standard",
    builtIn: true,
    parameters: [
      {
        name: "url",
        type: "string",
        required: true,
        description: "The URL to retrieve content from",
      },
      {
        name: "text_only",
        type: "boolean",
        required: false,
        description: "Return only the text content, stripping HTML",
        default: true,
      },
      {
        name: "xpath",
        type: "string",
        required: false,
        description: "XPath expression to return only a subset of the page content",
      },
      {
        name: "summarize",
        type: "boolean",
        required: false,
        description: "Summarize the content via a sub-agent",
        default: false,
      },
    ],
  },
  {
    name: "calculator",
    description:
      "Evaluate a mathematical expression. Supports arithmetic operators, standard math functions, and trigonometric functions.",
    group: "Standard",
    builtIn: true,
    parameters: [
      {
        name: "expression",
        type: "string",
        required: true,
        description:
          "Mathematical expression in infix notation (e.g. sqrt(2) + 3^4)",
      },
    ],
  },
];

export const useToolsStore = defineStore("tools", () => {
  const tools = ref<ToolDefinition[]>([...STANDARD_TOOLS]);
  const selectedToolName = ref<string | null>(null);

  const groups = computed(() => {
    const map = new Map<string, ToolDefinition[]>();
    for (const tool of tools.value) {
      const list = map.get(tool.group) || [];
      list.push(tool);
      map.set(tool.group, list);
    }
    return map;
  });

  const selectedTool = computed(() => {
    if (!selectedToolName.value) return null;
    return tools.value.find((t) => t.name === selectedToolName.value) || null;
  });

  const toolCount = computed(() => tools.value.length);
  const groupCount = computed(() => groups.value.size);

  function selectTool(name: string) {
    selectedToolName.value = name;
  }

  function clearSelection() {
    selectedToolName.value = null;
  }

  return {
    tools,
    selectedToolName,
    groups,
    selectedTool,
    toolCount,
    groupCount,
    selectTool,
    clearSelection,
  };
});

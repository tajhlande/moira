# Phase 3 Preparation: Multi-View UI and Standard Tools

## Status: In Progress

## Goal

Build the UI infrastructure and standard tool catalog before implementing the
Phase 3 backend changes (REST tools, MCP, semantic discovery refinement). This
establishes the navigation framework and tool catalog UI so backend work in
Phase 3 has a place to surface.

## What Was Built

### Navigation Framework

- **NavTray**: Bottom-of-sidebar navigation with icon buttons for Conversations,
  Tools, and Settings modes. Always visible regardless of current view.
  Uses route meta (`sidebar` field) to determine active mode.

- **Dynamic sidebar**: `AppLayout` switches sidebar content based on route meta:
  - `conversations` → `ConversationSidebar` (extracted from old AppLayout)
  - `tools` → `ToolSidebar` (tree view with accordion groups)
  - `settings` → placeholder sidebar

- **Route structure**:
  ```
  /conversation/new     → ChatView          (sidebar: conversations)
  /conversation/:id     → ChatView          (sidebar: conversations)
  /tools                → ToolCatalogView   (sidebar: tools)
  /tools/new            → ToolWizardView    (sidebar: tools)
  /tools/:name          → ToolDetailView    (sidebar: tools)
  /settings             → SettingsView      (sidebar: settings)
  ```

### Tool Catalog

- **Tools store** (`stores/tools.ts`): Pinia store with tool definitions,
  grouped by category. Ships with 4 standard tools:
  - `user_question` — ask the user a follow-up question with multiple-choice
  - `web_search` — search the web with optional domain filter
  - `url_content` — retrieve and optionally summarize web page content
  - `calculator` — evaluate mathematical expressions safely

- **ToolSidebar**: Tree view with collapsible groups. "Add Tool" button at top
  routes to the tool wizard. Clicking a tool navigates to its detail view.

- **ToolCatalogView**: Right-pane view showing aggregate summary (tool count,
  group count, session usage placeholder) and card-style listing of all tools
  organized by group. Cards link to tool detail.

- **ToolDetailView**: Shows tool description, required and optional parameters
  with types, descriptions, and defaults.

- **ToolWizardView**: Placeholder for future tool discovery/configuration wizard.

### Settings

- **SettingsView**: Placeholder for system info and configuration.

## Files Created

| File | Purpose |
|------|---------|
| `frontend/src/stores/tools.ts` | Tool catalog store with standard tools |
| `frontend/src/components/NavTray.vue` | Bottom navigation tray |
| `frontend/src/components/ConversationSidebar.vue` | Conversation list sidebar (extracted) |
| `frontend/src/components/ToolSidebar.vue` | Tool tree sidebar with groups |
| `frontend/src/components/ToolCatalogView.vue` | Tool catalog right pane |
| `frontend/src/components/ToolDetailView.vue` | Individual tool detail view |
| `frontend/src/components/ToolWizardView.vue` | Add-tool wizard placeholder |
| `frontend/src/components/SettingsView.vue` | Settings placeholder |

## Files Modified

| File | Change |
|------|--------|
| `frontend/src/components/AppLayout.vue` | Dynamic sidebar based on route meta, NavTray, extracted conversation sidebar |
| `frontend/src/router/index.ts` | Added /tools/*, /settings routes with sidebar meta |

## Design Decisions

- **Tool persistence**: All tools (built-in and user-added) are stored in the database.
  Built-in tools have an `is_builtin` boolean flag. The API rejects create/delete
  operations on builtin tools. Query endpoints return all tools uniformly, so built-in
  tools appear alongside user-added tools in listing and discovery queries.

## Next Steps (Phase 3 Backend)

1. Backend `GET /api/tools` endpoint to serve registered tools
2. Connect frontend tools store to backend API instead of hardcoded data
3. MCP client integration for dynamic tool discovery
4. Tool executor hardening (retry, timeout, error capture)
5. Two-pass tool discovery (see `two-pass-discovery.md`)
6. Tool call visibility in research workflow UI

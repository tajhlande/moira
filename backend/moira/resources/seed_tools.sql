-- Seed data for built-in tools distributed with MOiRA.
-- These are applied by service_setup.py at startup (idempotent).
-- Only the `enabled` column is user-modifiable for built-in tools.

INSERT OR IGNORE INTO tool_groups (name, display_name) VALUES ('standard', 'Standard');

INSERT OR IGNORE INTO tools (name, description, argument_schema, config, tags, reliability, is_default, enabled, built_in, implementation, group_name)
VALUES (
  'user_question',
  'Ask the user a follow-up question to clarify the research question or guide the answer. Presents multiple-choice options plus a free-text response.',
  '{"type":"object","properties":{"question":{"type":"string","description":"The question to ask the user"},"options":{"type":"array","items":{"type":"string"},"description":"A/B/C/D multiple choice options"}},"required":["question","options"]}',
  '{}',
  '[]',
  'unknown',
  1, 1, 1,
  'moira.tools.builtin.user_question.UserQuestionTool',
  'standard'
);

INSERT OR IGNORE INTO tools (name, description, argument_schema, config, tags, reliability, is_default, enabled, built_in, implementation, group_name)
VALUES (
  'web_search',
  'Search the web for information about specific topics. Returns a sorted list of URLs and relevance scores.',
  '{"type":"object","properties":{"query":{"type":"string","description":"The search query"},"domains":{"type":"array","items":{"type":"string"},"description":"Optional list of web domains to restrict search to"},"max_results":{"type":"integer","description":"Maximum number of search results to return","default":5}},"required":["query"]}',
  '{}',
  '[]',
  'unknown',
  1, 1, 1,
  '',
  'standard'
);

INSERT OR IGNORE INTO tools (name, description, argument_schema, config, tags, reliability, is_default, enabled, built_in, implementation, group_name)
VALUES (
  'url_content',
  'Retrieve the content of a web page given its URL. Can return full HTML or text-only content.',
  '{"type":"object","properties":{"url":{"type":"string","description":"The URL to retrieve content from"},"text_only":{"type":"boolean","description":"Return only text content, stripping HTML","default":true},"xpath":{"type":"string","description":"XPath to return only a subset of content"},"summarize":{"type":"boolean","description":"Summarize the content via a sub-agent","default":false}},"required":["url"]}',
  '{}',
  '[]',
  'unknown',
  1, 1, 1,
  '',
  'standard'
);

INSERT OR IGNORE INTO tools (name, description, argument_schema, config, tags, reliability, is_default, enabled, built_in, implementation, group_name)
VALUES (
  'calculator',
  'Evaluate a mathematical expression. Supports arithmetic operators, standard math functions, and trigonometric functions.',
  '{"type":"object","properties":{"expression":{"type":"string","description":"Mathematical expression in infix notation (e.g. sqrt(2) + 3^4)"}},"required":["expression"]}',
  '{}',
  '[]',
  'unknown',
  1, 1, 1,
  'moira.tools.builtin.calculator.CalculatorTool',
  'standard'
);

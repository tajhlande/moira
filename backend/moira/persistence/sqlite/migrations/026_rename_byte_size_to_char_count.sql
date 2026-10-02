-- Rename source_contents.byte_size -> char_count (amended plan Step 2
-- follow-up): the column has always stored len(content) — a code-point
-- count — never UTF-8 bytes. The old name understated nothing numerically
-- but lied about units (true byte size can be up to 4x larger for CJK or
-- emoji). All limits (max_body_chars, the 5K serving window, the retention
-- cap) are expressed in code points, which is the unit that matches
-- context-window budgeting, so the values are correct as-is; only the
-- label changes. No data is rewritten.
ALTER TABLE source_contents RENAME COLUMN byte_size TO char_count;

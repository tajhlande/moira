# LaTeX Math Rendering

## Requirement

Add LaTeX formula rendering to the markdown pipeline so that research reports can include mathematical notation.

### Inline Formulas

Single dollar sign delimiters: `$E = mc^2$` renders as inline math.

### Block Formulas

Double dollar sign delimiters:

```
$$
\sum_{i=1}^{n} \frac{1}{n}
$$
```

renders as a centered block equation.

### Disambiguation

Must distinguish LaTeX math from other uses of dollar signs:

- **Currency**: `$5.00`, `$1,000`, `costs $50 per unit` — these should NOT be treated as math.
- **Bash variables**: `$HOME`, `$PATH`, `$1` — typically inside code blocks (already handled by shiki), but could appear in inline code or plain text.
- **Escaped dollars**: `\$100` — the backslash-escaped dollar should render as a literal `$`.

Possible disambiguation heuristics:

- Require the content between `$...$` to contain at least one LaTeX command (`\frac`, `\sum`, `\sqrt`, etc.) or math operator (`^`, `_`, `\`, `{`). A bare number like `$5.00` or `$100` would not qualify.
- Require the opening `$` to NOT be preceded by a letter or digit (so `cost$5` is not math) and the closing `$` to NOT be followed by a letter or digit.
- Code blocks (triple backtick) and inline code (single backtick) are already handled — marked extracts them before extensions run, so their contents are never seen by the math renderer.

## Technical Approach

- Library: **KaTeX** (client-side, fast, lightweight).
- Integration: `marked-katex-extension` or a custom `marked` extension that runs before the main parse step.
- Files to modify: `frontend/src/markdown.ts` (add extension to the `Marked` instance).
- CSS: KaTeX ships its own stylesheet — import it in the markdown content component or the global styles.
- No backend changes needed — math is rendered entirely client-side from the markdown content.

## Open Questions

- Should block equations get a distinct visual treatment (centered, slightly larger, background)?
- Should we add a copy-source button on block equations (similar to code blocks)?
- Do we need to support `\begin{equation}...\end{equation}` environments, or only `$...$` / `$$...$$`?

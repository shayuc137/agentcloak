# Snapshot model

agentcloak represents web pages as indented accessibility trees where every interactive element has a stable `[N]` reference. Agents act on those numbers instead of CSS selectors. This page explains where the tree comes from, what gets pruned, and how progressive loading keeps even huge pages addressable inside an agent's context window.

## From Chrome AX tree to indented text

When you run `cloak snapshot`, the daemon pulls the full Accessibility tree from Chromium via CDP (`Accessibility.getFullAXTree`), then walks it into an indented text tree. The CLI prepends a header line with title / URL / node counts / `seq` so the agent can orient itself in a single glance:

```text
# Shop home | https://shop.example/ | 23 nodes (5 interactive) | seq=4
navigation "Main Nav"
  [1] link "Home"
  [2] link "Shop"
  [3] textbox "Search" value="shoes" focused
main "Content"
  [4] link "Item 1 - $29.99" href="/items/1"
  [5] button "Add to cart"
```

Each body line is `<indent><role> "<name>" <attributes>`. Interactive nodes get an `[N]` index assigned in document order; that index is the action target (positional `cloak click 5` or via `--index 5`) and is only valid until the page changes.

The shared builder lives in `src/agentcloak/browser/_snapshot_builder.py` — both `CloakBrowser` and `RemoteBridge` backends call it with the raw CDP nodes, so the tree format is identical regardless of backend.

## Snapshot modes

`cloak snapshot --mode <mode>` selects the representation:

| Mode | What you get | When to use |
|------|-------------|------------|
| `compact` (default) | Interactive + landmark nodes only; `generic`/`group` folded | Token-tight interaction loops |
| `accessible` | Full a11y tree with all `[N]` refs | First look, complex layouts |
| `content` | Pure visible text, no roles or refs | Article extraction, summarization |
| `dom` | Raw outer HTML | When ARIA hides what you need (rare) |

`compact` is the default since v0.2.0 — agents almost always want interactive elements + structural landmarks (`navigation`, `main`, `form`, `dialog`...) without the anonymous wrapper `<div>`s. Reach for `accessible` only when the default omitted context you need.

## ARIA state extraction

Inputs and toggles surface their live state directly in the tree:

```
[3] textbox "Search" value="shoes" focused
[7] checkbox "Remember me" checked
[8] button "Submit" disabled
[12] combobox "Country" expanded haspopup=listbox
[15] slider "Volume" valuenow=70 valuemin=0 valuemax=100
```

The builder picks up these boolean ARIA properties: `checked`, `disabled`, `expanded`, `selected`, `pressed`, `invalid`, `required`, `focused`, `hidden`. Value attributes (`value`, `valuetext`, `valuemin`, `valuemax`, `valuenow`, `level`, `haspopup`, `autocomplete`) appear when present.

Password fields are detected via `autocomplete="current-password"` / `new-password` and rendered as `value="••••"` so agents can confirm they typed something without leaking the secret.

## Link href extraction

Link nodes include the `href` directly in the tree, so the agent can resolve destinations without an extra `evaluate()` call:

```
[4] link "Documentation" href="/docs/"
[5] link "GitHub" href="https://github.com/cloak-hq/agentcloak"
```

## Progressive loading

Big pages can produce thousands of nodes. The daemon caches every full snapshot and exposes three flags to slice it without re-querying the browser:

| Flag | Effect |
|------|-------|
| `--limit 80` (alias: `--max-nodes`) | Truncate after N visible lines, print a summary line `[+ 412 more nodes]` |
| `--focus N` | Print only the subtree rooted at element `[N]`, plus an ancestor breadcrumb |
| `--offset 80` | Page from the Nth element (continue where `--limit` left off) |

Actions on a `[N]` ref work even when that ref was truncated from the visible output — the selector_map persists across the whole cached snapshot. This is the recommended pattern for exploring large pages:

```bash
cloak snapshot --limit 80                    # overview
cloak snapshot --focus 42                    # drill into a hit
cloak click 42                               # act
```

When the relevant region has a stable CSS selector, `cloak snapshot --within main` scopes the raw accessibility tree before refs are assigned. The resulting tree and selector map contain only that main-document subtree, which avoids spending refs and output on navigation, sidebars, and toolbars. Selector scoping cannot be combined with `--frames` or `--mode dom`.

## Multi-frame snapshots

By default a snapshot covers only the current frame. Add `--frames` to merge child iframe AX trees into the parent — the daemon walks each frame and inlines its tree under the corresponding iframe node. Use this on payment widgets, embedded forms, and OAuth dialogs that live inside iframes.

## Diff mode

`cloak snapshot --diff` compares against the previous cached snapshot with the same mode, selector, and frame settings, and marks every line:

```
  [3] textbox "Search" value="shoes" focused
[+] [9] button "Apply filter"           # added since last snapshot
[~] [4] link "Cart (2)" href="/cart"    # text or attrs changed
```

Removed lines are summarised in a trailing block. Diff is purely line-level; it doesn't track `[N]` renumbering, so use it for "what changed" awareness rather than as a complete change log.

## selector_map and backend_node_map

Internally the snapshot carries two maps alongside the tree text:

- `selector_map`: `{N: ElementRef(index, tag, role, text, attributes, depth)}` — what actions resolve positional `N` (e.g. `cloak click 5`) or `--index N` against
- `backend_node_map`: `{N: CDP backendNodeId}` — the durable Chromium node identifier the RemoteBridge backend uses to drive elements via CDP commands

CLI text output never prints the selector_map (agents read `[N]` refs from the tree directly). Pass `--selector-map` to opt in for ad-hoc inspection, or use `--json` and look under `data.selector_map`. MCP tools also omit it by default. The backend_node_map is internal — agents never need it.

## Token economics

Rough rule of thumb on a moderately complex page (Hacker News front page):

| Mode | Lines | Approx tokens |
|------|------|--------------|
| `accessible` (full) | 800-1500 | 4000-8000 |
| `compact` (default) | 80-200 | 400-1200 |
| `compact --limit 80` | 80 + summary | ~500 |
| `content` | 50-100 | 200-600 |

For interaction loops, the default `compact` + `--limit 80` is the sweet spot; only widen the window when the page genuinely needs it. For text extraction (articles, search results), `--mode content` is dramatically cheaper than parsing the full tree.

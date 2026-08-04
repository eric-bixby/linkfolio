# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`linkfolio.py` is a single-file CLI that converts a Netscape-format bookmarks export
(the file browsers produce via "Export Bookmarks to HTML") into a dark-mode,
newspaper-style `index.html` homepage. There is no package structure, no test suite,
and one runtime dependency (`bookmarks_parser`). The page shell lives in the
user-editable `template.html`.

## Commands

```bash
pip install -r requirements.txt          # installs bookmarks_parser

./linkfolio.py [input] [-o OUTPUT]        # default input bookmarks.html -> index.html
./linkfolio.py --no-icons                 # skip network favicon fetching (fast, offline)
./linkfolio.py --preserve-input           # write bookmarks-sorted.html instead of overwriting input
./linkfolio.py -t other.html              # use a different page template
./linkfolio.py --write-template           # regenerate template.html from BUILTIN_TEMPLATE
./linkfolio.py --name Ada                 # name used by the clock page greeting
```

```bash
python3 -m pytest                                   # run the test suite
python3 -m pytest test_linkfolio.py::test_die_exits_nonzero   # run a single test
```

`test_linkfolio.py` lives at the repo root and imports `linkfolio` directly (no
package layout). It covers the pure logic and mocks `urllib.request.urlopen` so the
suite runs fully offline — no test hits the network. When adding a feature that
touches favicon fetching, mock `_fetch_favicon`/`urlopen` rather than making real
requests.

## Architecture

The `main()` pipeline (bottom of the file) is the map for everything:

1. **Parse** the export with `bookmarks_parser.parse()` into a list of *root* dicts.
   Roots are identified by an `ns_root` key: `"toolbar"`, `"menu"`, or
   `"other_bookmarks"`. Each node is a dict with `type` (`"folder"` / `"bookmark"`),
   `title`, `url`, `children`, `icon`, and date fields.
2. **Sort** every root's children folders-first, alphabetically, recursively
   (`sort_nodes`).
3. **Fetch favicons** for bookmarks lacking an `icon`, deduped by domain, via
   Google's favicon service in a thread pool (`update_icons` / `_fetch_favicon`).
   Icons are stored back onto nodes as base64 data URIs. Skipped with `--no-icons`.
4. **Re-export** the sorted+enriched tree back to Netscape HTML (`write_netscape`),
   overwriting the input file by default so the on-disk export stays sorted and
   carries the fetched icons across future runs. `--preserve-input` diverts this to
   `bookmarks-sorted.html`.
5. **Build the page** (`build_html`): a fixed hover-menu toolbar from the `"toolbar"`
   root (`build_toolbar`) plus two tables — an index and a main listing — generated
   from the `"menu"`/`"other_bookmarks"` root's folders, all substituted into the
   template returned by `load_template`.

### Templating

`build_html` no longer holds the page shell — it fills placeholders in a template:

| Placeholder | Source |
| --- | --- |
| `{{LOGO}}` | `build_logo()` |
| `{{TOOLBAR}}` | `build_toolbar()` |
| `{{INDEX_TABLE}}` / `{{MAIN_TABLE}}` | `table_html()` |
| `{{NAME}}` | `--name`, escaped by `_js_string` |

- **`template.html` and `BUILTIN_TEMPLATE` must stay identical.** The module embeds
  a copy so a `pip install`ed linkfolio (a bare `py-modules` install, no data files)
  still works. `template.html` is regenerated with `--write-template`, and
  `test_shipped_template_matches_builtin` fails if the two drift — edit
  `BUILTIN_TEMPLATE`, then re-run `--write-template`.
- `load_template` resolves in order: `--template` path (fatal if unreadable), then
  `template.html` in the CWD, then next to `linkfolio.py`, then `BUILTIN_TEMPLATE`.
- Substitution is a **single** `_PLACEHOLDER_RE.sub` pass, so a bookmark title
  containing `{{MAIN_TABLE}}` is never expanded. Unknown `{{...}}` tags are left
  alone, which keeps `{{` in user CSS/JS safe. Missing `{{TOOLBAR}}`/`{{MAIN_TABLE}}`
  only warns — it doesn't `die()`.

### The logo and the clock dashboard

`build_logo()` emits the `<button id='logo'>` (inline SVG folder-plus-chain-link
mark, wordmark `Link<em>Folio</em>`). Its click handler — in the template, not
Python — toggles `body.tables-hidden`, which hides `main`, reveals `#dashboard`
(clock/date/greeting) and the photo credit, and lightens
`#overlay` so the background photo comes forward. Tables are shown by default;
the state is deliberately not persisted.

Splitting the toolbar means the fixed bar is now `<header id="topbar">` in the
template, holding `{{LOGO}}` plus the `<nav id="toolbar">` that `build_toolbar`
returns. `build_toolbar` still returns `""` when there's no toolbar root — the logo
lives outside it, so the tables stay toggleable either way.

### Things to know before editing

- **Two output formats, don't confuse them.** `write_netscape` emits the
  Netscape/browser re-export (source of truth, round-trippable). `build_html` emits
  the styled viewing page. Bookmark-rendering helpers differ per format:
  `_bookmark_html` (page `<a>` tags) vs `_netscape_nodes`/`_bookmark_attrs`
  (Netscape `<DT><A>` lines).
- **Folder anchors** use a `_fid` integer injected onto folder dicts by
  `_assign_folder_ids`. `collect_index_items` and `collect_main_items` read `_fid` to
  cross-link the index table to the main table — assignment must run before either.
- **Icon key aliasing.** Different export sources spell the favicon key differently
  (`icon`, `icon_uri`, `iconUri`, `ICON_URI`, `ICON`). Rendering/export helpers check
  several variants; preserve this when touching icon logic.
- **Menu root fallback.** If the menu root has no folders, index/main tables are
  simply omitted and the page still renders (toolbar only).
- **Fatal errors** go through `die()` (prints `✖` to stderr, exits non-zero). Parse
  failure, missing menu/other_bookmarks root, and I/O errors all use it.
- **Never put a `background` on `body` in the template.** `#background`/`#overlay`
  sit at negative z-index, and an in-flow element's background paints *above*
  negative z-index layers — so a body background hides the photo everywhere body's
  box reaches. The page colour belongs on `html`, whose background propagates to the
  canvas and paints below them.
- CSS and page JS live in the template, not in Python. Favicons are still embedded
  data URIs, but the page is no longer strictly self-contained: the template's
  `BACKGROUND_URL` points at an Unsplash photo. It degrades to a CSS gradient when
  the image can't load, so the page still renders offline.

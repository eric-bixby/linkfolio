# linkfolio

Convert Netscape bookmarks into a newspaper-style homepage.

`linkfolio.py` reads a browser bookmarks export (Netscape HTML format) and
generates a dark-mode `index.html`: a fixed toolbar with hover drop-downs built
from your Bookmarks Toolbar, plus index/main tables built from your Bookmarks
Menu folders. Favicons are fetched and embedded inline as data URIs, so your
links render without any external files.

Click the **LinkFolio logo** at the left of the toolbar to hide the link tables;
the page becomes a clock, date, and greeting over the background photo. Click it
again to bring the tables back. The tables are shown by default.

## Install

```bash
pip install -r requirements.txt
```

Requires Python 3.10+.

## Usage

First, export your bookmarks from your browser to an HTML file (in Firefox:
**Bookmarks → Manage Bookmarks → Import and Backup → Export Bookmarks to HTML**).

```bash
./linkfolio.py bookmarks.html            # read bookmarks.html -> write index.html
./linkfolio.py                           # same, using the default bookmarks.html
./linkfolio.py export.html -o home.html  # custom input and output filenames
./linkfolio.py --name Ada                # greet "Good Morning, Ada" on the clock page
```

Open the generated `index.html` in any browser.

### Options

| Option | Description |
| --- | --- |
| `input` | Bookmarks export to read (default: `bookmarks.html`). |
| `-o`, `--output` | HTML page to write (default: `index.html`). |
| `-t`, `--template` | Page template to fill in (default: `template.html` if present, else the built-in one). |
| `--write-template` | Write the built-in template to `template.html` (or a given path) and exit. |
| `--name` | Name to greet on the clock dashboard (default: greet without a name). |
| `--no-icons` | Skip fetching missing favicons from the network (fast, works offline). |
| `--preserve-input` | Don't overwrite the input file with the sorted export; write `bookmarks-sorted.html` instead. |

By default linkfolio also rewrites the input file: it saves your bookmarks back in
Netscape format, sorted (folders first, then alphabetical) and with the fetched
favicons embedded. Keeping this sorted export means later runs reuse the icons
instead of re-downloading them. Use `--preserve-input` if you'd rather leave the
original export untouched.

## Customizing the page

The whole page shell — markup, CSS, the background photo, the clock scripts —
lives in `template.html`. Edit it and re-run linkfolio; your changes survive
every regeneration. If you installed linkfolio from PyPI and have no template
file yet, get one with:

```bash
linkfolio --write-template          # writes template.html you can edit
```

linkfolio looks for the template in the current directory, then next to
`linkfolio.py`, and falls back to a built-in copy if it finds neither. Pass
`-t path/to/other.html` to use a different one.

Generated content is substituted into these placeholders:

| Placeholder | Replaced with |
| --- | --- |
| `{{LOGO}}` | The clickable logo button that toggles the link tables. |
| `{{TOOLBAR}}` | The `<nav id="toolbar">` of hover drop-down menus. |
| `{{INDEX_TABLE}}` | The index table (empty when the menu has no folders). |
| `{{MAIN_TABLE}}` | The main listing table (likewise). |
| `{{NAME}}` | The `--name` value, escaped for the greeting's JS string. |

Anything else in the template is copied through untouched, so `{{` in your own
CSS or scripts is safe.

### The background photo

The template loads a single Unsplash photo, set near the top of its `<script>`:

```js
const BACKGROUND_URL = 'https://images.unsplash.com/photo-...';
```

Point it at any image URL — or a local file, or a `data:` URI if you want the
page to stay fully self-contained. When the image can't be loaded (offline, bad
URL) the page falls back to a gradient, so it always renders.

## Development

```bash
pip install -r requirements.txt   # installs bookmarks_parser and pytest
python3 -m pytest                 # run the test suite (fully offline)
```

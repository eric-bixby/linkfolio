# linkfolio

Convert Netscape bookmarks into a newspaper-style homepage.

`linkfolio.py` reads a browser bookmarks export (Netscape HTML format) and
generates a self-contained, dark-mode `index.html`: a fixed toolbar with hover
drop-downs built from your Bookmarks Toolbar, plus index/main tables built from
your Bookmarks Menu folders. Favicons are fetched and embedded inline, so the
generated page needs no network or external files to display.

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
```

Open the generated `index.html` in any browser.

### Options

| Option | Description |
| --- | --- |
| `input` | Bookmarks export to read (default: `bookmarks.html`). |
| `-o`, `--output` | HTML page to write (default: `index.html`). |
| `--no-icons` | Skip fetching missing favicons from the network (fast, works offline). |
| `--preserve-input` | Don't overwrite the input file with the sorted export; write `bookmarks-sorted.html` instead. |

By default linkfolio also rewrites the input file: it saves your bookmarks back in
Netscape format, sorted (folders first, then alphabetical) and with the fetched
favicons embedded. Keeping this sorted export means later runs reuse the icons
instead of re-downloading them. Use `--preserve-input` if you'd rather leave the
original export untouched.

## Development

```bash
pip install -r requirements.txt   # installs bookmarks_parser and pytest
python3 -m pytest                 # run the test suite (fully offline)
```

#!/usr/bin/env python3
"""
Generate **index.html**: a dark‑mode bookmarks page from a Netscape‑format export.

Layout
----
* **Horizontal toolbar** fixed at the top, built from the *Bookmarks Toolbar*
  root. First‑level folders behave like menu buttons; hovering reveals a drop‑down
  list. Bookmarks that aren't inside a folder are collected into a pseudo‑folder
  with the toolbar's title.
* **Logo** pinned to the left of that toolbar. Clicking it hides/shows the link
  tables; with the tables hidden the page becomes a clock/greeting dashboard over
  the background photo. Tables are shown by default.
* **Index/Main tables** built from *Bookmarks Menu* folders (if any). If the
  menu contains no folders the page still renders—just without the tables.
* The page shell (markup, CSS, background photo, greeting) lives in an editable
  **template.html**; generated content is substituted into ``{{PLACEHOLDER}}``
  tags. A copy of the default template is built into this module, so linkfolio
  still works when no template file is present.
* Prints clear error messages (✖) and exits with *non‑zero* status on fatal
  problems (parse failure, missing roots, I/O errors).
"""

# pip install bookmarks_parser

from __future__ import annotations

import argparse
import base64
import html
import itertools
import json
import pathlib
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NoReturn
from urllib.parse import urlparse

import bookmarks_parser

# ────
# Configuration
# ────

DEFAULT_INPUT = "bookmarks.html"
DEFAULT_OUTPUT = "index.html"
DEFAULT_TEMPLATE = "template.html"
SORTED_OUTPUT = "bookmarks-sorted.html"

# Generic blue globe shown when a bookmark has no favicon (mirrors Google's default)
_DEFAULT_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
    "%3Ccircle cx='8' cy='8' r='7.5' fill='%234285f4'/%3E"
    "%3Cellipse cx='8' cy='8' rx='3.5' ry='7.5' fill='none' stroke='white' stroke-width='.8'/%3E"
    "%3Cline x1='.5' y1='8' x2='15.5' y2='8' stroke='white' stroke-width='.8'/%3E"
    "%3Cline x1='2' y1='5' x2='14' y2='5' stroke='white' stroke-width='.8'/%3E"
    "%3Cline x1='2' y1='11' x2='14' y2='11' stroke='white' stroke-width='.8'/%3E"
    "%3C/svg%3E"
)

# ────
# Helper functions
# ────


def _sort_folders_first_alpha(nodes: list[dict]) -> list[dict]:
    """Sort nodes folders-first, each group alphabetically by title."""
    folders = sorted(
        [n for n in nodes if n.get("type") == "folder"],
        key=lambda n: (n.get("title") or "").lower(),
    )
    bookmarks = sorted(
        [n for n in nodes if n.get("type") == "bookmark"],
        key=lambda n: (n.get("title") or "").lower(),
    )
    return folders + bookmarks


def sort_nodes(nodes: list[dict], recursive: bool = True) -> list[dict]:
    """Return *nodes* sorted folders-first; folder children are sorted in place when recursive=True."""
    sorted_nodes = _sort_folders_first_alpha(nodes)
    if recursive:
        for node in sorted_nodes:
            if node.get("type") == "folder" and node.get("children"):
                node["children"] = sort_nodes(node["children"], recursive=True)
    return sorted_nodes


def _assign_folder_ids(nodes: list[dict], counter: itertools.count) -> None:
    """Recursively assign a unique integer ``_fid`` to every folder node."""
    for node in nodes:
        if node.get("type") == "folder":
            node["_fid"] = next(counter)
            _assign_folder_ids(node.get("children", []), counter)


# ────
# Favicon fetching
# ────

_FAVICON_SERVICE = "https://www.google.com/s2/favicons?domain={domain}&sz=16"


def _fetch_favicon(domain: str) -> str | None:
    """Fetch a 16 px favicon for *domain* via Google's service; return a base64 data URI or None."""
    try:
        url = _FAVICON_SERVICE.format(domain=domain)
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read()
            ct = resp.headers.get_content_type()
        return f"data:{ct};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


def _collect_bookmarks(nodes: list[dict]) -> list[dict]:
    """Return a flat list of every bookmark node in the tree."""
    out: list[dict] = []
    for n in nodes:
        if n.get("type") == "bookmark":
            out.append(n)
        elif n.get("type") == "folder":
            out.extend(_collect_bookmarks(n.get("children", [])))
    return out


def update_icons(roots: list[dict], workers: int = 20) -> None:
    """Fetch and store favicons for bookmarks that don't already have one, one fetch per domain."""
    bookmarks = []
    for root in roots:
        bookmarks.extend(_collect_bookmarks(root.get("children", [])))

    by_domain: dict[str, list[dict]] = {}
    for n in bookmarks:
        if n.get("icon"):
            continue  # keep the icon from the export / a previous run
        domain = urlparse(n.get("url", "")).netloc
        if domain:
            by_domain.setdefault(domain, []).append(n)

    total = len(by_domain)
    print(f"Fetching favicons for {total} domains ({sum(len(v) for v in by_domain.values())} bookmarks)…")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_favicon, domain): domain for domain in by_domain
        }
        for future in as_completed(futures):
            icon = future.result()
            if icon:
                for node in by_domain[futures[future]]:
                    node["icon"] = icon
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  {done}/{total}")
    print("✔ Updated favicons")


# ────
# Toolbar (horizontal) builder
# ────


def _bookmark_html(n: dict) -> str:
    """Render a bookmark node as an <a> with favicon; empty titles fall back to the URL."""
    title = html.escape(n.get("title") or n.get("url", ""))
    url = html.escape(n.get("url", "#"))
    icon = (
        n.get("icon")
        or n.get("icon_uri")
        or n.get("iconUri")
        or n.get("ICON_URI")
        or n.get("ICON")
        or _DEFAULT_FAVICON
    )
    icon_html = f'<img src="{html.escape(icon)}" class="favicon" alt="" />'
    return f'<a href="{url}">{icon_html}{title}</a>'


def _links_html(nodes: list[dict]) -> str:
    """Return <a> list for the given bookmark *nodes* joined by <br>."""
    return "<br>\n".join(
        _bookmark_html(n) for n in nodes if n.get("type") == "bookmark"
    )


def build_toolbar(toolbar_root: dict | None) -> str:
    """Return the HTML markup for the horizontal toolbar."""
    if toolbar_root is None:
        return ""  # nothing to render

    items: list[str] = []
    loose: list[dict] = []  # bookmarks not inside a folder

    children = toolbar_root.get("children", [])

    for child in children:
        if child.get("type") == "folder":
            title = html.escape(child.get("title", "(untitled)"))
            items.append(
                "<div class='tb-item'>"
                f"<span class='tb-label'>{title}</span>"
                f"<div class='tb-menu'>{_links_html(child.get('children', []))}</div>"
                "</div>"
            )
        elif child.get("type") == "bookmark":
            loose.append(child)

    if loose:  # add pseudo‑folder first
        label = html.escape(toolbar_root.get("title", "Toolbar"))
        items.insert(
            0,
            "<div class='tb-item'>"
            f"<span class='tb-label'>{label}</span>"
            f"<div class='tb-menu'>{_links_html(loose)}</div>"
            "</div>",
        )

    return "<nav id='toolbar'>" + "\n".join(items) + "</nav>"


# ────
# Bookmarks Menu helpers
# ────


def collect_index_items(nodes: list[dict]) -> list[str]:
    out: list[str] = []
    for n in nodes:
        if n.get("type") == "folder":
            fid = n["_fid"]
            title = html.escape(n.get("title", "(untitled folder)"))
            out.append(
                f'<a id="index{fid}" href="#folder{fid}" class="folder">{title}</a>'
            )
            out.extend(collect_index_items(n.get("children", [])))
    return out


def collect_main_items(nodes: list[dict]) -> list[str]:
    out: list[str] = []
    for n in nodes:
        if n.get("type") == "folder":
            fid = n["_fid"]
            title = html.escape(n.get("title", "(untitled folder)"))
            out.append(
                f'<a id="folder{fid}" href="#top" class="folder">{title}</a>'
            )
            out.extend(collect_main_items(n.get("children", [])))
        elif n.get("type") == "bookmark":
            out.append(_bookmark_html(n))
    return out


def table_html(cols: list[tuple[str, list[str]]]) -> str:
    if not cols:
        return ""  # render nothing when list empty
    heads = "".join(f"<th>{hdr}</th>" for hdr, _ in cols)
    cells = []
    for _, items in cols:
        joined = "<br>\n".join(items)
        cells.append(f"<td>{joined}</td>\n")
    return f"<table>\n<tr>{heads}</tr>\n<tr>{''.join(cells)}</tr>\n</table>"


# ────
# Netscape export
# ────


def _ns_escape(s: str) -> str:
    """Escape text for Netscape bookmark output."""
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _ns_url_escape(s: str) -> str:
    """Escape bookmark URLs for Netscape output without rewriting ampersands."""
    return s.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _folder_attrs(n: dict) -> str:
    """Return ADD_DATE/LAST_MODIFIED attribute string for a folder node."""
    parts = []
    if n.get("add_date"):
        parts.append(f'ADD_DATE="{n["add_date"]}"')
    if n.get("last_modified"):
        parts.append(f'LAST_MODIFIED="{n["last_modified"]}"')
    if n.get("ns_root") == "toolbar":
        parts.append('PERSONAL_TOOLBAR_FOLDER="true"')
    if n.get("ns_root") == "other_bookmarks":
        parts.append('UNFILED_BOOKMARKS_FOLDER="true"')
    return (" " + " ".join(parts)) if parts else ""


def _bookmark_attrs(n: dict, now: int) -> str:
    """Return ADD_DATE/LAST_MODIFIED/ICON_URI/ICON attribute string for a bookmark node."""
    parts = []
    if n.get("add_date"):
        parts.append(f'ADD_DATE="{n["add_date"]}"')
    parts.append(f'LAST_MODIFIED="{now}"')
    icon_uri = n.get("icon_uri") or n.get("iconUri") or n.get("ICON_URI")
    if icon_uri:
        parts.append(f'ICON_URI="{_ns_url_escape(icon_uri)}"')
    icon = n.get("icon") or n.get("ICON")
    if icon:
        parts.append(f'ICON="{_ns_escape(icon)}"')
    return (" " + " ".join(parts)) if parts else ""


def _netscape_nodes(nodes: list[dict], depth: int = 1, now: int = 0) -> list[str]:
    """Recursively render *nodes* as Netscape bookmark lines."""
    pad = "    " * depth
    lines: list[str] = []
    for n in nodes:
        if n.get("type") == "folder":
            title = _ns_escape(n.get("title", ""))
            attrs = _folder_attrs(n)
            lines.append(f"{pad}<DT><H3{attrs}>{title}</H3>")
            lines.append(f"{pad}<DL><p>")
            lines.extend(_netscape_nodes(n.get("children", []), depth + 1, now))
            lines.append(f"{pad}</DL><p>")
        elif n.get("type") == "bookmark":
            title = _ns_escape(n.get("title", "") or n.get("url", ""))
            url = _ns_url_escape(n.get("url", ""))
            attrs = _bookmark_attrs(n, now)
            lines.append(f'{pad}<DT><A HREF="{url}"{attrs}>{title}</A>')
    return lines


def write_netscape(parsed: list[dict], output_file: str) -> None:
    """Write the full sorted bookmark tree in Netscape HTML format."""
    now = int(time.time())
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        "<!-- This is an automatically generated file.",
        "     It will be read and overwritten.",
        "     DO NOT EDIT! -->",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]
    for root in parsed:
        if root.get("ns_root") == "menu":
            # Firefox exports menu items directly in the top-level <DL>, without a wrapper folder
            lines.extend(_netscape_nodes(root.get("children", []), depth=1, now=now))
            continue
        title = _ns_escape(root.get("title", root.get("ns_root", "")))
        attrs = _folder_attrs(root)
        lines.append(f"    <DT><H3{attrs}>{title}</H3>")
        lines.append("    <DL><p>")
        lines.extend(_netscape_nodes(root.get("children", []), depth=2, now=now))
        lines.append("    </DL><p>")
    lines.append("</DL><p>")

    try:
        pathlib.Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"✔ Wrote {output_file}")
    except Exception as exc:
        die(f"Failed to write '{output_file}': {exc}")


# ────
# Logo
# ────

# Folder silhouette with a chain-link glyph cut into it; the folder inherits the
# button's colour (currentColor) so a template can restyle it with plain CSS.
_LOGO_SVG = (
    "<svg class='logo-mark' xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' "
    "width='26' height='26' aria-hidden='true' focusable='false'>"
    "<path d='M4 8.5A2.5 2.5 0 0 1 6.5 6h5.7a2 2 0 0 1 1.6.8l1.5 1.7h10.2"
    "A2.5 2.5 0 0 1 28 11v13.5a2.5 2.5 0 0 1-2.5 2.5h-19A2.5 2.5 0 0 1 4 24.5z' "
    "fill='currentColor'/>"
    "<g transform='translate(8 10) scale(0.66)' fill='none' stroke='#1e1e1e' "
    "stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'/>"
    "<path d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'/>"
    "</g></svg>"
)


def build_logo() -> str:
    """Return the clickable LinkFolio logo that toggles the link tables."""
    return (
        "<button id='logo' type='button' aria-pressed='false' "
        "aria-controls='tables' title='Show or hide the link tables' "
        "aria-label='Show or hide the link tables'>"
        f"{_LOGO_SVG}"
        "<span class='logo-text'>Link<em>Folio</em></span>"
        "</button>"
    )


# ────
# Page builder
# ────

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

# Placeholders a template needs for the page to be usable at all.
_REQUIRED_PLACEHOLDERS = ("TOOLBAR", "MAIN_TABLE")

BUILTIN_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bookmarks</title>
<style>

*{box-sizing:border-box;}

html{scroll-padding-top:3.5rem;}

html,body{
  margin:0;
  padding:0;
  min-height:100%;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;
  color:#e0e0e0;
}

/* The page colour goes on html only. html's background propagates to the canvas,
   which paints *below* the negative z-index layers below; a background on body
   would paint over them and hide the photo wherever body's box reaches. */
html{background:#121212;}

/* Background photo, and the scrim that keeps the tables readable on top of it.
   The scrim lightens when the tables are hidden so the photo comes forward. */
#background{position:fixed;inset:0;background-size:cover;background-position:center;background-repeat:no-repeat;opacity:0;transition:opacity 1s ease-in-out;z-index:-2;}
#overlay{position:fixed;inset:0;background:rgba(18,18,18,0.62);transition:background .35s ease;z-index:-1;}
body.tables-hidden #overlay{background:rgba(0,0,0,0.25);}

/* Top bar: logo on the left, bookmark menus to its right. */
#topbar{position:fixed;top:0;left:0;right:0;display:flex;align-items:center;gap:1.25em;background:#1e1e1e;border-bottom:1px solid #333;padding:0.4em 1em;z-index:1000;}
#logo{display:flex;align-items:center;gap:.45em;margin:0;padding:.15em .35em;border:0;border-radius:4px;background:none;color:#ffcb6b;font:inherit;cursor:pointer;}
#logo:hover{background:#2a2a2a;}
#logo:focus-visible{outline:2px solid #4ea3ff;outline-offset:2px;}
.logo-mark{flex:none;display:block;}
.logo-text{font-weight:700;font-size:1.05em;white-space:nowrap;}
.logo-text em{font-style:normal;color:#4ea3ff;}

#toolbar{display:flex;gap:1.25em;align-items:center;min-width:0;}
#toolbar .tb-item{position:relative;}
#toolbar .tb-label{color:#ffcb6b;font-weight:bold;cursor:pointer;}
#toolbar .tb-menu{display:none;position:absolute;top:100%;left:0;background:#1e1e1e;padding:0.5em;border:1px solid #333;max-width:320px;white-space:nowrap;}
#toolbar .tb-item:hover .tb-menu{display:block;}
#toolbar a{color:#4ea3ff;text-decoration:none;line-height:1.3;}
#toolbar a:hover{text-decoration:underline;}

main{position:relative;z-index:1;margin-top:3.5rem;padding:1rem;}

table {
  width: 1200px;
  max-width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  border: 1px solid black;
  background: rgba(18,18,18,0.85);
  margin-bottom: 1rem;
}

th,td{text-align:left;vertical-align:top;padding:2px;border:none}
th{background:#1e1e1e;font-weight:600;}
td, th {
  padding: 5px;
  border: 1px solid black;
}

a{color:#4ea3ff;text-decoration:none;line-height:1.25;}
a:hover{text-decoration:underline;}
.folder{font-weight:bold;color:#ffcb6b;}
img.favicon{width:16px;height:16px;vertical-align:middle;margin-right:0.5em;border-radius:2px;}

/* Clock dashboard: hidden until the logo hides the tables. */
#dashboard{display:none;position:relative;z-index:1;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;color:#fff;text-shadow:2px 2px 8px rgba(0,0,0,0.7);}
#clock{font-size:120px;font-weight:300;letter-spacing:2px;margin-bottom:20px;}
#date{font-size:24px;font-weight:300;margin-bottom:40px;opacity:.9;}
#greeting{font-size:32px;font-weight:300;margin-top:20px;opacity:.95;}
.photo-credit{display:none;position:fixed;bottom:20px;left:20px;z-index:1;color:#fff;font-size:12px;text-shadow:1px 1px 4px rgba(0,0,0,0.8);opacity:.7;transition:opacity .3s;}
.photo-credit:hover{opacity:1;}
.photo-credit a{color:#fff;}

body.tables-hidden{overflow:hidden;}
body.tables-hidden main{display:none;}
body.tables-hidden #dashboard{display:flex;}
body.tables-hidden .photo-credit{display:block;}

@media (max-width:768px){
  #clock{font-size:72px;}
  #date{font-size:18px;}
  #greeting{font-size:24px;}
  .logo-text{display:none;}
}

</style>
</head>
<body>
<a id="top"></a>

<div id="background"></div>
<div id="overlay"></div>

<header id="topbar">
{{LOGO}}
{{TOOLBAR}}
</header>

<main id="tables">
{{INDEX_TABLE}}
{{MAIN_TABLE}}
</main>

<div id="dashboard">
  <div id="clock"></div>
  <div id="date"></div>
  <div id="greeting"></div>
</div>

<div class="photo-credit">
  Photo by <a href="https://unsplash.com" target="_blank" rel="noopener" id="photographer">Unsplash</a>
</div>

<script>
// Edit these two lines to change the background photo and who gets greeted.
const BACKGROUND_URL = 'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=1920&h=1080&fit=crop';
const USER_NAME = "{{NAME}}";

function updateClock() {
  const now = new Date();
  let hours = now.getHours();
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12 || 12;  // 0 -> 12 for midnight/noon
  document.getElementById('clock').textContent =
    `${String(hours).padStart(2, '0')}:${minutes} ${ampm}`;
}

function updateDate() {
  const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
  document.getElementById('date').textContent =
    new Date().toLocaleDateString('en-US', options);
}

function updateGreeting() {
  const hour = new Date().getHours();
  let greeting = hour < 12 ? 'Good Morning' : hour < 18 ? 'Good Afternoon' : 'Good Evening';
  if (USER_NAME) greeting += ', ' + USER_NAME;
  document.getElementById('greeting').textContent = greeting;
}

// Preload the photo so it fades in once; fall back to a gradient when offline.
function loadBackground() {
  const background = document.getElementById('background');
  const img = new Image();
  img.onload = () => {
    background.style.backgroundImage = `url('${BACKGROUND_URL}')`;
    background.style.opacity = '1';
  };
  img.onerror = () => {
    background.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
    background.style.opacity = '1';
  };
  img.src = BACKGROUND_URL;
}

// The logo swaps between the link tables and the clock dashboard.
function initLogo() {
  const logo = document.getElementById('logo');
  if (!logo) return;
  logo.addEventListener('click', () => {
    const hidden = document.body.classList.toggle('tables-hidden');
    logo.setAttribute('aria-pressed', hidden ? 'true' : 'false');
  });
}

function init() {
  updateClock();
  updateDate();
  updateGreeting();
  loadBackground();
  initLogo();
  setInterval(updateClock, 1000);
  setInterval(() => { updateDate(); updateGreeting(); }, 60000);
}

window.addEventListener('load', init);
</script>
</body>
</html>
"""


def _js_string(value: str) -> str:
    """Escape *value* for use inside a double-quoted JS string literal."""
    # json.dumps handles quotes/backslashes/control chars; "<" is neutralised so a
    # crafted value can't close the <script> element.
    return json.dumps(value)[1:-1].replace("<", "\\u003c")


def load_template(path: str | None) -> str:
    """Return the page template: *path* if given, else template.html, else the builtin."""
    if path:
        try:
            return pathlib.Path(path).read_text(encoding="utf-8")
        except Exception as exc:
            die(f"Failed to read template '{path}': {exc}")

    here = pathlib.Path(__file__).resolve().parent
    for candidate in (pathlib.Path.cwd() / DEFAULT_TEMPLATE, here / DEFAULT_TEMPLATE):
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8")
            except Exception as exc:
                die(f"Failed to read template '{candidate}': {exc}")
            print(f"Using template {candidate}")
            return text
    return BUILTIN_TEMPLATE


def build_html(
    toolbar: str,
    idx_cols: list[tuple[str, list[str]]],
    main_cols: list[tuple[str, list[str]]],
    template: str | None = None,
    name: str = "",
) -> str:
    """Substitute the generated content into *template* (default: the builtin one)."""
    tpl = BUILTIN_TEMPLATE if template is None else template

    for missing in (p for p in _REQUIRED_PLACEHOLDERS if f"{{{{{p}}}}}" not in tpl):
        print(f"⚠ Template has no {{{{{missing}}}}} placeholder", file=sys.stderr)

    values = {
        "LOGO": build_logo(),
        "TOOLBAR": toolbar,
        "INDEX_TABLE": table_html(idx_cols),
        "MAIN_TABLE": table_html(main_cols),
        "NAME": _js_string(name),
    }
    # One pass, so substituted content is never rescanned for placeholders.
    return _PLACEHOLDER_RE.sub(
        lambda m: values.get(m.group(1), m.group(0)),
        tpl,
    )


# ────
# Main entry
# ────


def die(msg: str) -> NoReturn:
    """Print msg to stderr and exit non‑zero."""
    print(f"✖ {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a styled dark-mode bookmarks page from a Netscape-format export."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=DEFAULT_INPUT,
        help=f"bookmarks export to read (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"HTML page to write (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "-t",
        "--template",
        help=f"page template to fill in (default: {DEFAULT_TEMPLATE} if present, else built-in)",
    )
    parser.add_argument(
        "--write-template",
        nargs="?",
        const=DEFAULT_TEMPLATE,
        metavar="PATH",
        help=f"write the built-in template to PATH (default: {DEFAULT_TEMPLATE}) and exit",
    )
    parser.add_argument(
        "--name",
        default="",
        help="name to greet on the clock dashboard (default: greet without a name)",
    )
    parser.add_argument(
        "--no-icons",
        action="store_true",
        help="skip fetching missing favicons from the network",
    )
    parser.add_argument(
        "--preserve-input",
        action="store_true",
        help=f"don't overwrite the input file with the sorted export; write {SORTED_OUTPUT} instead",
    )
    args = parser.parse_args()

    if args.write_template:
        try:
            pathlib.Path(args.write_template).write_text(
                BUILTIN_TEMPLATE, encoding="utf-8"
            )
        except Exception as exc:
            die(f"Failed to write '{args.write_template}': {exc}")
        print(f"✔ Wrote {args.write_template}")
        return

    template = load_template(args.template)

    # Parse export ----
    try:
        parsed = bookmarks_parser.parse(args.input)
    except Exception as exc:
        die(f"Failed to parse '{args.input}': {exc}")

    menu_root = next(
        (n for n in parsed if n.get("ns_root") in ("menu", "other_bookmarks")),
        None,
    )
    if menu_root is None:
        die("No 'menu' or 'other_bookmarks' root found in the export.")

    # Sort bookmarks and export Netscape file ----
    for root in parsed:
        root["children"] = sort_nodes(root.get("children", []))
    if not args.no_icons:
        update_icons(parsed)
    write_netscape(parsed, SORTED_OUTPUT if args.preserve_input else args.input)

    toolbar_root = next((n for n in parsed if n.get("ns_root") == "toolbar"), None)
    toolbar_html = build_toolbar(toolbar_root)

    # Build Bookmarks Menu columns ----
    menu_children = menu_root.get("children", [])
    menu_folders = [n for n in menu_children if n.get("type") == "folder"]

    index_cols: list[tuple[str, list[str]]] = []
    main_cols: list[tuple[str, list[str]]] = []

    if menu_folders:
        _assign_folder_ids(menu_folders, itertools.count())
        for folder in menu_folders:
            children = folder.get("children", [])
            fid = folder["_fid"]
            title = html.escape(folder.get("title", "(untitled)"))

            index_cols.append(
                (
                    f'<a id="index{fid}" href="#folder{fid}" class="folder">{title}</a>',
                    collect_index_items(children),
                )
            )
            main_cols.append(
                (
                    f'<a id="folder{fid}" href="#top" class="folder">{title}</a>',
                    collect_main_items(children),
                )
            )

    # Generate full HTML ----
    html_output = build_html(
        toolbar_html, index_cols, main_cols, template=template, name=args.name
    )

    try:
        pathlib.Path(args.output).write_text(html_output, encoding="utf-8")
        print(f"✔ Wrote {args.output}")
    except Exception as exc:
        die(f"Failed to write '{args.output}': {exc}")


if __name__ == "__main__":
    main()

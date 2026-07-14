#!/usr/bin/env python3
"""
Generate **index.html**: a dark‑mode bookmarks page from a Netscape‑format export.

Layout
----
* **Horizontal toolbar** fixed at the top, built from the *Bookmarks Toolbar*
  root. First‑level folders behave like menu buttons; hovering reveals a drop‑down
  list. Bookmarks that aren't inside a folder are collected into a pseudo‑folder
  with the toolbar's title.
* **Index/Main tables** built from *Bookmarks Menu* folders (if any). If the
  menu contains no folders the page still renders—just without the tables.
* Prints clear error messages (✖) and exits with *non‑zero* status on fatal
  problems (parse failure, missing roots, I/O errors).
"""

# pip install bookmarks_parser

from __future__ import annotations

import argparse
import base64
import html
import itertools
import pathlib
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
    """Escape for Netscape bookmark output (same entities Firefox emits)."""
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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
        parts.append(f'ICON_URI="{_ns_escape(icon_uri)}"')
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
            url = _ns_escape(n.get("url", ""))
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
# Page builder
# ────


def build_html(
    toolbar: str,
    idx_cols: list[tuple[str, list[str]]],
    main_cols: list[tuple[str, list[str]]],
) -> str:
    CSS = """

html{
    scroll-padding-top: 3rem;
}

html,body{
  margin:0;
  padding:0;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;
  background:#121212;
  color:#e0e0e0;
}

#toolbar{position:fixed;top:0;left:0;right:0;background:#1e1e1e;display:flex;gap:1.25em;padding:0.5em 1em;border-bottom:1px solid #333;z-index:1000;}
#toolbar .tb-item{position:relative;}
#toolbar .tb-label{color:#ffcb6b;font-weight:bold;cursor:pointer;}
#toolbar .tb-menu{display:none;position:absolute;top:100%;left:0;background:#1e1e1e;padding:0.5em;border:1px solid #333;max-width:320px;white-space:nowrap;}
#toolbar .tb-item:hover .tb-menu{display:block;}
#toolbar a{color:#4ea3ff;text-decoration:none;line-height:1.3;}
#toolbar a:hover{text-decoration:underline;}

main{margin-top:3rem;padding:1rem;}

table {
  width: 1200px;
  table-layout: fixed;
  border-collapse: collapse;
  border: 1px solid black;
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

"""

    return "\n".join(
        [
            "<!doctype html><html><head><meta charset='utf-8'><title>Bookmarks</title>",
            f"<style>{CSS}</style></head><body><a id='top'></a>",
            toolbar,
            "<main>",
            table_html(idx_cols),
            table_html(main_cols),
            "</main></body></html>",
        ]
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
    html_output = build_html(toolbar_html, index_cols, main_cols)

    try:
        pathlib.Path(args.output).write_text(html_output, encoding="utf-8")
        print(f"✔ Wrote {args.output}")
    except Exception as exc:
        die(f"Failed to write '{args.output}': {exc}")


if __name__ == "__main__":
    main()

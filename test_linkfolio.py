"""Tests for linkfolio.py.

Pure-logic functions are tested directly; the one network path
(``_fetch_favicon`` / ``update_icons``) is exercised with ``urlopen`` mocked
so the suite runs fully offline.
"""

from __future__ import annotations

import itertools
from unittest import mock

import pytest

import linkfolio as lf


# ── helpers to build node trees ────────────────────────────────────────────


def bookmark(title, url="https://example.com/", **extra):
    return {"type": "bookmark", "title": title, "url": url, **extra}


def folder(title, children=None, **extra):
    return {"type": "folder", "title": title, "children": children or [], **extra}


# ── sorting ────────────────────────────────────────────────────────────────


def test_sort_folders_first_then_alpha():
    nodes = [
        bookmark("zebra"),
        folder("Beta"),
        bookmark("apple"),
        folder("alpha"),
    ]
    result = lf._sort_folders_first_alpha(nodes)
    assert [n["title"] for n in result] == ["alpha", "Beta", "apple", "zebra"]


def test_sort_is_case_insensitive():
    nodes = [bookmark("banana"), bookmark("Apple"), bookmark("cherry")]
    assert [n["title"] for n in lf._sort_folders_first_alpha(nodes)] == [
        "Apple",
        "banana",
        "cherry",
    ]


def test_sort_handles_missing_and_none_titles():
    nodes = [bookmark(None), folder(None), bookmark("a")]
    # should not raise; None titles sort as empty string
    result = lf._sort_folders_first_alpha(nodes)
    assert result[0]["type"] == "folder"  # folders always first


def test_sort_nodes_recursive_sorts_children():
    tree = [
        folder(
            "Top",
            children=[bookmark("zzz"), folder("Bbb"), bookmark("aaa"), folder("Aaa")],
        )
    ]
    sorted_tree = lf.sort_nodes(tree, recursive=True)
    child_titles = [c["title"] for c in sorted_tree[0]["children"]]
    assert child_titles == ["Aaa", "Bbb", "aaa", "zzz"]


def test_sort_nodes_non_recursive_leaves_children_untouched():
    original_children = [bookmark("zzz"), bookmark("aaa")]
    tree = [folder("Top", children=list(original_children))]
    sorted_tree = lf.sort_nodes(tree, recursive=False)
    assert [c["title"] for c in sorted_tree[0]["children"]] == ["zzz", "aaa"]


# ── folder id assignment ───────────────────────────────────────────────────


def test_assign_folder_ids_unique_and_recursive():
    tree = [
        folder("A", children=[folder("A1"), bookmark("x")]),
        folder("B"),
    ]
    lf._assign_folder_ids(tree, itertools.count())
    ids = [
        tree[0]["_fid"],
        tree[0]["children"][0]["_fid"],
        tree[1]["_fid"],
    ]
    assert ids == [0, 1, 2]
    # bookmarks never get a _fid
    assert "_fid" not in tree[0]["children"][1]


# ── flatten ────────────────────────────────────────────────────────────────


def test_collect_bookmarks_flattens_tree():
    tree = [
        folder("A", children=[bookmark("one"), folder("B", children=[bookmark("two")])]),
        bookmark("three"),
    ]
    titles = [b["title"] for b in lf._collect_bookmarks(tree)]
    assert titles == ["one", "two", "three"]


# ── bookmark rendering ─────────────────────────────────────────────────────


def test_bookmark_html_uses_default_favicon_when_missing():
    out = lf._bookmark_html(bookmark("Site", "https://x.com/"))
    assert 'href="https://x.com/"' in out
    assert ">Site</a>" in out
    assert "svg+xml" in out  # default favicon data URI


def test_bookmark_html_prefers_explicit_icon():
    out = lf._bookmark_html(bookmark("Site", icon="data:image/png;base64,AAA"))
    assert "data:image/png;base64,AAA" in out
    assert "svg+xml" not in out


@pytest.mark.parametrize("key", ["icon", "icon_uri", "iconUri", "ICON_URI", "ICON"])
def test_bookmark_html_accepts_icon_key_aliases(key):
    out = lf._bookmark_html(bookmark("Site", **{key: "ICONVALUE"}))
    assert "ICONVALUE" in out


def test_bookmark_html_escapes_title_and_url():
    out = lf._bookmark_html(bookmark('<b>"&', url="https://x.com/?a=1&b=2"))
    assert "<b>" not in out
    assert "&lt;b&gt;" in out
    assert "&amp;" in out


def test_bookmark_html_falls_back_to_url_when_title_empty():
    out = lf._bookmark_html(bookmark("", url="https://only-url.com/"))
    assert ">https://only-url.com/</a>" in out


def test_links_html_ignores_folders():
    nodes = [bookmark("keep"), folder("drop")]
    out = lf._links_html(nodes)
    assert "keep" in out
    assert "drop" not in out


# ── toolbar ────────────────────────────────────────────────────────────────


def test_build_toolbar_none_returns_empty():
    assert lf.build_toolbar(None) == ""


def test_build_toolbar_folders_become_items():
    root = folder(
        "Bookmarks Toolbar",
        children=[folder("Dev", children=[bookmark("GitHub")])],
    )
    out = lf.build_toolbar(root)
    assert "<nav id='toolbar'>" in out
    assert "tb-label'>Dev</span>" in out
    assert "GitHub" in out


def test_build_toolbar_loose_bookmarks_get_pseudo_folder_first():
    root = folder(
        "MyBar",
        children=[bookmark("Loose"), folder("Dev", children=[bookmark("GitHub")])],
    )
    out = lf.build_toolbar(root)
    # pseudo-folder labeled with the toolbar title comes before the real folder
    assert out.index("MyBar") < out.index("Dev")
    assert "Loose" in out


# ── index / main collection ────────────────────────────────────────────────


def test_collect_index_items_only_folders_and_links_them():
    tree = [folder("A", children=[bookmark("LeafMark"), folder("B")])]
    lf._assign_folder_ids(tree, itertools.count())
    items = lf.collect_index_items(tree)
    joined = "".join(items)
    assert 'href="#folder0"' in joined
    assert 'href="#folder1"' in joined
    assert "LeafMark" not in joined  # bookmarks excluded from index


def test_collect_main_items_includes_folders_and_bookmarks():
    tree = [folder("A", children=[bookmark("LeafMark"), folder("B")])]
    lf._assign_folder_ids(tree, itertools.count())
    items = lf.collect_main_items(tree)
    joined = "".join(items)
    assert 'id="folder0"' in joined
    assert 'id="folder1"' in joined
    assert ">LeafMark</a>" in joined  # bookmark rendered


# ── tables ─────────────────────────────────────────────────────────────────


def test_table_html_empty_returns_empty_string():
    assert lf.table_html([]) == ""


def test_table_html_builds_headers_and_cells():
    out = lf.table_html([("Head", ["<a>one</a>", "<a>two</a>"])])
    assert "<th>Head</th>" in out
    assert "one" in out and "two" in out
    assert out.count("<td>") == 1


# ── Netscape escaping / attrs ──────────────────────────────────────────────


def test_ns_escape_entities():
    assert lf._ns_escape('a&b"c<d>e') == "a&amp;b&quot;c&lt;d&gt;e"


def test_folder_attrs_flags_roots():
    assert 'PERSONAL_TOOLBAR_FOLDER="true"' in lf._folder_attrs(
        folder("t", ns_root="toolbar", add_date="1")
    )
    assert 'UNFILED_BOOKMARKS_FOLDER="true"' in lf._folder_attrs(
        folder("o", ns_root="other_bookmarks")
    )
    assert lf._folder_attrs(folder("plain")) == ""


def test_bookmark_attrs_writes_dates_and_icons():
    out = lf._bookmark_attrs(
        bookmark("b", add_date="100", icon_uri="http://i/", icon="data:x"),
        now=999,
    )
    assert 'ADD_DATE="100"' in out
    assert 'LAST_MODIFIED="999"' in out
    assert 'ICON_URI="http://i/"' in out
    assert 'ICON="data:x"' in out


# ── Netscape node rendering ────────────────────────────────────────────────


def test_netscape_nodes_folder_and_bookmark_structure():
    tree = [folder("F", children=[bookmark("Site", "https://s/")])]
    lines = lf._netscape_nodes(tree, depth=1, now=5)
    text = "\n".join(lines)
    assert "<DT><H3" in text and ">F</H3>" in text
    assert "<DL><p>" in text and "</DL><p>" in text
    assert '<DT><A HREF="https://s/"' in text


def test_netscape_nodes_bookmark_title_falls_back_to_url():
    lines = lf._netscape_nodes([bookmark("", "https://u/")], now=0)
    assert ">https://u/</A>" in "\n".join(lines)


# ── full-file writers ──────────────────────────────────────────────────────


def test_write_netscape_roundtrips_menu_and_toolbar(tmp_path):
    parsed = [
        folder("Bookmarks Toolbar", ns_root="toolbar", children=[bookmark("HN", "https://hn/")]),
        folder("Bookmarks Menu", ns_root="menu", children=[folder("News")]),
    ]
    out = tmp_path / "export.html"
    lf.write_netscape(parsed, str(out))
    text = out.read_text()
    assert text.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    assert 'PERSONAL_TOOLBAR_FOLDER="true"' in text
    # menu root is emitted without a wrapper <H3>Bookmarks Menu</H3>
    assert "Bookmarks Menu" not in text
    assert ">News</H3>" in text


def test_build_html_is_self_contained_page():
    page = lf.build_html("<nav></nav>", [("I", ["<a>i</a>"])], [("M", ["<a>m</a>"])])
    assert page.startswith("<!doctype html>")
    assert "<style>" in page
    assert page.rstrip().endswith("</html>")


# ── error handling ─────────────────────────────────────────────────────────


def test_die_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as exc:
        lf.die("boom")
    assert exc.value.code == 1
    assert "boom" in capsys.readouterr().err


# ── favicon fetching (network mocked) ──────────────────────────────────────


class _FakeResp:
    def __init__(self, data, content_type):
        self._data = data
        self._ct = content_type

    def read(self):
        return self._data

    class _Headers:
        def __init__(self, ct):
            self._ct = ct

        def get_content_type(self):
            return self._ct

    @property
    def headers(self):
        return self._Headers(self._ct)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_favicon_returns_data_uri():
    with mock.patch.object(
        lf.urllib.request, "urlopen", return_value=_FakeResp(b"\x89PNG", "image/png")
    ):
        out = lf._fetch_favicon("example.com")
    assert out.startswith("data:image/png;base64,")


def test_fetch_favicon_swallows_errors():
    with mock.patch.object(lf.urllib.request, "urlopen", side_effect=OSError("nope")):
        assert lf._fetch_favicon("example.com") is None


def test_update_icons_assigns_by_domain_and_skips_existing():
    roots = [
        folder(
            "Menu",
            children=[
                bookmark("A", "https://same.com/a"),
                bookmark("B", "https://same.com/b"),
                bookmark("C", "https://other.com/", icon="data:already"),
            ],
        )
    ]

    def fake_fetch(domain):
        return f"data:icon-for-{domain}"

    with mock.patch.object(lf, "_fetch_favicon", side_effect=fake_fetch) as m:
        lf.update_icons(roots, workers=2)

    kids = roots[0]["children"]
    # both same-domain bookmarks share one fetched icon
    assert kids[0]["icon"] == "data:icon-for-same.com"
    assert kids[1]["icon"] == "data:icon-for-same.com"
    # the one that already had an icon is untouched and not re-fetched
    assert kids[2]["icon"] == "data:already"
    fetched_domains = {c.args[0] for c in m.call_args_list}
    assert fetched_domains == {"same.com"}

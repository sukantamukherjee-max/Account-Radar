"""Regression tests for page-content extraction in research/fetcher.py.

Covers the boilerplate filter (class/id/role/aria-label detection), the
protection of main/article/H1 content from accidental removal, whitespace
normalisation that keeps paragraph and list boundaries, the 16,000-character
cap, and fetch_page's access-status handling (including the LinkedIn login
wall). No network: requests.get is replaced in every fetch_page test.
"""

from __future__ import annotations

import pytest
import requests
from bs4 import BeautifulSoup

from research import fetcher
from research.fetcher import (
    MAX_CONTENT_CHARS,
    _is_boilerplate_node,
    _text_from_html,
    fetch_page,
)
from research.sources import AccessStatus

ARTICLE = (
    "The authority announced a new data centre migration programme this year "
    "and invited qualified bidders to respond to the published tender."
)


def _first(html: str, selector: str):
    return BeautifulSoup(html, "html.parser").select_one(selector)


def _page(inner: str) -> str:
    return f"<html><body>{inner}</body></html>"


# ---------------------------------------------------------------------------
# Boilerplate detection: class / id / role / aria-label
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "markup",
    [
        '<div class="footer clearfix">x</div>',          # multi-valued class (a list in BeautifulSoup)
        '<div class="cookie-banner">x</div>',            # hyphen boundary
        '<div class="site_menu">x</div>',                # underscore boundary
        '<div class="related posts">x</div>',            # space boundary
        '<div class="Newsletter">x</div>',               # case-insensitive
        '<div class="mega-menu dropdown">x</div>',       # multi-word vocabulary entry
        '<div class="side-bar">x</div>',
        '<div id="main nav">x</div>',                    # id
        '<div id="cookie-consent">x</div>',
        '<div role="navigation">x</div>',                # role
        '<div role="contentinfo">x</div>',
        '<section aria-label="Share this article">x</section>',  # aria-label
    ],
)
def test_boilerplate_attributes_are_detected(markup):
    assert _is_boilerplate_node(_first(markup, "div, section")) is True


@pytest.mark.parametrize(
    "markup",
    [
        '<div class="research-findings">x</div>',        # contains "search", not the word
        '<div class="downloads">x</div>',                # contains "ads"
        '<div class="headerless shared-services">x</div>',  # contains "header" / "share"
        '<div class="content body-text">x</div>',
        '<div>x</div>',                                  # no attributes at all
    ],
)
def test_words_merely_containing_a_boilerplate_term_are_not_boilerplate(markup):
    assert _is_boilerplate_node(_first(markup, "div")) is False


def test_boilerplate_blocks_are_removed_from_main_content():
    html = _page(
        "<main><h1>Authority launches cloud migration tender</h1>"
        f"<p>{ARTICLE}</p>"
        '<div class="related posts"><ul><li>Airline signs endpoint security deal with a vendor</li></ul></div>'
        '<div class="cookie notice">We use cookies to improve your experience on this website.</div>'
        '<div class="share-tools">Share on social media now</div>'
        "</main>"
    )
    text = _text_from_html(html)
    assert ARTICLE in text
    assert "Authority launches cloud migration tender" in text
    assert "endpoint security deal" not in text
    assert "We use cookies" not in text
    assert "Share on social media" not in text


def test_boilerplate_list_items_are_removed_in_block_fallback_path():
    html = _page(
        '<div class="mega-menu dropdown"><ul>'
        "<li>Home page of the authority website</li>"
        "<li>About us and our leadership team</li></ul></div>"
        f"<p>{ARTICLE}</p>"
    )
    text = _text_from_html(html)
    assert text == ARTICLE


def test_nested_boilerplate_inside_removed_block_does_not_break_extraction():
    html = _page(
        '<div class="footer"><div class="social"><div class="share">deep share widget text here</div></div></div>'
        f"<p>{ARTICLE}</p>"
    )
    assert _text_from_html(html) == ARTICLE


# ---------------------------------------------------------------------------
# Protection of main / article / H1 content
# ---------------------------------------------------------------------------

def test_body_with_layout_classes_keeps_all_content():
    # WordPress-style body classes must not wipe the page.
    html = (
        '<html><body class="page has-sidebar header-fixed">'
        f"<main><h1>Programme announcement</h1><p>{ARTICLE}</p></main>"
        "</body></html>"
    )
    text = _text_from_html(html)
    assert ARTICLE in text and "Programme announcement" in text


def test_wrapper_with_boilerplate_class_containing_main_is_kept():
    html = _page(f'<div class="layout-with-sidebar"><main><p>{ARTICLE}</p></main></div>')
    assert ARTICLE in _text_from_html(html)


def test_wrapper_with_boilerplate_class_containing_article_is_kept():
    html = _page(f'<div id="content-header-wrap" class="header"><article><p>{ARTICLE}</p></article></div>')
    assert ARTICLE in _text_from_html(html)


def test_header_block_holding_the_h1_is_kept():
    html = _page(
        '<div class="article-header"><h1>Authority launches cloud migration tender</h1></div>'
        f"<p>{ARTICLE}</p>"
    )
    text = _text_from_html(html)
    assert "Authority launches cloud migration tender" in text
    assert ARTICLE in text


def test_main_article_tags_and_role_main_are_never_boilerplate():
    for markup, selector in (
        ('<main class="site-header">x</main>', "main"),
        ('<article class="related">x</article>', "article"),
        ('<div role="main" class="sidebar">x</div>', "div"),
        ('<div class="menu"><div role="main">x</div></div>', "div.menu"),
    ):
        assert _is_boilerplate_node(_first(markup, selector)) is False, markup


# ---------------------------------------------------------------------------
# Whitespace normalisation and boundaries
# ---------------------------------------------------------------------------

def test_whitespace_inside_a_block_is_collapsed():
    html = _page("<p>The authority announced   a new\n\t data centre\n   migration programme this year.</p>")
    assert _text_from_html(html) == "The authority announced a new data centre migration programme this year."


def test_whitespace_is_collapsed_in_main_content_path():
    body = "The authority announced   a new\n data centre migration programme. " * 3
    text = _text_from_html(_page(f"<main><p>{body}</p></main>"))
    assert "  " not in text and "\n" not in text


def test_paragraph_and_list_boundaries_are_preserved():
    html = _page(
        "<p>First   paragraph about the\n tender process.</p>"
        "<ul><li>Item one of the published list.</li><li>Item two of the published list.</li></ul>"
    )
    assert _text_from_html(html) == (
        "First paragraph about the tender process.\n\n"
        "Item one of the published list.\n\n"
        "Item two of the published list."
    )


def test_output_never_contains_more_than_one_blank_line_or_padded_lines():
    html = _page("".join(f"<p>Paragraph number {i} with enough words to keep.</p>" for i in range(5)))
    text = _text_from_html(html)
    assert "\n\n\n" not in text
    assert " \n" not in text and "\n " not in text
    assert text.count("\n\n") == 4


def test_content_cap_is_unchanged():
    assert MAX_CONTENT_CHARS == 16000
    html = _page("".join(f"<p>{ARTICLE} {i}</p>" for i in range(400)))
    assert len(_text_from_html(html)) == MAX_CONTENT_CHARS


# ---------------------------------------------------------------------------
# fetch_page access-status handling (network replaced)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, text: str, *, status: int = 200, ctype: str = "text/html"):
        self.text = text
        self.status_code = status
        self.ok = 200 <= status < 400
        self.headers = {"content-type": ctype}


@pytest.fixture
def fake_get(monkeypatch):
    calls: list[str] = []
    responses: dict[str, object] = {}

    def _get(url, headers=None, timeout=None):
        calls.append(url)
        resp = responses.get(url)
        if isinstance(resp, Exception):
            raise resp
        if resp is None:
            raise requests.exceptions.ConnectionError("no route")
        return resp

    monkeypatch.setattr(fetcher.requests, "get", _get)
    return calls, responses


def test_fetch_page_returns_cleaned_content(fake_get):
    calls, responses = fake_get
    url = "https://news.example.com/story"
    responses[url] = _Resp(_page(
        f'<main><p>{ARTICLE}</p><div class="newsletter">Subscribe to our newsletter today please.</div></main>'
    ))
    text, status = fetch_page(url)
    assert status == AccessStatus.FETCHED
    assert ARTICLE in text and "Subscribe" not in text


def test_fetch_page_non_html_is_no_content(fake_get):
    calls, responses = fake_get
    url = "https://news.example.com/file.pdf"
    responses[url] = _Resp("%PDF-1.7 binary", ctype="application/pdf")
    assert fetch_page(url) == ("", AccessStatus.NO_CONTENT)


def test_fetch_page_all_boilerplate_is_no_content(fake_get):
    calls, responses = fake_get
    url = "https://news.example.com/empty"
    responses[url] = _Resp(_page('<div class="footer"><p>Copyright and all rights reserved notice.</p></div>'))
    assert fetch_page(url) == ("", AccessStatus.NO_CONTENT)


def test_fetch_page_http_error_and_transport_error_are_unreachable(fake_get):
    calls, responses = fake_get
    responses["https://a.example.com/x"] = _Resp("gone", status=404)
    responses["https://b.example.com/x"] = requests.exceptions.Timeout("slow")
    assert fetch_page("https://a.example.com/x") == ("", AccessStatus.UNREACHABLE)
    assert fetch_page("https://b.example.com/x") == ("", AccessStatus.UNREACHABLE)
    assert not any("r.jina.ai" in c for c in calls)  # reader fallback is LinkedIn-only


def test_fetch_page_linkedin_login_wall_is_snippet_only_and_never_content(fake_get):
    calls, responses = fake_get
    url = "https://www.linkedin.com/in/someone"
    wall = _page("<p>Sign in to view this profile. Join now to see more.</p>")
    responses[url] = _Resp(wall)
    responses["https://r.jina.ai/" + url] = _Resp("Sign in. Join LinkedIn to continue. " * 5)
    assert fetch_page(url) == ("", AccessStatus.INDEXED_SNIPPET_ONLY)
    assert calls == [url, "https://r.jina.ai/" + url]


def test_fetch_page_records_publication_date(fake_get):
    calls, responses = fake_get
    url = "https://news.example.com/dated"
    responses[url] = _Resp(
        '<html><head><meta property="article:published_time" content="2026-09-01T10:00:00+04:00"></head>'
        f"<body><main><p>{ARTICLE}</p></main></body></html>"
    )
    fetch_page(url)
    assert fetcher.published_at_for_url(url) == "2026-09-01T10:00:00+04:00"


def test_fetch_page_very_short_text_is_no_content(fake_get):
    calls, responses = fake_get
    url = "https://news.example.com/tiny"
    responses[url] = _Resp(_page("<main><p>Short.</p></main>"))
    assert fetch_page(url) == ("", AccessStatus.NO_CONTENT)


def test_non_tag_nodes_are_never_boilerplate():
    soup = BeautifulSoup('<div class="footer">text</div>', "html.parser")
    assert _is_boilerplate_node(soup.div.string) is False


# ---------------------------------------------------------------------------
# LinkedIn reader fallback paths (behaviour unchanged by the extraction fix)
# ---------------------------------------------------------------------------

LINKEDIN = "https://www.linkedin.com/in/someone"
READER = "https://r.jina.ai/" + LINKEDIN
READER_TEXT = "Public profile summary: works on enterprise infrastructure programmes in the region. " * 2


@pytest.mark.parametrize("direct", [_Resp("blocked", status=999), requests.exceptions.ConnectionError("reset")])
def test_fetch_page_linkedin_failure_uses_reader_content(fake_get, direct):
    calls, responses = fake_get
    responses[LINKEDIN] = direct
    responses[READER] = _Resp(READER_TEXT)
    text, status = fetch_page(LINKEDIN)
    assert status == AccessStatus.INDEXED_SNIPPET_ONLY
    assert text == READER_TEXT.strip()


def test_fetch_page_linkedin_wall_with_readable_reader_returns_reader_content(fake_get):
    calls, responses = fake_get
    responses[LINKEDIN] = _Resp(_page("<p>Sign in to view this profile. Join now to see more.</p>"))
    responses[READER] = _Resp(READER_TEXT)
    assert fetch_page(LINKEDIN) == (READER_TEXT.strip(), AccessStatus.INDEXED_SNIPPET_ONLY)


def test_fetch_page_linkedin_failure_with_failing_reader_is_unreachable(fake_get):
    calls, responses = fake_get
    responses[LINKEDIN] = _Resp("blocked", status=999)
    responses[READER] = requests.exceptions.Timeout("slow")
    assert fetch_page(LINKEDIN) == ("", AccessStatus.UNREACHABLE)


# ---------------------------------------------------------------------------
# Publication-date extraction (behaviour unchanged by the extraction fix)
# ---------------------------------------------------------------------------

def test_published_at_from_itemprop():
    html = '<html><body><time itemprop="datePublished" datetime="2026-05-02">2 May</time></body></html>'
    assert fetcher._extract_published_at(html) == "2026-05-02"


def test_published_at_from_nested_json_ld_skipping_invalid_blocks():
    html = (
        '<html><head><script type="application/ld+json"></script>'
        '<script type="application/ld+json">{not json</script>'
        '<script type="application/ld+json">{"@graph": [{"@type": "WebPage"}, '
        '{"@type": "NewsArticle", "datePublished": "2026-06-30T08:00:00Z"}]}</script>'
        "</head><body></body></html>"
    )
    assert fetcher._extract_published_at(html) == "2026-06-30T08:00:00Z"


def test_published_at_absent_returns_none():
    assert fetcher._extract_published_at(_page(f"<p>{ARTICLE}</p>")) is None

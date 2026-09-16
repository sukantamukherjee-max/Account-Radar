"""Phase 1C-H item 1: URL resolution hardening tests."""

from __future__ import annotations

from research.organization import OrganizationInput, discover_official_site, discover_linkedin_company_page
from research.providers.base import QueryOutcome, RawResult
from research.url_utils import is_valid_resolved_url, sanitize_resolved_url


class _StaticProvider:
    """Returns a fixed list of RawResult for every query -- used to inject
    specific (including malformed) URLs into the resolver deterministically.
    """

    name = "static"

    def __init__(self, results: list[RawResult]):
        self._results = results

    def is_configured(self) -> bool:
        return True

    def search(self, query: str, *, num_results: int = 10) -> QueryOutcome:
        return QueryOutcome(query=query, provider=self.name, results=[
            RawResult(url=r.url, title=r.title, snippet=r.snippet, query=query, provider=self.name, rank=i)
            for i, r in enumerate(self._results)
        ])


# ---------------------------------------------------------------------------
# is_valid_resolved_url / sanitize_resolved_url unit tests
# ---------------------------------------------------------------------------

def test_rejects_html_fragment():
    assert is_valid_resolved_url('<a href="https://example.com">Example</a>') is False


def test_rejects_encoded_markup():
    assert is_valid_resolved_url("https://example.com/page%3Cscript%3E") is False


def test_rejects_non_http_scheme():
    assert is_valid_resolved_url("ftp://example.com") is False
    assert is_valid_resolved_url("javascript:alert(1)") is False
    assert is_valid_resolved_url("mailto:info@example.com") is False


def test_rejects_concatenated_urls():
    assert is_valid_resolved_url("https://example.comhttps://other.com") is False


def test_rejects_whitespace_or_newline_containing_string():
    assert is_valid_resolved_url("https://example.com/page one two") is False
    assert is_valid_resolved_url("https://example.com/page\nSome Title Text") is False


def test_rejects_bare_string_with_no_scheme():
    assert is_valid_resolved_url("example.com official site") is False
    assert is_valid_resolved_url("Acme Corp - Official Website") is False


def test_rejects_hostname_without_dot():
    assert is_valid_resolved_url("https://localhost/") is False


def test_accepts_clean_url():
    assert is_valid_resolved_url("https://www.example.com") is True
    assert is_valid_resolved_url("https://example.com/about-us") is True


def test_require_host_contains_enforced():
    assert is_valid_resolved_url("https://www.linkedin.com/company/acme", require_host_contains="linkedin.com") is True
    assert is_valid_resolved_url("https://www.example.com", require_host_contains="linkedin.com") is False


def test_sanitize_strips_trailing_slash():
    assert sanitize_resolved_url("https://example.com/") == "https://example.com"


def test_sanitize_returns_none_for_malformed():
    assert sanitize_resolved_url('<div>https://example.com</div>') is None


# ---------------------------------------------------------------------------
# Resolver-level integration: a malformed top-ranked candidate must never
# become the resolved website; the resolver should fall through to the next
# valid candidate, or return None if nothing is valid.
# ---------------------------------------------------------------------------

def test_resolver_skips_malformed_top_candidate_and_uses_next_valid_one():
    # The malformed result is crafted to out-score the clean one under
    # _score_official_candidate (exact company name in title/snippet),
    # proving validation runs AFTER scoring, not instead of it.
    malformed = RawResult(
        url='<a href="https://acme-test-corp.example">Acme Test Corp — Official Website</a> extra text',
        title="Acme Test Corp — Official Website",
        snippet="Acme Test Corp official corporate site.",
        query="q", provider="static", rank=0,
    )
    clean = RawResult(
        url="https://www.acme-test-corp.example",
        title="Acme Test Corp",
        snippet="Acme Test Corp homepage.",
        query="q", provider="static", rank=1,
    )
    provider = _StaticProvider([malformed, clean])
    account = OrganizationInput(name="Acme Test Corp", country="Testland")
    website, _ = discover_official_site(account, [provider])
    assert website == "https://www.acme-test-corp.example"


def test_resolver_returns_none_when_all_candidates_malformed():
    malformed_1 = RawResult(
        url='<a href="https://acme-test-corp.example">bad</a>', title="Acme Test Corp", snippet="",
        query="q", provider="static", rank=0,
    )
    malformed_2 = RawResult(
        url="not-a-url-at-all Acme Test Corp official site", title="Acme Test Corp", snippet="",
        query="q", provider="static", rank=1,
    )
    provider = _StaticProvider([malformed_1, malformed_2])
    account = OrganizationInput(name="Acme Test Corp", country="Testland")
    website, _ = discover_official_site(account, [provider])
    assert website is None


def test_linkedin_company_resolver_rejects_malformed_and_non_linkedin_hosts():
    malformed = RawResult(
        url='https://www.linkedin.com/company/acme"><script>evil()</script>',
        title="Acme Test Corp — LinkedIn", snippet="", query="q", provider="static", rank=0,
    )
    clean = RawResult(
        url="https://www.linkedin.com/company/acme-test-corp",
        title="Acme Test Corp — LinkedIn", snippet="", query="q", provider="static", rank=1,
    )
    provider = _StaticProvider([malformed, clean])
    account = OrganizationInput(name="Acme Test Corp", country="Testland")
    url, _ = discover_linkedin_company_page(account, [provider])
    assert url == "https://www.linkedin.com/company/acme-test-corp"


def test_supplied_malformed_website_is_treated_as_not_supplied():
    from research.organization import resolve_organization

    provider = _StaticProvider([
        RawResult(url="https://www.acme-test-corp.example", title="Acme Test Corp", snippet="official site",
                   query="q", provider="static", rank=0)
    ])
    account = OrganizationInput(name="Acme Test Corp", country="Testland", website="<script>bad</script>")
    resolved = resolve_organization(account, [provider])
    assert resolved.website_was_supplied is False
    assert resolved.website == "https://www.acme-test-corp.example"

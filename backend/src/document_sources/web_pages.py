import json
import logging
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree
import requests
from bs4 import BeautifulSoup, Tag
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from src.shared.constants import (
    PAGE_TYPE_RULES, PAGE_TYPE_INSTRUCTIONS,
    SCHEMA_ORG_TYPE_MAP, OG_TYPE_MAP, PAGE_TYPE_LLM_PROMPT,
)
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

_HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
_NOISE_TAGS = ['script', 'style', 'noscript', 'nav', 'footer', 'header', 'aside', 'form', 'iframe', 'button']
_NOISE_ARIA_ROLES = {'navigation', 'banner', 'dialog', 'alertdialog', 'search', 'complementary', 'contentinfo'}
_REQUEST_TIMEOUT = 30
_USER_AGENT = 'Mozilla/5.0 (compatible; LLMGraphBuilder/1.0)'

# Markers that indicate a JS-rendered SPA where requests.get() returns incomplete content
_SPA_MARKERS = ('__NEXT_DATA__', '__NUXT__', 'window.__GATSBY', 'ng-version=', 'data-reactroot')
# If plain-text body is shorter than this after an SSR fetch, try Playwright
_MIN_CONTENT_LENGTH = 3000


def _is_spa_response(html: str) -> bool:
    """Return True if the HTML looks like a JS-rendered SPA with thin SSR content."""
    has_spa_marker = any(marker in html for marker in _SPA_MARKERS)
    if not has_spa_marker:
        return False
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    text_len = len(soup.get_text(strip=True))
    logging.info(f"SPA marker detected, SSR plain-text length: {text_len}")
    return text_len < _MIN_CONTENT_LENGTH


_EXPAND_PATTERNS = (
    'show all', 'show more', 'load more', 'see all', 'view all', 'expand',
    'alle anzeigen', 'mehr anzeigen', 'alle funktionen', 'tout afficher',
    'ver todo', 'mostra tutto',
)

_DISMISS_OVERLAYS_JS = """
    // Remove common cookie/consent overlays that block clicks
    ['onetrust-consent-sdk', 'cookie-banner', 'gdpr-banner', 'consent-modal',
     'cookie-consent', 'cc-window'].forEach(id => {
        document.getElementById(id)?.remove();
        document.querySelector('.' + id)?.remove();
    });
"""

_CLICK_EXPAND_BUTTONS_JS = """
    (patterns) => {
        const lower = s => s.trim().toLowerCase();
        const clicked = [];
        document.querySelectorAll('button,a,div,span').forEach(el => {
            const text = lower(el.textContent);
            if (patterns.some(p => text === p || text.startsWith(p))) {
                try { el.click(); clicked.push(text.slice(0, 60)); } catch(e) {}
            }
        });
        return clicked;
    }
"""


def _fetch_html_with_playwright(url: str) -> str:
    """
    Fetch fully JS-rendered HTML using a headless Chromium browser.

    After the page loads it:
      1. Dismisses common cookie/consent overlays (via JS removal).
      2. Clicks any "show all / expand" buttons to reveal collapsed content.
      3. Waits briefly for the DOM to settle before capturing.
    """
    from playwright.sync_api import sync_playwright
    logging.info(f"Fetching with Playwright: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=_USER_AGENT)
            page.goto(url, timeout=60000)
            page.wait_for_load_state('domcontentloaded')
            # Give JS frameworks time to render initial content
            page.wait_for_timeout(2000)

            # Dismiss overlays then click expand buttons
            page.evaluate(_DISMISS_OVERLAYS_JS)
            clicked = page.evaluate(_CLICK_EXPAND_BUTTONS_JS, list(_EXPAND_PATTERNS))
            if clicked:
                logging.info(f"Playwright clicked expand buttons: {clicked}")
                # Wait for any lazy-loaded content triggered by the clicks
                page.wait_for_timeout(2000)

            return page.content()
        finally:
            browser.close()


def _fetch_html(url: str) -> str:
    """
    Fetch HTML for *url*. Uses plain requests for static/SSR pages, and falls
    back to a headless Playwright browser when SPA markers are detected and
    the SSR content is too thin to be useful.
    """
    try:
        response = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT,
            verify=False,
            headers={'User-Agent': _USER_AGENT},
        )
        response.raise_for_status()
        html = response.text
    except requests.RequestException as exc:
        raise LLMGraphBuilderException(str(exc)) from exc

    if _is_spa_response(html):
        logging.info(f"SSR content too thin for {url}, falling back to Playwright")
        try:
            html = _fetch_html_with_playwright(url)
        except Exception as exc:
            logging.warning(f"Playwright fetch failed for {url}: {exc}, using SSR response")

    return html


def _extract_structured_elements(soup: BeautifulSoup) -> str:
    """
    Extract high-signal structured elements from parsed HTML and return them as
    natural-language sentences to prepend to the plain-text chunk.

    Handles:
      - Section-headed lists  (<ul>/<ol> preceded by a heading)
      - Data tables           (<table>)
      - Definition lists      (<dl>/<dt>/<dd>)
    """
    parts: list[str] = []

    # --- 1. Section-headed lists -------------------------------------------
    for list_el in soup.find_all(['ul', 'ol']):
        heading_text: str | None = None
        for sibling in list_el.previous_siblings:
            if not isinstance(sibling, Tag):
                continue
            if sibling.name in _HEADING_TAGS:
                heading_text = sibling.get_text(separator=' ', strip=True)
                break
            # Stop at any other block element — the heading is too far away
            if sibling.name in {'p', 'div', 'section', 'article', 'ul', 'ol', 'table'}:
                break

        items = [
            li.get_text(separator=' ', strip=True)
            for li in list_el.find_all('li', recursive=False)
            if li.get_text(strip=True)
        ]
        if items and heading_text:
            parts.append(f"{heading_text}: {', '.join(items)}.")

    # --- 2. Tables ----------------------------------------------------------
    # Collect global column headers from <th> elements across the whole page.
    # This handles split-header patterns (a standalone header table + separate
    # data tables) common in React-rendered pricing grids, where the data
    # tables' first <tr> is empty and the column names live in a sibling table.
    _all_th = [th.get_text(separator=' ', strip=True) for th in soup.find_all('th') if th.get_text(strip=True)]
    _seen_th: set[str] = set()
    _deduped_th: list[str] = []
    for _t in _all_th:
        if _t not in _seen_th:
            _seen_th.add(_t)
            _deduped_th.append(_t)
    # Prepend empty string so index 0 = row-label column, index 1+ = data columns
    _global_col_headers: list[str] = [''] + _deduped_th if _deduped_th else []

    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue

        header_cells = rows[0].find_all(['th', 'td'])
        col_headers = [c.get_text(separator=' ', strip=True) for c in header_cells]

        # If the first row is empty (common in split-header grids), fall back
        # to the page-level column headers collected above.
        if not any(col_headers) and _global_col_headers:
            col_headers = _global_col_headers

        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if not cells:
                continue
            row_header = cells[0].get_text(separator=' ', strip=True)
            for i, cell in enumerate(cells[1:], start=1):
                col_header = col_headers[i] if i < len(col_headers) else ''
                cell_text = cell.get_text(separator=' ', strip=True)
                # If cell has no text but contains a child element (icon/SVG/img),
                # treat it as an inclusion indicator (e.g. a checkmark icon).
                if not cell_text and cell.find(['i', 'svg', 'img']):
                    cell_text = '✓'
                if row_header and col_header and cell_text:
                    parts.append(f"{row_header} — {col_header}: {cell_text}.")

    # --- 3. Definition lists ------------------------------------------------
    for dl in soup.find_all('dl'):
        terms = dl.find_all('dt')
        definitions = dl.find_all('dd')
        for term, defn in zip(terms, definitions):
            t = term.get_text(separator=' ', strip=True)
            d = defn.get_text(separator=' ', strip=True)
            if t and d:
                parts.append(f"{t}: {d}.")

    return '\n'.join(parts)


def _extract_schema_org_type(soup: BeautifulSoup) -> str | None:
    """Return the first @type value found in JSON-LD script tags, or None."""
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            # Unwrap @graph arrays
            if isinstance(data, dict) and '@graph' in data:
                data = data['@graph']
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and '@type' in item:
                    t = item['@type']
                    return (t[0] if isinstance(t, list) else t)
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
    return None


def _extract_og_type(soup: BeautifulSoup) -> str | None:
    """Return the og:type meta content, or None."""
    tag = soup.find('meta', property='og:type')
    if tag and tag.get('content'):
        return tag['content'].strip().lower()
    return None


def classify_page_type(
    url: str,
    schema_org_type: str | None = None,
    og_type: str | None = None,
) -> str | None:
    """
    Classify a page into a type using a three-level chain:
      1. Schema.org @type  (site-agnostic, unambiguous)
      2. OpenGraph og:type (widely supported fallback)
      3. URL path-segment rules (site-specific last resort)
    Returns the page type string, or None if no level matches.
    """
    # Level 1: Schema.org
    if schema_org_type:
        for page_type, types in SCHEMA_ORG_TYPE_MAP.items():
            if schema_org_type in types:
                logging.info(f"Page type '{page_type}' from Schema.org @type='{schema_org_type}'")
                return page_type

    # Level 2: OpenGraph
    if og_type:
        for page_type, types in OG_TYPE_MAP.items():
            if og_type in types:
                logging.info(f"Page type '{page_type}' from og:type='{og_type}'")
                return page_type

    # Level 3: URL path-segment rules
    path = urlparse(url).path.lower()
    segments = set(s for s in path.split('/') if s)
    for page_type, keywords in PAGE_TYPE_RULES.items():
        if any(kw in segments or any(seg.startswith(kw) for seg in segments) for kw in keywords):
            logging.info(f"Page type '{page_type}' from URL path rules for {url}")
            return page_type

    return None


def classify_page_type_llm_fallback(title: str, description: str, llm) -> str | None:
    """
    Level 4: Use an LLM to classify page type from title + meta description.
    Returns the page type string, or None if the LLM returns 'other' or fails.
    """
    if not title and not description:
        return None
    try:
        prompt = PAGE_TYPE_LLM_PROMPT.format(title=title, description=description)
        response = llm.invoke([HumanMessage(content=prompt)])
        result = response.content.strip().lower().split()[0]
        if result in PAGE_TYPE_INSTRUCTIONS:
            logging.info(f"Page type '{result}' from LLM classification (title='{title[:50]}')")
            return result
    except Exception as e:
        logging.warning(f"LLM page type classification failed: {e}")
    return None


def get_page_type_instructions(
    url: str,
    schema_org_type: str | None = None,
    og_type: str | None = None,
    title: str | None = None,
    description: str | None = None,
    llm=None,
) -> str | None:
    """
    Run the full classification chain and return type-specific extraction
    instructions, or None for generic pages.
    """
    page_type = classify_page_type(url, schema_org_type, og_type)
    if page_type is None and llm and (title or description):
        page_type = classify_page_type_llm_fallback(title or '', description or '', llm)
    return PAGE_TYPE_INSTRUCTIONS.get(page_type) if page_type else None


def _fetch_sitemap_urls(sitemap_url: str, prefix_filter: str) -> list[str]:
    """
    Fetch a sitemap (or sitemap index) and return all matching page URLs.

    Handles:
      - Regular sitemaps: returns <loc> values filtered by ``prefix_filter``.
      - Sitemap indexes: recursively fetches each child sitemap.
    """
    try:
        resp = requests.get(
            sitemap_url,
            timeout=_REQUEST_TIMEOUT,
            verify=False,
            headers={'User-Agent': _USER_AGENT},
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError:
        return []

    # Strip XML namespace for tag comparisons
    ns = ''
    if root.tag.startswith('{'):
        ns = root.tag.split('}')[0] + '}'

    # Sitemap index — recurse into child sitemaps
    if root.tag == f'{ns}sitemapindex':
        urls: list[str] = []
        for sitemap_el in root.findall(f'{ns}sitemap'):
            loc_el = sitemap_el.find(f'{ns}loc')
            if loc_el is not None and loc_el.text:
                urls.extend(_fetch_sitemap_urls(loc_el.text.strip(), prefix_filter))
        return urls

    # Regular sitemap — collect <loc> values matching the prefix
    urls = []
    for url_el in root.findall(f'{ns}url'):
        loc_el = url_el.find(f'{ns}loc')
        if loc_el is not None and loc_el.text:
            loc = loc_el.text.strip()
            if loc.startswith(prefix_filter):
                urls.append(loc)
    return urls


def discover_subpage_urls(seed_url: str, max_pages: int = 50) -> list[str]:
    """
    Discover subpage URLs starting from *seed_url*.

    Strategy (tried in order):
      1. ``{seed}/sitemap.xml``
      2. ``{root}/sitemap.xml``
      3. ``{root}/sitemap_index.xml``
      4. ``{seed}/sitemap_index.xml``
      5. Fallback: crawl ``<a href>`` links on the seed page (same domain only)

    All discovered URLs are filtered so they start with the seed URL (same
    locale / path prefix) and de-duplicated.  The result is capped at
    *max_pages* entries.

    Args:
        seed_url: The starting URL (e.g. ``https://example.com/de/``).
        max_pages: Maximum number of URLs to return.

    Returns:
        List of unique URLs, up to *max_pages*.
    """
    parsed = urlparse(seed_url)
    root_url = f"{parsed.scheme}://{parsed.netloc}"
    # Normalise: ensure seed ends with "/" for prefix matching
    prefix = seed_url if seed_url.endswith('/') else seed_url + '/'

    sitemap_candidates = [
        urljoin(seed_url.rstrip('/') + '/', 'sitemap.xml'),
        urljoin(root_url + '/', 'sitemap.xml'),
        urljoin(root_url + '/', 'sitemap_index.xml'),
        urljoin(seed_url.rstrip('/') + '/', 'sitemap_index.xml'),
    ]
    # Deduplicate candidates while preserving order
    seen: set[str] = set()
    unique_candidates: list[str] = []
    for c in sitemap_candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    discovered: list[str] = []
    for candidate in unique_candidates:
        urls = _fetch_sitemap_urls(candidate, prefix)
        if urls:
            logging.info(f"discover_subpage_urls: found {len(urls)} URLs via sitemap {candidate}")
            discovered = urls
            break

    # Fallback: crawl <a href> links on the seed page
    if not discovered:
        logging.info(f"discover_subpage_urls: no sitemap found for {seed_url}, falling back to link crawl")
        try:
            resp = requests.get(
                seed_url,
                timeout=_REQUEST_TIMEOUT,
                verify=False,
                headers={'User-Agent': _USER_AGENT},
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                href = urljoin(seed_url, a_tag['href'])
                # Only follow links that start with the seed prefix and are on the same domain
                if href.startswith(prefix) and urlparse(href).netloc == parsed.netloc:
                    discovered.append(href)
        except requests.RequestException as exc:
            logging.warning(f"discover_subpage_urls: link crawl failed for {seed_url}: {exc}")

    # Deduplicate and cap
    seen_urls: set[str] = set()
    result: list[str] = []
    for url in discovered:
        if url not in seen_urls:
            seen_urls.add(url)
            result.append(url)
            if len(result) >= max_pages:
                break

    logging.info(f"discover_subpage_urls: returning {len(result)} URLs for seed {seed_url}")
    return result


def url_path_segments(url: str, seed_url: str) -> tuple[str, str]:
    """
    Return the (category, subcategory) path segments for *url* relative to *seed_url*.

    Example:
        url      = "https://example.com/de/products/cloudya/features"
        seed_url = "https://example.com/de"
        → ("products", "cloudya")
    """
    prefix = seed_url.rstrip('/')
    relative = url[len(prefix):].lstrip('/')
    parts = [p for p in relative.split('/') if p]
    category = parts[0] if len(parts) >= 1 else ''
    subcategory = parts[1] if len(parts) >= 2 else ''
    return category, subcategory


def filter_urls_by_paths(urls: list[str], seed_url: str, include_paths: list[str]) -> list[str]:
    """
    Filter *urls* to only those whose first path segment (relative to *seed_url*)
    is in *include_paths*.

    Args:
        urls:          Full list of discovered URLs.
        seed_url:      The seed URL used for discovery.
        include_paths: Category names to keep (first path segment after seed prefix).

    Returns:
        Filtered list of URLs in the original order.
    """
    if not include_paths:
        return urls
    include_set = {p.strip().strip('/') for p in include_paths if p.strip()}
    result = []
    for url in urls:
        category, _ = url_path_segments(url, seed_url)
        if category in include_set:
            result.append(url)
    return result


def group_urls_by_path_segments(urls: list[str], seed_url: str) -> list[dict]:
    """
    Group a list of URLs by their first two path segments relative to *seed_url*.

    Each entry in the returned list is a dict with:
      - ``category``    — first path segment after the seed prefix (str)
      - ``subcategory`` — second path segment, or ``""`` for top-level pages (str)
      - ``count``       — number of URLs in this group (int)
      - ``example``     — one representative URL from the group (str)

    The list is sorted by category ascending, then by descending count within
    each category so the most-populated subcategory appears first.

    Args:
        urls:     URLs to group (typically returned by ``discover_subpage_urls``).
        seed_url: The seed URL used for discovery; used to strip the common prefix.

    Returns:
        List of group dicts, sorted as described above.
    """
    prefix = seed_url.rstrip('/')

    counts: dict[tuple[str, str], int] = {}
    examples: dict[tuple[str, str], str] = {}

    for url in urls:
        # Strip the seed prefix and leading slash to get the relative path
        relative = url[len(prefix):].lstrip('/')
        parts = [p for p in relative.split('/') if p]
        category = parts[0] if len(parts) >= 1 else ''
        subcategory = parts[1] if len(parts) >= 2 else ''
        key = (category, subcategory)
        counts[key] = counts.get(key, 0) + 1
        if key not in examples:
            examples[key] = url

    groups = [
        {
            'category': cat,
            'subcategory': sub,
            'count': cnt,
            'example': examples[(cat, sub)],
        }
        for (cat, sub), cnt in counts.items()
    ]
    groups.sort(key=lambda g: (g['category'], -g['count']))
    return groups


def get_documents_from_web_page(source_url: str) -> list[Document]:
    """
    Load a web page, extract structured HTML elements (tables, headed lists,
    definition lists) as natural-language sentences, and return a single
    Document whose content starts with those sentences followed by the
    cleaned plain text of the page.

    Args:
        source_url: The URL to fetch.

    Returns:
        A list containing one Document object.

    Raises:
        LLMGraphBuilderException: If the page cannot be fetched or parsed.
    """
    try:
        html = _fetch_html(source_url)
        soup = BeautifulSoup(html, 'html.parser')

        # Build metadata before removing noise tags
        title_tag = soup.find('title')
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        lang_tag = soup.find('html', attrs={'lang': True})
        metadata = {
            'source': source_url,
            'title': title_tag.get_text(strip=True) if title_tag else '',
            'description': desc_tag['content'].strip() if desc_tag and desc_tag.get('content') else '',
            'language': lang_tag['lang'] if lang_tag else '',
            'schema_org_type': _extract_schema_org_type(soup),
            'og_type': _extract_og_type(soup),
        }

        # Extract structured elements from the full soup (before noise removal)
        structured = _extract_structured_elements(soup)
        if structured:
            logging.info(
                f"Extracted {len(structured.splitlines())} structured sentences from {source_url}"
            )

        # Remove noise tags and ARIA UI-chrome elements to get clean plain text
        for tag in soup(_NOISE_TAGS):
            tag.decompose()
        for tag in soup(attrs={'role': lambda r: r and r.lower() in _NOISE_ARIA_ROLES}):
            tag.decompose()
        plain_text = soup.get_text(separator='\n', strip=True)

        content = (structured + '\n\n' + plain_text) if structured else plain_text

        return [Document(page_content=content, metadata=metadata)]

    except Exception as exc:
        raise LLMGraphBuilderException(str(exc)) from exc

import logging
import requests
from bs4 import BeautifulSoup, Tag
from langchain_core.documents import Document
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

_HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
_NOISE_TAGS = ['script', 'style', 'noscript', 'nav', 'footer', 'header', 'aside', 'form', 'iframe', 'button']
_NOISE_ARIA_ROLES = {'navigation', 'banner', 'dialog', 'alertdialog', 'search', 'complementary', 'contentinfo'}
_REQUEST_TIMEOUT = 30
_USER_AGENT = 'Mozilla/5.0 (compatible; LLMGraphBuilder/1.0)'


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
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue

        header_cells = rows[0].find_all(['th', 'td'])
        col_headers = [c.get_text(separator=' ', strip=True) for c in header_cells]

        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if not cells:
                continue
            row_header = cells[0].get_text(separator=' ', strip=True)
            for i, cell in enumerate(cells[1:], start=1):
                col_header = col_headers[i] if i < len(col_headers) else ''
                cell_text = cell.get_text(separator=' ', strip=True)
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
        response = requests.get(
            source_url,
            timeout=_REQUEST_TIMEOUT,
            verify=False,
            headers={'User-Agent': _USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LLMGraphBuilderException(str(exc)) from exc

    try:
        soup = BeautifulSoup(response.text, 'html.parser')

        # Build metadata before removing noise tags
        title_tag = soup.find('title')
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        lang_tag = soup.find('html', attrs={'lang': True})
        metadata = {
            'source': source_url,
            'title': title_tag.get_text(strip=True) if title_tag else '',
            'description': desc_tag['content'].strip() if desc_tag and desc_tag.get('content') else '',
            'language': lang_tag['lang'] if lang_tag else '',
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

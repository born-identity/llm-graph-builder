"""
Before/after comparison for Enhancement 7: Structured HTML Pre-Processing.

Usage (from backend/ with venv active):
    python test_structured_html.py <url>

Example:
    python test_structured_html.py https://www.nfon.com/de/produkte/cloudya/
"""

import sys
import textwrap
import warnings

warnings.filterwarnings("ignore")  # suppress SSL warnings

# --------------------------------------------------------------------------- #
# "Before" — plain BeautifulSoup text extraction (what WebBaseLoader produces)
# --------------------------------------------------------------------------- #
def load_before(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup
    response = requests.get(url, timeout=30, verify=False,
                            headers={'User-Agent': 'Mozilla/5.0 (compatible; LLMGraphBuilder/1.0)'})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    return soup.get_text(separator='\n', strip=True)


# --------------------------------------------------------------------------- #
# "After" — new structured extraction
# --------------------------------------------------------------------------- #
def load_after(url: str) -> tuple[str, str]:
    """Returns (structured_section, full_content)."""
    import requests
    from bs4 import BeautifulSoup, Tag

    HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
    NOISE_TAGS = ['script', 'style', 'noscript', 'nav', 'footer', 'header', 'aside', 'form', 'iframe', 'button']
    NOISE_ARIA_ROLES = {'navigation', 'banner', 'dialog', 'alertdialog', 'search', 'complementary', 'contentinfo'}

    response = requests.get(url, timeout=30, verify=False,
                            headers={'User-Agent': 'Mozilla/5.0 (compatible; LLMGraphBuilder/1.0)'})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    parts = []

    # 1. Section-headed lists
    for list_el in soup.find_all(['ul', 'ol']):
        heading_text = None
        for sibling in list_el.previous_siblings:
            if not isinstance(sibling, Tag):
                continue
            if sibling.name in HEADING_TAGS:
                heading_text = sibling.get_text(separator=' ', strip=True)
                break
            if sibling.name in {'p', 'div', 'section', 'article', 'ul', 'ol', 'table'}:
                break
        items = [li.get_text(separator=' ', strip=True)
                 for li in list_el.find_all('li', recursive=False)
                 if li.get_text(strip=True)]
        if items and heading_text:
            parts.append(f"{heading_text}: {', '.join(items)}.")

    # 2. Tables
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue
        col_headers = [c.get_text(separator=' ', strip=True)
                       for c in rows[0].find_all(['th', 'td'])]
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

    # 3. Definition lists
    for dl in soup.find_all('dl'):
        for term, defn in zip(dl.find_all('dt'), dl.find_all('dd')):
            t = term.get_text(separator=' ', strip=True)
            d = defn.get_text(separator=' ', strip=True)
            if t and d:
                parts.append(f"{t}: {d}.")

    structured = '\n'.join(parts)

    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for tag in soup(attrs={'role': lambda r: r and r.lower() in NOISE_ARIA_ROLES}):
        tag.decompose()
    plain_text = soup.get_text(separator='\n', strip=True)

    full_content = (structured + '\n\n' + plain_text) if structured else plain_text
    return structured, full_content


# --------------------------------------------------------------------------- #
# Display helpers
# --------------------------------------------------------------------------- #
SEPARATOR = '─' * 80

def print_section(title: str, text: str, max_lines: int = 60):
    print(f"\n{'═' * 80}")
    print(f"  {title}")
    print('═' * 80)
    lines = text.splitlines()
    # Print non-empty lines up to max_lines
    shown = 0
    for line in lines:
        if line.strip():
            print(textwrap.fill(line.strip(), width=78, subsequent_indent='  '))
            shown += 1
            if shown >= max_lines:
                remaining = sum(1 for l in lines[lines.index(line)+1:] if l.strip())
                if remaining:
                    print(f"\n  ... ({remaining} more non-empty lines truncated)")
                break


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    url = sys.argv[1]
    print(f"\nFetching: {url}")

    print("\n[1/2] Running BEFORE (WebBaseLoader)...")
    before = load_before(url)

    print("[2/2] Running AFTER (structured extraction)...")
    structured, after = load_after(url)

    # Stats
    before_chars = len(before)
    after_chars = len(after)
    structured_lines = len([l for l in structured.splitlines() if l.strip()])

    print(f"\n{SEPARATOR}")
    print(f"  STATS")
    print(SEPARATOR)
    print(f"  Before content length : {before_chars:,} chars")
    print(f"  After content length  : {after_chars:,} chars")
    print(f"  Structured sentences  : {structured_lines}")

    print_section("STRUCTURED SECTION (new — prepended to content)", structured)
    print_section("BEFORE — first 60 non-empty lines (WebBaseLoader plain text)", before)
    print_section("AFTER  — first 60 non-empty lines (structured + plain text)", after)

    print(f"\n{'═' * 80}\n")


if __name__ == '__main__':
    main()

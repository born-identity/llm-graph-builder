"""
Test page-type classification chain for Enhancement 6.

Runs all four classification levels against real URLs:
  1. Schema.org JSON-LD @type
  2. OpenGraph og:type
  3. URL path-segment rules
  4. LLM fallback (only if --llm <model> is provided)

Usage (from backend/, no venv needed for levels 1-3):
    python test_page_type.py <url> [url2] ...
    python test_page_type.py --llm openai_gpt_4o_mini <url> [url2] ...

Example:
    python test_page_type.py \\
        https://www.nfon.com/de/produkte/cloudya/ \\
        https://www.nfon.com/de/integrations/microsoft-teams/ \\
        https://www.nfon.com/de/preise/ \\
        https://www.nfon.com/de/los-gehts/erste-schritte/ \\
        https://www.nfon.com/de/news/pressemitteilungen/
"""

import json
import sys
import warnings
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")  # suppress SSL warnings

# ── Inline copies of the constants (no venv needed) ─────────────────────────

SCHEMA_ORG_TYPE_MAP = {
    "product": ["Product", "ProductGroup", "SoftwareApplication", "MobileApplication", "WebApplication", "Service"],
    "kb": ["FAQPage", "HowTo", "TechArticle", "Article", "DefinedTerm"],
    "pricing": ["Offer", "AggregateOffer"],
    "integration": [],
}
OG_TYPE_MAP = {
    "product": ["product", "product.item"],
    "kb": ["article"],
}
PAGE_TYPE_RULES = {
    "product": ["produkte", "products", "solutions", "loesungen", "intelligent-assistant",
                "ai-transcription", "business-telefonie", "cloudya", "features"],
    "integration": ["integrations", "integrationen", "integrations-detail"],
    "kb": ["los-gehts", "get-started", "getting-started", "lexikon", "glossary",
           "knowledgebase", "knowledgebase-detail", "help", "hilfe", "support",
           "documentation", "docs"],
    "pricing": ["preise", "pricing", "plans", "tarife"],
}

# ── Classification helpers ────────────────────────────────────────────────────

def fetch_soup(url):
    r = requests.get(url, timeout=30, verify=False,
                     headers={'User-Agent': 'Mozilla/5.0 (compatible; LLMGraphBuilder/1.0)'})
    r.raise_for_status()
    return BeautifulSoup(r.text, 'html.parser')

def get_schema_org_type(soup):
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            if isinstance(data, dict) and '@graph' in data:
                data = data['@graph']
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and '@type' in item:
                    t = item['@type']
                    return t[0] if isinstance(t, list) else t
        except Exception:
            continue
    return None

def get_og_type(soup):
    tag = soup.find('meta', property='og:type')
    return tag['content'].strip().lower() if tag and tag.get('content') else None

def get_title(soup):
    t = soup.find('title')
    return t.get_text(strip=True) if t else ''

def get_description(soup):
    t = soup.find('meta', attrs={'name': 'description'})
    return t['content'].strip() if t and t.get('content') else ''

def classify_url(url, soup, llm_model=None):
    schema_type = get_schema_org_type(soup)
    og_type     = get_og_type(soup)
    title       = get_title(soup)
    description = get_description(soup)

    # Level 1: Schema.org
    if schema_type:
        for page_type, types in SCHEMA_ORG_TYPE_MAP.items():
            if schema_type in types:
                return page_type, f"Schema.org @type='{schema_type}'"

    # Level 2: OpenGraph
    if og_type:
        for page_type, types in OG_TYPE_MAP.items():
            if og_type in types:
                return page_type, f"og:type='{og_type}'"

    # Level 3: URL rules
    path = urlparse(url).path.lower()
    segments = set(s for s in path.split('/') if s)
    for page_type, keywords in PAGE_TYPE_RULES.items():
        if any(kw in segments or any(seg.startswith(kw) for seg in segments) for kw in keywords):
            return page_type, "URL path rules"

    # Level 4: LLM fallback
    if llm_model and (title or description):
        result = llm_classify(title, description, llm_model)
        if result:
            return result, f"LLM ({llm_model})"

    return None, "no match"

def llm_classify(title, description, model_name):
    """
    Calls the Gemini API directly via google-generativeai (no langchain needed).
    model_name: the env var key suffix, e.g. gemini_2.5_flash
    The env var LLM_MODEL_CONFIG_<model_name> must be set as "model_id,api_key".
    """
    try:
        import os
        env_key = f"LLM_MODEL_CONFIG_{model_name}"
        # Also try uppercase with dots replaced by underscores (zsh-safe variant)
        env_key_alt = env_key.upper().replace('.', '_')
        env_value = os.environ.get(env_key) or os.environ.get(env_key_alt, '')
        if not env_value or ',' not in env_value:
            print(f"  LLM error: env var {env_key} not set or missing api_key (expected 'model_id,api_key')")
            return None
        model_id, api_key = env_value.split(',', 1)
        # Direct Gemini REST API — only requires 'requests' (no langchain)
        prompt = (
            f"Classify this web page into one of: product, integration, kb, pricing, other.\n"
            f"Title: {title}\nDescription: {description}\n"
            f"Reply with only one word."
        )
        api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id.strip()}:generateContent"
        )
        resp = requests.post(
            api_url,
            params={"key": api_key.strip()},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip().lower().split()[0]
        return result if result in ('product', 'integration', 'kb', 'pricing') else None
    except Exception as e:
        print(f"  LLM error: {e}")
        return None

# ── Display ───────────────────────────────────────────────────────────────────

SEPARATOR = '─' * 70

def test_url(url, llm_model=None):
    print(SEPARATOR)
    print(f"URL        : {url}")
    try:
        soup = fetch_soup(url)
    except Exception as e:
        print(f"FETCH ERROR: {e}")
        return

    schema_type = get_schema_org_type(soup)
    og_type     = get_og_type(soup)
    title       = get_title(soup)
    description = get_description(soup)

    print(f"Title      : {title[:80]}")
    print(f"Description: {description[:80]}")
    print(f"Schema.org : {schema_type or '(none)'}")
    print(f"og:type    : {og_type or '(none)'}")

    page_type, source = classify_url(url, soup, llm_model)
    print(f"Classified : {page_type or '(generic)'} — via {source}")

def main():
    args = sys.argv[1:]
    llm_model = None

    if '--llm' in args:
        idx = args.index('--llm')
        llm_model = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if not args:
        print(__doc__)
        sys.exit(1)

    for url in args:
        test_url(url, llm_model)
    print(SEPARATOR)

if __name__ == '__main__':
    main()

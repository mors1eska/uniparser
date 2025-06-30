import requests
from bs4 import BeautifulSoup
import os
import re
import json
from pathlib import Path
from markdownify import markdownify as md

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

CATEGORY = "1eska"
SECTION_1C = "УНФ"

# Utility functions
def sanitize(text: str) -> str:
    t = re.sub(r"[^\w\- ]", "", text.lower()).strip()
    return t.replace(" ", "-")[:100] or "untitled"

def normalize_url(u: str) -> str:
    return u.split("#")[0].rstrip("/")

def save_md(out_dir: Path, file_slug: str, title: str, date: str, url: str, body_md: str, subcategory: str):
    """Write Markdown file with YAML front-matter."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"{file_slug}.md"
    if fp.exists():
        logging.info(f"Skipping existing file: {fp.name}")
        return
    front = {
        "date": date,
        "category": CATEGORY,
        "section_1c": SECTION_1C,
        "subcategory": subcategory,
        "question": title,
        "url": url
    }
    lines = ["---"]
    for key in ["date","category","section_1c","subcategory","question","url"]:
        lines.append(f'{key}: {json.dumps(front[key], ensure_ascii=False)}')
    lines.append("---\n")
    content = "\n".join(lines) + body_md.strip() + "\n"
    fp.write_text(content, encoding="utf-8")
    logging.info(f"Saved: {fp.name}")

BASE_URL = "https://1eska.ru/projects/publications/upravlenie-nashey-firmoy-unf/"
OUTPUT_DIR = "unf_articles_md"
os.makedirs(OUTPUT_DIR, exist_ok=True)

session = requests.Session()

# Track processed pages and articles
prev_posts = None
processed_page_urls = set()
processed_article_urls = set()

# pretend to be a real browser to get full HTML including meta tags
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/114.0.0.0 Safari/537.36"
})

# find total pages or iterate until no next
page = 1
while True:
    # build and check pagination URL
    page_url = BASE_URL if page == 1 else f"{BASE_URL}?PAGEN_1={page}"
    logging.info(f"Processing page {page}: {page_url}")
    if page_url in processed_page_urls:
        logging.info("INFO: URL страницы повторяется — выходим")
        break
    processed_page_urls.add(page_url)
    r = session.get(page_url)
    soup = BeautifulSoup(r.text, 'html.parser')
    posts = soup.select('div.item.shadow .inner-item .title > a')
    logging.info(f"Found {len(posts)} articles on page {page}")
    # detect if this page repeats the same articles
    page_article_urls = [
        normalize_url(requests.compat.urljoin(BASE_URL, a['href']))
        for a in posts
    ]
    if prev_posts is not None and page_article_urls == prev_posts:
        logging.info("INFO: Те же статьи, что на предыдущей странице — выходим")
        break
    prev_posts = page_article_urls
    logging.info(f"INFO: Article URLs on page {page}: {page_article_urls}")
    if not posts:
        break
    for a in posts:
        try:
            href = a['href']
            url = requests.compat.urljoin(BASE_URL, href)
            if url in processed_article_urls:
                logging.info(f"Already processed: {url}")
                continue
            processed_article_urls.add(url)
            logging.info(f"Fetching article: {url}")
            rr = session.get(url)
            logging.info(f"GET {url} -> {rr.status_code}, {len(rr.text)} bytes")
            ss = BeautifulSoup(rr.text, 'html.parser')
            # metadata
            headline_meta = ss.find('meta', {'itemprop':'headline'})
            if headline_meta and headline_meta.get('content'):
                title = headline_meta['content'].strip()
            else:
                # fallback to visible headline
                title_elem = ss.select_one('h1.publication__title') or ss.find('h1') or ss.find('meta', {'property':'og:title'})
                title = (title_elem.get_text(strip=True) 
                         if hasattr(title_elem, 'get_text') 
                         else title_elem.get('content', '').strip())
                logging.warning(f"Fallback title used on {url}: {title}")

            date_meta = ss.find('meta', {'itemprop':'datePublished'})
            date = date_meta.get('datetime', '').strip() if date_meta else ''
            if not date:
                date = ss.select_one('span.date').get_text(strip=True) if ss.select_one('span.date') else ""

            img_meta = ss.find('meta', {'itemprop':'image'})
            image = img_meta.get('content', '').strip() if img_meta else ''
            if not image and ss.select_one('div.detailimage img'):
                image = ss.select_one('div.detailimage img')['src']

            # Extract additional metadata
            tags = [t.get_text(strip=True).lstrip('#') for t in ss.select('div.publication-tags__item')] or None
            section_elem = ss.select_one('div.period-wrapper .section_name a')
            section = section_elem.get_text(strip=True) if section_elem else None
            author_elem = ss.select_one('.publication__author-bold-text')
            author = author_elem.get_text(strip=True) if author_elem else None
            author_pos_elem = ss.select_one('.publication__position')
            author_position = author_pos_elem.get_text(strip=True) if author_pos_elem else None

            # content div
            content_div = ss.select_one('div.detail.blog .content') or ss.select_one('div.detail.blog')
            # remove image tags from content
            if content_div:
                for img in content_div.find_all('img'):
                    img.decompose()
            html_body = content_div.decode_contents() if content_div else ''
            markdown = md(html_body)
            # normalize and sanitize
            url = normalize_url(url)
            if '/upravlenie-nashey-firmoy-unf/' not in url:
                continue
            body_md = markdown
            safe_slug = sanitize(title)
            subcat = tags[0] if tags else ""
            save_md(Path(OUTPUT_DIR), safe_slug, title, date, url, body_md, subcat)
        except Exception as e:
            logging.error(f"Error processing {url}: {e}")
    page += 1

#convert_html_to_md.py

"""
usage:
    python html2md.py --in page.html --out out_dir/ [--category faq]
"""
from __future__ import annotations
import argparse, hashlib, re, yaml, pathlib, datetime as dt
import urllib.parse
from bs4 import BeautifulSoup
from readability import Document  # pip install readability-lxml
import html2text
from datetime import datetime, timezone
import sys
import os

"""
Как запустить конвертацию

OUT_DIR="data/Компетенции/Универсальные механизмы/md"

for f in "data/Компетенции/Универсальные механизмы"/*.html; do
  python convert_html_to_md.py \
      --in "$f" \
      --out "$OUT_DIR" \
      --category skills \
      --section "Универсальные механизмы"
done
"""

# ---------- helpers -------------------------------------------------
def read_html(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1251", errors="ignore")

def extract_main(html: str) -> tuple[str, str]:
    doc = Document(html)
    title = doc.short_title()
    main_html = doc.summary()        # статья без хедеров/меню
    return title, main_html

def to_markdown(html: str) -> str:
    h = html2text.HTML2Text()
    h.body_width = 0                # не ломаем строки
    h.ignore_links = False
    h.ignore_images = True            # пропускаем теги <img>
    return h.handle(html)

def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]

def extract_saved_url(html: str) -> str | None:
    """
    Ищет в исходном HTML комментарий браузера вида
    <!-- saved from url=(0041)https://example.com/page -->
    и возвращает найденный URL, либо None.
    """
    m = re.search(r"<!--\s*saved from url=\(\d+\)\s*(https?://[^ >]+)\s*-->", html, re.IGNORECASE)
    return m.group(1) if m else None

# ---------- CLI ------------------------------------------------------


# ---------- site‑specific enrichment rules ---------------------------
# Each entry maps a domain suffix to one or more callables (soup, url, meta) -> None
def _infostart_ru(soup, url, meta):
    # example: save article id if present
    m = re.search(r"/articles/(\\d+)", url or "")
    if m:
        meta["article_id"] = m.group(1)

def _its_1c_ru(soup, url, meta):
    # put page <title> into question field if empty
    if not meta.get("question"):
        if (tag := soup.find("title")):
            meta["question"] = tag.text.strip()

def _buhexpert8_ru(soup, url, meta):
    # extract URL from og:url if not found
    if not url and (tag := soup.find("meta", property="og:url")):
        meta["url"] = tag["content"]
    # extract published date from article:published_time
    if (tag := soup.find("meta", property="article:published_time")) and tag.get("content"):
        dt_str = tag["content"]
        meta["date"] = dt_str.split("T")[0]
    # extract section name from article:section
    if (tag := soup.find("meta", property="article:section")) and tag.get("content"):
        meta["section_1c"] = tag["content"]

SITE_RULES: dict[str, list[callable]] = {
    "infostart.ru": [_infostart_ru],
    "its.1c.ru": [_its_1c_ru],
    "buhexpert8.ru": [_buhexpert8_ru],
}
# ---------------------------------------------------------------------

p = argparse.ArgumentParser()
p.add_argument("--in", dest="src", required=True)
p.add_argument("--out", dest="out_dir", required=False)
p.add_argument("--category", default="misc")
p.add_argument("--section", default="")
args = p.parse_args()

DEPARTMENT = os.getenv("DEPARTMENT")

base_dir = pathlib.Path("data")
if DEPARTMENT:
    base_dir = base_dir / DEPARTMENT

if args.out_dir:
    out_dir = pathlib.Path(args.out_dir)
else:
    src_path = pathlib.Path(args.src).resolve()
    try:
        parent_relative = src_path.parent.relative_to(base_dir)
    except ValueError:
        parent_relative = src_path.parent.name
    out_dir = base_dir / parent_relative / "md"

out_dir.mkdir(parents=True, exist_ok=True)
print(f"Сохраняем результат в {out_dir}")

src_path   = pathlib.Path(args.src)

raw_html   = read_html(src_path)
saved_url  = extract_saved_url(raw_html)
title, main_html = extract_main(raw_html)
soup = BeautifulSoup(raw_html, "html.parser")
md_body    = to_markdown(main_html)


# Формируем человекочитаемый slug из заголовка:
# 1. Удаляем все символы кроме букв/цифр/пробелов/‑.
# 2. Пробелы → «-».
# 3. Транслитерируем кириллицу, чтобы не было проблем на разных ОС.
try:
    from unidecode import unidecode            # pip install unidecode
    translit = unidecode(title)
except Exception:
    translit = title

slug = re.sub(r"[^\w\- ]+", "", translit).strip().replace(" ", "-").lower()
fname = f"{slug}.md" if slug else f"{sha1(title)}.md"

# ----- prepare front‑matter ------------------------------------------
imported_ts = dt.datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
front = {
    "category"    : args.category,
    "section_1c"  : args.section,
    "source_file" : src_path.name,
    "url"         : saved_url,
    "question"    : title,
    "imported"    : imported_ts,
    "date"        : imported_ts[:10],   # YYYY‑MM‑DD
}

# apply site‑specific rules if any
if saved_url:
    host = urllib.parse.urlparse(saved_url).hostname or ""
    for domain, funcs in SITE_RULES.items():
        if host.endswith(domain):
            for fn in funcs:
                try:
                    fn(soup, saved_url, front)
                except Exception:
                    pass

# Fallback: use <link rel="canonical"> if URL still unset
if not front.get("url"):
    if (canonical_tag := soup.find("link", rel="canonical")) and canonical_tag.get("href"):
        front["url"] = canonical_tag["href"]

fm = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip()

out_path = out_dir / fname
# ↪️ Skip conversion if output .md exists and is up‑to‑date
if out_path.exists() and out_path.stat().st_mtime >= src_path.stat().st_mtime:
    print(f"↩️ {out_path.name} актуален, пропускаем")
    sys.exit(0)
out_path.write_text(f"---\n{fm}\n---\n\n# {title}\n\n{md_body}", encoding="utf-8")
print("✓ saved", out_path)
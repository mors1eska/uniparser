# Скрипт: parse_unf_book.py
# Цель: спарсить книгу с https://its.1c.ru/db/unfdoc и сохранить в формате Markdown для RAG

import re
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://its.1c.ru"
START_URL = f"{BASE_URL}/db/unfdoc"
USERNAME = "mors"
PASSWORD = "morser"
OUTPUT_DIR = Path("data/unfdoc/md")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_content(page, url):
    try:
        page.goto(url, timeout=20000)
        page.wait_for_selector("iframe[name=w_metadata_doc_frame]", timeout=20000)
        iframe = page.frame(name="w_metadata_doc_frame")
        if not iframe:
            print(f"❌ iframe не найден на странице: {url}")
            return None

        html = iframe.content()
        soup = BeautifulSoup(html, "html.parser")
        content_div = soup.select_one("div.doc-content") or soup.select_one("div#content") or soup.body
        if not content_div:
            print(f"⚠️ Контент не найден: {url}")
            debug_name = "debug_failed_" + sanitize_filename(url.split("/")[-1]) + ".html"
            with open(debug_name, "w", encoding="utf-8") as f:
                f.write(html)
            return None

        return content_div.get_text("\n", strip=True).strip()
    except Exception as e:
        print(f"❌ Ошибка при загрузке {url}: {e}")
        import traceback; traceback.print_exc()
        return None


def sanitize_filename(text):
    text = text.lower()
    text = re.sub(r"[^\w\- ]", "", text)
    text = text.replace(" ", "-")
    return text[:100]


def save_as_md(title, url, content):
    # Преобразуем название в структуру "глава-<номер>-<подномер>-название"
    chapter_match = re.match(r"^(\d+)(?:\.(\d+))?\.\s*(.+)$", title)
    if chapter_match:
        main, sub, name = chapter_match.groups()
        if sub:
            filename = f"глава-{main}-{sub}-{sanitize_filename(name)}.md"
        else:
            filename = f"глава-{main}-{sanitize_filename(name)}.md"
    elif title.lower().startswith("глава "):
        filename = f"{sanitize_filename(title)}.md"
    else:
        filename = f"{sanitize_filename(title)}.md"

    out_path = OUTPUT_DIR / filename
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"""---
category: SD
section_1c: УНФ
question: {title}
url: {url}
---

{content.strip()}
""")
    print("✅", filename)


def extract_content_from_iframe(iframe):
    html = iframe.content()
    soup = BeautifulSoup(html, "html.parser")
    div = soup.select_one("div.doc-content, div#content") or soup.body
    return div.get_text("\n", strip=True) if div else ""


def get_doc_iframe(page):
    page.wait_for_selector("iframe[name=w_metadata_doc_frame]", timeout=10000)
    return page.frame(name="w_metadata_doc_frame")


def safe_goto(page, url):
    """Переходит по url и возвращает актуальный iframe с документом."""
    page.goto(url, timeout=20000)
    return get_doc_iframe(page)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context()
        page = context.new_page()

        # 1. Защита + логин
        page.goto("https://its.1c.ru/")
        input("⏳ Пройди защиту DDoS и нажми Enter...")
        print("🧭 Текущий URL:", page.url)
        with open("debug_page_content.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        # 2. Переход в документацию УНФ
        page.goto(START_URL)
        page.wait_for_timeout(3000)  # дать время на прогрузку авторизации
        print("🧭 После логина:", page.url)

        # DEBUG: сохраняем HTML
        with open("debug_after_login.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        print("📦 Доступные фреймы:")
        for f in page.frames:
            print("  →", f.name, f.url)
        try:
            page.wait_for_selector("iframe[name=w_metadata_doc_frame]", timeout=10000)
            iframe = page.frame(name="w_metadata_doc_frame")
            if not iframe:
                print("❌ iframe w_metadata_doc_frame не найден")
                return
            print(f"🧭 iframe загружен: {iframe.url}")
            with open("debug_iframe_index.html", "w", encoding="utf-8") as f:
                f.write(iframe.content())
            soup = BeautifulSoup(iframe.content(), "html.parser")
            ul_count = len(soup.select("ul"))
            li_count = len(soup.select("li"))
            a_count = len(soup.select("a"))
            print(f"📊 Элементы в iframe: ul={ul_count}, li={li_count}, a={a_count}")

            print("📥 Проверим HTML до раскрытия папок...")
            with open("debug_iframe_before_expand.html", "w", encoding="utf-8") as f:
                f.write(iframe.content())

            iframe.wait_for_selector("a[href*='content']", timeout=10000)
            print("🔍 Ищем все ссылки с 'content'...")
            toc_links = iframe.evaluate("""
                Array.from(document.querySelectorAll('a[href*="/db/unfdoc/content/"]'))
                     .map(a => ({title: a.textContent.trim(), url: a.href}))
            """)

            links = [link for link in toc_links if link["title"] and link["url"] and not link["url"].startswith("#")]

            with open("debug_iframe_links.html", "w", encoding="utf-8") as f:
                f.write(iframe.content())
        except Exception as e:
            print(f"❌ Ошибка ожидания ссылок в оглавлении: {e}")
            with open("debug_unfdoc_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            return

        print(f"📋 Всего ссылок в оглавлении: {len(links)}")

        print(f"🧩 Всего ссылок для обработки: {len(links)}")

        saved_urls = set()

        for i, link in enumerate(links):
            print(f"🔹 [{i+1}/{len(links)}] {link['title']} — {link['url']}")
            iframe = safe_goto(page, link["url"])
            content = extract_content_from_iframe(iframe)
            if content:
                save_as_md(link["title"], link["url"], content)
                saved_urls.add(link["url"])

            # --- ищем подглавы (все ссылки content/ внутри iframe) ---
            try:
                sub_links = iframe.evaluate("""
                    Array.from(document.querySelectorAll('a[href*="/db/unfdoc/content/"]'))
                         .map(a => ({ title: a.textContent.trim(), url: a.href }));
                """)
                print(f"🔖 Под-ссылок найдено: {len(sub_links)}")

                for sub in sub_links:
                    if (not sub["title"] or not sub["url"]
                        or sub["url"] == link["url"]
                        or sub["url"] in saved_urls):
                        continue

                    print(f"📁 Подглава: {sub['title']} — {sub['url']}")
                    iframe = safe_goto(page, sub["url"])
                    sub_content = extract_content_from_iframe(iframe)
                    if sub_content:
                        save_as_md(sub["title"], sub["url"], sub_content)
                        saved_urls.add(sub["url"])
            except Exception as e:
                print(f"⚠️ Ошибка при поиске подглав: {e}")

        print("🏁 Парсинг завершён. Закрываем браузер.")
        browser.close()


if __name__ == "__main__":
    main()

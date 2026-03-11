import argparse
import csv
from typing import List, Dict

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def login_if_needed(page, base_url: str, username: str | None, password: str | None) -> bool:
    if not username or not password:
        return False

    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    # Avoid flaky clickability checks in headless mode: submit the form directly.
    page.locator("form").first.evaluate("form => form.submit()")
    page.wait_for_load_state("domcontentloaded")

    try:
        page.wait_for_selector("a[href='/logout']", timeout=5000)
        return True
    except PlaywrightTimeoutError:
        return False


def parse_quotes(page, base_url: str, max_pages: int) -> List[Dict[str, str]]:
    page.goto(base_url, wait_until="domcontentloaded")

    items: List[Dict[str, str]] = []
    current_page = 1

    while True:
        quote_blocks = page.locator("div.quote")
        count = quote_blocks.count()

        for i in range(count):
            block = quote_blocks.nth(i)
            text = block.locator("span.text").inner_text().strip()
            author = block.locator("small.author").inner_text().strip()
            tags = ", ".join(block.locator("div.tags a.tag").all_inner_texts())
            author_path = block.locator('a[href^="/author/"]').first.get_attribute("href") or ""
            author_url = f"{base_url}{author_path}" if author_path.startswith("/") else author_path

            items.append(
                {
                    "quote": text,
                    "author": author,
                    "tags": tags,
                    "author_url": author_url,
                    "page": str(current_page),
                }
            )

        if current_page >= max_pages:
            break

        next_btn = page.locator("li.next a")
        if next_btn.count() == 0:
            break

        next_href = next_btn.first.get_attribute("href")
        if not next_href:
            break

        next_url = f"{base_url}{next_href}" if next_href.startswith("/") else next_href
        page.goto(next_url, wait_until="domcontentloaded")
        current_page += 1

    return items


def save_to_csv(data: List[Dict[str, str]], output_path: str) -> None:
    fieldnames = ["quote", "author", "tags", "author_url", "page"]
    with open(output_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Playwright parser with pagination and optional authorization."
    )
    parser.add_argument(
        "--base-url",
        default="https://quotes.toscrape.com",
        help="Base URL of the website to parse.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum number of pages to parse.",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Username for optional login.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password for optional login.",
    )
    parser.add_argument(
        "--output",
        default="quotes.csv",
        help="Path to output CSV file.",
    )
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        logged_in = login_if_needed(page, args.base_url, args.username, args.password)
        data = parse_quotes(page, args.base_url, args.max_pages)
        save_to_csv(data, args.output)

        browser.close()

    print(f"Saved {len(data)} rows to {args.output}.")
    print(f"Login status: {'success' if logged_in else 'skipped/failed'}.")


if __name__ == "__main__":
    main()

#python main.py --max-pages 3 --output output.csv
#python main.py --username admin --password admin --max-pages 3 --output output.csv
#python main.py --base-url "https://example.com" --max-pages 3 --output output.csv

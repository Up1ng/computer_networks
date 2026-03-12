import argparse
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import psycopg
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/parser_db"
DEFAULT_MAX_PAGES = 5
DEFAULT_DB_CONNECT_TIMEOUT = 5


def make_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("bad url")
    return f"{parsed.scheme}://{parsed.netloc}"


def login_if_needed(page, origin_url: str, username: str | None, password: str | None) -> bool:
    if not username or not password:
        return False

    page.goto(f"{origin_url}/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.locator("form").first.evaluate("form => form.submit()")
    page.wait_for_load_state("domcontentloaded")

    try:
        page.wait_for_selector("a[href='/logout']", timeout=5000)
        return True
    except PlaywrightTimeoutError:
        return False


def parse_quotes(page, start_url: str, max_pages: int) -> list[dict[str, str]]:
    origin_url = origin_from_url(start_url)
    page.goto(start_url, wait_until="domcontentloaded")

    items: list[dict[str, str]] = []
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
            author_url = f"{origin_url}{author_path}" if author_path.startswith("/") else author_path

            items.append(
                {
                    "quote": text,
                    "author": author,
                    "tags": tags,
                    "author_url": author_url,
                    "page": str(current_page),
                    "source_url": page.url,
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

        next_url = f"{origin_url}{next_href}" if next_href.startswith("/") else next_href
        page.goto(next_url, wait_until="domcontentloaded")
        current_page += 1

    return items


def run_parser(
    target_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    username: str | None = None,
    password: str | None = None,
) -> tuple[list[dict[str, str]], bool]:
    origin_url = origin_from_url(target_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        logged_in = login_if_needed(page, origin_url, username, password)
        data = parse_quotes(page, target_url, max_pages)

        browser.close()

    return data, logged_in


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


@contextmanager
def db_connection():
    connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", str(DEFAULT_DB_CONNECT_TIMEOUT)))
    with psycopg.connect(get_database_url(), connect_timeout=connect_timeout) as connection:
        yield connection


def init_db() -> None:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS quotes (
                    id BIGSERIAL PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    author TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    author_url TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    parsed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        connection.commit()


def save_quotes(data: list[dict[str, str]]) -> int:
    if not data:
        return 0

    parsed_at = datetime.now(timezone.utc)
    rows = [
        (
            item["source_url"],
            item["quote"],
            item["author"],
            item["tags"],
            item["author_url"],
            int(item["page"]),
            parsed_at,
        )
        for item in data
    ]

    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO quotes (source_url, quote, author, tags, author_url, page, parsed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        connection.commit()

    return len(rows)


def fetch_quotes(limit: int = 100) -> list[dict[str, Any]]:
    with db_connection() as connection:
        with connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, source_url, quote, author, tags, author_url, page, parsed_at
                FROM quotes
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()

    return [
        {
            **row,
            "parsed_at": row["parsed_at"].isoformat(),
        }
        for row in rows
    ]


class ParserApiHandler(BaseHTTPRequestHandler):
    server_version = "ParserApi/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        try:
            if parsed.path == "/parse":
                self.handle_parse(parsed.query)
                return
            if parsed.path == "/quotes":
                self.handle_quotes(parsed.query)
                return
            if parsed.path == "/health":
                self.send_json(HTTPStatus.OK, {"status": "ok"})
                return

            self.send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "server error"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def handle_parse(self, query: str) -> None:
        params = parse_qs(query)
        target_url = params.get("url", [None])[0]
        max_pages_raw = params.get("max_pages", [str(DEFAULT_MAX_PAGES)])[0]
        username = params.get("username", [None])[0]
        password = params.get("password", [None])[0]

        if not target_url:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
            return

        try:
            max_pages = max(1, int(max_pages_raw))
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
            return

        data, logged_in = run_parser(
            target_url=target_url,
            max_pages=max_pages,
            username=username,
            password=password,
        )
        inserted_rows = save_quotes(data)

        self.send_json(
            HTTPStatus.OK,
            {
                "status": "ok",
                "requested_url": target_url,
                "logged_in": logged_in,
                "parsed_rows": len(data),
                "inserted_rows": inserted_rows,
            },
        )

    def handle_quotes(self, query: str) -> None:
        params = parse_qs(query)
        limit_raw = params.get("limit", ["100"])[0]

        try:
            limit = max(1, min(1000, int(limit_raw)))
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
            return

        rows = fetch_quotes(limit=limit)
        self.send_json(HTTPStatus.OK, {"items": rows, "count": len(rows)})

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = make_json_bytes(payload)
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str, port: int) -> None:
    try:
        init_db()
    except Exception as exc:
        raise RuntimeError("db error") from exc
    server = ThreadingHTTPServer((host, port), ParserApiHandler)
    print(f"API listening on http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()



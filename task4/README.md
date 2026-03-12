.\start_api.ps1

curl.exe "http://127.0.0.1:8000/parse?url=https://quotes.toscrape.com&max_pages=1"

curl.exe "http://127.0.0.1:8000/quotes?limit=10"

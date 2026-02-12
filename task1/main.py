import csv

from icmplib import ping


sites = [
    "google.com", "youtube.com", "wikipedia.org", "openai.com", "github.com",
    "store.steampowered.com", "apple.com", "wildberries.com", "ozon.com", "yandex.ru"
]

output_file = "results.csv"


def check_host(address):
    response = ping(address, count=10, timeout=2, interval=0.2, privileged=False)

    jitter_value = None
    if response.max_rtt is not None and response.min_rtt is not None:
        jitter_value = response.max_rtt - response.min_rtt

    return [
        address,
        response.is_alive,
        response.avg_rtt,
        response.packet_loss,
        jitter_value
    ]


def save_results(domains, filename):
    with open(filename, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)

        writer.writerow([
            "Domain",
            "Is Alive",
            "Average RTT (ms)",
            "Packet Loss (%)",
            "Jitter (ms)"
        ])

        for domain in domains:
            writer.writerow(check_host(domain))


save_results(sites, output_file)


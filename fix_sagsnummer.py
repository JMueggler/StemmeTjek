"""
fix_sagsnummer.py – Opdaterer sagsnummer-feltet i sag-tabellen
ved at hente det korrekte 'nummer'-felt fra Folketingets API.
Kør: python fix_sagsnummer.py
"""

import requests
import time
from database import get_conn

BASE_URL = "https://oda.ft.dk/api/"
DELAY = 0.3


def fetch_json(url, params=None, retries=5):
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [FEJL] {e} – forsøger igen om {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Kunne ikke hente {url}")


def main():
    conn = get_conn()

    # Hent alle sag-IDs fra databasen
    sag_ids = [row[0] for row in conn.execute("SELECT id FROM sag").fetchall()]
    print(f"Opdaterer sagsnummer for {len(sag_ids)} sager...")

    updated = 0
    batch_size = 20

    for i in range(0, len(sag_ids), batch_size):
        batch = sag_ids[i:i + batch_size]
        filter_str = " or ".join(f"id eq {x}" for x in batch)

        url = BASE_URL + "Sag"
        params = {"$filter": filter_str, "$top": batch_size}
        data = fetch_json(url, params)
        records = data.get("value", [])

        for s in records:
            nummer = s.get("nummer")  # Det korrekte felt (fx "L 111")
            conn.execute(
                "UPDATE sag SET sagsnummer=? WHERE id=?",
                (nummer, s["id"])
            )
            if nummer:
                updated += 1

        print(f"  {min(i + batch_size, len(sag_ids))}/{len(sag_ids)}", end="\r", flush=True)
        time.sleep(DELAY)

    conn.commit()
    print(f"\nFærdig! {updated}/{len(sag_ids)} sager har fået sagsnummer.")

    # Vis eksempler
    samples = conn.execute(
        "SELECT id, sagsnummer, titelkort FROM sag WHERE sagsnummer IS NOT NULL LIMIT 5"
    ).fetchall()
    print("\nEksempler:")
    for s in samples:
        print(f"  {dict(s)}")

    conn.close()


if __name__ == "__main__":
    main()

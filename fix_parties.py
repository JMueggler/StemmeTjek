"""
fix_parties.py – Opdaterer politikeres parti-info ved at parse biografi-XML fra API.
Kør: python fix_parties.py
"""

import requests
import time
import xml.etree.ElementTree as ET
from database import get_conn

BASE_URL = "https://oda.ft.dk/api/"
DELAY = 0.4


def fetch_json(url, params=None, retries=5):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [FEJL] {e} – retry om {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Fejl ved {url}")


def parse_party(biografi):
    """Udtræk partyShortname og party fra biografi XML."""
    if not biografi:
        return None, None
    try:
        root = ET.fromstring(biografi)
        short = root.findtext("partyShortname")
        full = root.findtext("party")
        return short, full
    except ET.ParseError:
        return None, None


def main():
    print("=== Opdaterer parti-info fra biografi XML ===")
    conn = get_conn()

    # Hent alle politikere fra API med biografi
    print("Henter politikere fra API...")
    url = BASE_URL + "Aktør"
    params = {"$inlinecount": "allpages", "$skip": 0, "$top": 100,
              "$filter": "typeid eq 5"}

    all_aktører = []
    while True:
        data = fetch_json(url, params)
        records = data.get("value", [])
        if not records:
            break
        all_aktører.extend(records)
        total = int(data.get("odata.count", 0))
        params["$skip"] += len(records)
        print(f"  {len(all_aktører)}/{total}", end="\r", flush=True)
        if params["$skip"] >= total:
            break
        time.sleep(DELAY)
    print()
    print(f"  {len(all_aktører)} politikere hentet.")

    # Byg parti-tabel fra unikke partier
    partier = {}  # forkortelse -> fuldt navn
    for a in all_aktører:
        short, full = parse_party(a.get("biografi"))
        if short and short not in partier:
            partier[short] = full or short

    print(f"  {len(partier)} unikke partier fundet: {', '.join(sorted(partier.keys()))}")

    # Opdater/indsæt partier
    for fork, navn in partier.items():
        existing = conn.execute(
            "SELECT id FROM parti WHERE forkortelse=? AND navn=?", (fork, navn)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT OR IGNORE INTO parti (forkortelse, navn) VALUES (?, ?)",
                (fork, navn)
            )
    conn.commit()

    # Opdater politikere
    updated = 0
    for a in all_aktører:
        short, full = parse_party(a.get("biografi"))
        if short:
            parti_row = conn.execute(
                "SELECT id FROM parti WHERE forkortelse=? AND navn=?", (short, full or short)
            ).fetchone()
            if not parti_row:
                parti_row = conn.execute(
                    "SELECT id FROM parti WHERE forkortelse=?", (short,)
                ).fetchone()
            parti_id = parti_row["id"] if parti_row else None

            conn.execute(
                "UPDATE politiker SET parti_forkortelse=?, parti_id=? WHERE id=?",
                (short, parti_id, a["id"])
            )
            updated += 1

    conn.commit()
    print(f"  {updated} politikere opdateret med korrekt parti-info.")

    # Vis resultat
    print("\nKontrol – sample politikere med parti:")
    rows = conn.execute("""
        SELECT p.fornavn, p.efternavn, p.parti_forkortelse, pa.navn as parti_navn
        FROM politiker p
        LEFT JOIN parti pa ON p.parti_id = pa.id
        WHERE p.parti_forkortelse IS NOT NULL
        ORDER BY RANDOM() LIMIT 10
    """).fetchall()
    for r in rows:
        print(f"  {r['fornavn']} {r['efternavn']} – {r['parti_forkortelse']} ({r['parti_navn']})")

    conn.close()
    print("\nFærdig! Genstart app.py for at se ændringerne.")


if __name__ == "__main__":
    main()

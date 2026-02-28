"""
fetch_data.py – Henter afstemningsdata fra Folketingets Åbne Data API.
Kun data fra den nuværende valgperiode (siden 1. november 2022).
Kør: python fetch_data.py
"""

import requests
import time
from database import create_schema, get_conn, insert_or_replace, get_progress, set_progress

BASE_URL = "https://oda.ft.dk/api/"
DELAY = 0.4
PERIODE_START = "2022-11-01"


# ---------------------------------------------------------------------------
# HTTP-hjælper med retry
# ---------------------------------------------------------------------------

def fetch_json(url, params=None, retries=5):
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [FEJL] {e} – forsøger igen om {wait}s ({attempt+1}/{retries})")
            time.sleep(wait)
    raise RuntimeError(f"Kunne ikke hente {url} efter {retries} forsøg")


def fetch_all(endpoint, filter_str=None, extra_params=None):
    url = BASE_URL + endpoint
    params = {"$inlinecount": "allpages", "$skip": 0, "$top": 100}
    if filter_str:
        params["$filter"] = filter_str
    if extra_params:
        params.update(extra_params)

    all_records = []
    while True:
        data = fetch_json(url, params)
        records = data.get("value", [])
        if not records:
            break
        all_records.extend(records)
        total = int(data.get("odata.count", 0))
        params["$skip"] += len(records)
        print(f"  {len(all_records)}/{total}", end="\r", flush=True)
        if params["$skip"] >= total:
            break
        time.sleep(DELAY)
    print()
    return all_records


def fetch_by_ids(endpoint, ids, batch_size=20):
    all_records = []
    ids = list(ids)
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i+batch_size]
        filter_str = " or ".join(f"id eq {x}" for x in batch)
        records = fetch_all(endpoint, filter_str=filter_str)
        all_records.extend(records)
        print(f"  {min(i+batch_size, len(ids))}/{len(ids)}", end="\r", flush=True)
        time.sleep(DELAY)
    print()
    return all_records


# ---------------------------------------------------------------------------
# Trin 1: Lookup-tabeller
# ---------------------------------------------------------------------------

def fetch_lookup_tables(conn):
    print("\n[1/6] Henter lookup-tabeller...")
    afstemningstyper = fetch_all("Afstemningstype")
    insert_or_replace("afstemningstype", [{"id": r["id"], "type": r["type"]} for r in afstemningstyper], conn)
    stemmetyper = fetch_all("Stemmetype")
    insert_or_replace("stemmetype", [{"id": r["id"], "type": r["type"]} for r in stemmetyper], conn)
    conn.commit()
    print(f"  {len(afstemningstyper)} afstemningstyper, {len(stemmetyper)} stemmetyper gemt.")


# ---------------------------------------------------------------------------
# Trin 2: Politikere der har været aktive siden valgperiodestart
# ---------------------------------------------------------------------------

def fetch_politicians(conn):
    print(f"\n[2/6] Henter politikere (typeid=5)...")

    # Alle personer – gruppenavnkort på Aktør giver parti direkte
    aktører = fetch_all("Aktør", filter_str="typeid eq 5")
    print(f"  {len(aktører)} aktører fundet.")

    # Parti-grupper (typeid=9) for fulde parti-navne
    print("  Henter parti-grupper...")
    grupper = fetch_all("Aktør", filter_str="typeid eq 9")
    # Map: forkortelse -> gruppe
    fork_map = {g.get("gruppenavnkort"): g for g in grupper if g.get("gruppenavnkort")}

    insert_or_replace("parti", [{
        "id": g["id"],
        "forkortelse": g.get("gruppenavnkort") or str(g["id"]),
        "navn": (g.get("navn") or f"{g.get('fornavn','')} {g.get('efternavn','')}").strip()
    } for g in grupper], conn)

    # Brug gruppenavnkort direkte fra Aktør
    politiker_rows = []
    for a in aktører:
        fork = a.get("gruppenavnkort")
        parti_id = None
        if fork and fork in fork_map:
            parti_id = fork_map[fork]["id"]
        politiker_rows.append({
            "id": a["id"],
            "fornavn": a.get("fornavn") or "",
            "efternavn": a.get("efternavn") or "",
            "parti_forkortelse": fork,
            "parti_id": parti_id,
        })

    insert_or_replace("politiker", politiker_rows, conn)
    conn.commit()
    print(f"  {len(politiker_rows)} politikere gemt.")


# ---------------------------------------------------------------------------
# Trin 3: Afstemninger fra den nuværende periode
# ---------------------------------------------------------------------------

def fetch_afstemninger(conn):
    print("\n[3/6] Henter afstemninger og tilknyttede sager...")

    # Hent alle afstemninger (~4.000 records, hurtigt)
    print("  Henter alle afstemninger...")
    afstemninger = fetch_all("Afstemning")
    print(f"  {len(afstemninger)} afstemninger fundet i alt.")

    # Hent sagstrin kun for disse afstemninger
    sagstrin_ids = list({a["sagstrinid"] for a in afstemninger if a.get("sagstrinid")})
    print(f"  Henter {len(sagstrin_ids)} sagstrin...")
    sagstrin_records = fetch_by_ids("Sagstrin", sagstrin_ids, batch_size=20)

    # Filtrer: kun sagstrin fra den nuværende periode
    sagstrin_i_periode = {
        s["id"]: s for s in sagstrin_records
        if s.get("dato") and s["dato"][:10] >= PERIODE_START
    }
    print(f"  {len(sagstrin_i_periode)} sagstrin fra {PERIODE_START}+.")

    # Filtrer afstemninger til kun dem i perioden
    afstemninger_i_periode = [
        a for a in afstemninger
        if a.get("sagstrinid") and a["sagstrinid"] in sagstrin_i_periode
    ]
    print(f"  {len(afstemninger_i_periode)} afstemninger i den nuværende periode.")

    # Hent sager
    sag_ids = list({sagstrin_i_periode[a["sagstrinid"]]["sagid"]
                    for a in afstemninger_i_periode
                    if sagstrin_i_periode[a["sagstrinid"]].get("sagid")})
    print(f"  Henter {len(sag_ids)} sager...")
    sager = fetch_by_ids("Sag", sag_ids, batch_size=20)
    insert_or_replace("sag", [{
        "id": s["id"],
        "titel": s.get("titel"),
        "titelkort": s.get("titelkort"),
        "resume": s.get("resume"),
        "sagsnummer": s.get("nummer"),  # 'nummer' er det rigtige felt (fx "L 111")
        "typeid": s.get("typeid"),
        "periodeid": s.get("periodeid"),
    } for s in sager], conn)
    print(f"  {len(sager)} sager gemt.")

    # Gem afstemninger
    rows = []
    for a in afstemninger_i_periode:
        strin = sagstrin_i_periode.get(a["sagstrinid"], {})
        rows.append({
            "id": a["id"],
            "nummer": a.get("nummer"),
            "konklusion": a.get("konklusion"),
            "vedtaget": 1 if a.get("vedtaget") else 0,
            "kommentar": a.get("kommentar"),
            "dato": strin.get("dato") or a.get("opdateringsdato"),
            "typeid": a.get("typeid"),
            "sag_id": strin.get("sagid"),
        })
    insert_or_replace("afstemning", rows, conn)
    conn.commit()
    print(f"  {len(rows)} afstemninger gemt.")
    return {a["id"] for a in afstemninger_i_periode}


# ---------------------------------------------------------------------------
# Trin 4: Kategorier
# ---------------------------------------------------------------------------

KATEGORIER = [
    "Miljø og klima", "Landbrug og fødevarer", "Vand og vandmiljø",
    "Sundhed", "Forsvar og sikkerhed", "Skat og økonomi",
    "Social- og familiepolitik", "Retspolitik", "Udlændinge og integration",
    "Arbejdsmarked", "Uddannelse", "Transport og infrastruktur",
    "Digitalisering", "Erhverv og handel", "Kultur og medier",
    "Demokrati og institutioner", "Udenrigspolitik", "Bolig", "Energi",
]

def insert_kategorier(conn):
    print("\n[4/6] Indsætter kategorier...")
    for navn in KATEGORIER:
        conn.execute("INSERT OR IGNORE INTO kategori (navn) VALUES (?)", (navn,))
    conn.commit()
    print(f"  {len(KATEGORIER)} kategorier indsat.")


# ---------------------------------------------------------------------------
# Trin 5: Stemmer – kun for afstemninger i perioden
# ---------------------------------------------------------------------------

def fetch_stemmer(conn, afstemning_ids):
    print(f"\n[5/6] Henter stemmer for {len(afstemning_ids):,} afstemninger...")
    print("  Dette tager 20-40 min. Scriptet kan genstartes hvis det afbrydes.")

    # Find hvilke afstemninger der allerede er hentet
    done_ids = set(row[0] for row in conn.execute(
        "SELECT DISTINCT afstemning_id FROM stemme"
    ).fetchall())

    remaining = sorted(afstemning_ids - done_ids)
    print(f"  {len(done_ids):,} allerede hentet, {len(remaining):,} mangler.")

    total_inserted = 0
    for i, afstemning_id in enumerate(remaining):
        # Paginer stemmer per afstemning (API returnerer maks 100 per request)
        records = []
        skip = 0
        while True:
            url = BASE_URL + "Stemme"
            params = {"$filter": f"afstemningid eq {afstemning_id}", "$top": 100, "$skip": skip}
            data = fetch_json(url, params)
            page = data.get("value", [])
            if not page:
                break
            records.extend(page)
            skip += len(page)
            if len(page) < 100:
                break

        if records:
            rows = [{
                "id": r["id"],
                "afstemning_id": r.get("afstemningid"),
                "politiker_id": r.get("aktørid"),
                "stemmetype_id": r.get("typeid"),
            } for r in records]
            insert_or_replace("stemme", rows, conn)
            total_inserted += len(rows)

        if (i + 1) % 100 == 0:
            conn.commit()
            print(f"  {i+1:,}/{len(remaining):,} afstemninger behandlet, {total_inserted:,} stemmer gemt...", flush=True)

        time.sleep(DELAY)

    conn.commit()
    print(f"  Færdig! {total_inserted:,} stemmer gemt.")


# ---------------------------------------------------------------------------
# Trin 6: Oversigt
# ---------------------------------------------------------------------------

def print_summary(conn):
    print("\n[6/6] Database-oversigt:")
    for tabel in ["afstemningstype", "stemmetype", "parti", "politiker", "sag", "kategori", "afstemning", "stemme"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]
        print(f"  {tabel}: {count:,} rækker")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"=== Folketinget Data-hentning (periode fra {PERIODE_START}) ===")
    create_schema()
    conn = get_conn()

    step = get_progress("last_completed_step")
    step = int(step) if step else 0

    try:
        if step < 1:
            fetch_lookup_tables(conn)
            set_progress("last_completed_step", 1, conn)
            conn.commit()

        if step < 2:
            fetch_politicians(conn)
            set_progress("last_completed_step", 2, conn)
            conn.commit()

        afstemning_ids = None
        if step < 3:
            afstemning_ids = fetch_afstemninger(conn)
            set_progress("last_completed_step", 3, conn)
            conn.commit()

        if step < 4:
            insert_kategorier(conn)
            set_progress("last_completed_step", 4, conn)
            conn.commit()

        if step < 5:
            if afstemning_ids is None:
                afstemning_ids = set(row[0] for row in conn.execute("SELECT id FROM afstemning").fetchall())
            fetch_stemmer(conn, afstemning_ids)
            set_progress("last_completed_step", 5, conn)
            conn.commit()

        print_summary(conn)
        print("\n=== Datahentning fuldført! ===")
        print("Kør nu: python categorize.py")

    except KeyboardInterrupt:
        print("\n\nAfbrudt. Kør scriptet igen for at genoptage.")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""
categorize.py – AI-kategorisering af Folketingssager med Claude API.
Kør: python categorize.py

Kræver ANTHROPIC_API_KEY i .env filen.
"""

import json
import time
import os
from dotenv import load_dotenv
import anthropic
from database import get_conn

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

KATEGORIER = [
    "Miljø og klima",
    "Landbrug og fødevarer",
    "Vand og vandmiljø",
    "Sundhed",
    "Forsvar og sikkerhed",
    "Skat og økonomi",
    "Social- og familiepolitik",
    "Retspolitik",
    "Udlændinge og integration",
    "Arbejdsmarked",
    "Uddannelse",
    "Transport og infrastruktur",
    "Digitalisering",
    "Erhverv og handel",
    "Kultur og medier",
    "Demokrati og institutioner",
    "Udenrigspolitik",
    "Bolig",
    "Energi",
]

BATCH_SIZE = 15


def get_uncategorized_sager(conn):
    """Hent sager der endnu ikke er kategoriseret."""
    rows = conn.execute("""
        SELECT s.id, s.titel, s.titelkort, s.resume
        FROM sag s
        WHERE s.id NOT IN (SELECT DISTINCT sag_id FROM sag_kategori)
        AND (s.titel IS NOT NULL OR s.titelkort IS NOT NULL)
        ORDER BY s.id
    """).fetchall()
    return [dict(r) for r in rows]


def build_prompt(batch):
    kategorier_str = "\n".join(f"- {k}" for k in KATEGORIER)
    sager_str = ""
    for i, s in enumerate(batch, 1):
        titel = s.get("titel") or s.get("titelkort") or "Uden titel"
        resume = s.get("resume") or s.get("titelkort") or ""
        if len(resume) > 300:
            resume = resume[:300] + "..."
        sager_str += f"{i}. [id={s['id']}] {titel}\n   Resume: {resume}\n\n"

    return f"""Du er en ekspert i dansk politik og lovgivning. Kategorisér følgende lovforslag/beslutningsforslag i en eller flere af disse kategorier:

{kategorier_str}

VIGTIGT: Miljø, klima, vand, vandmiljø og landbrug skal have ekstra opmærksomhed — tag altid disse med når de er relevante, også som sekundær kategori.

Svar KUN med et JSON-array (ingen forklaringer, ingen markdown):
[{{"sag_id": X, "kategorier": ["Kategori1", "Kategori2"]}}, ...]

Sager:
{sager_str}"""


def categorize_batch(batch, conn):
    prompt = build_prompt(batch)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        content = message.content[0].text.strip()

        # Rens JSON (fjern eventuel markdown)
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])

        results = json.loads(content)

        # Hent kategori-id'er
        kat_map = {}
        for navn in KATEGORIER:
            row = conn.execute("SELECT id FROM kategori WHERE navn=?", (navn,)).fetchone()
            if row:
                kat_map[navn] = row["id"]

        inserted = 0
        for item in results:
            sag_id = item.get("sag_id")
            kategorier = item.get("kategorier", [])
            for kat_navn in kategorier:
                kat_id = kat_map.get(kat_navn)
                if kat_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO sag_kategori (sag_id, kategori_id) VALUES (?, ?)",
                        (sag_id, kat_id)
                    )
                    inserted += 1

        conn.commit()
        return inserted

    except json.JSONDecodeError as e:
        print(f"  [FEJL] Kunne ikke parse JSON: {e}")
        print(f"  Svar: {content[:200]}")
        return 0
    except Exception as e:
        print(f"  [FEJL] API-fejl: {e}")
        time.sleep(5)
        return 0


def main():
    print("=== AI-kategorisering af Folketingssager ===")
    conn = get_conn()

    sager = get_uncategorized_sager(conn)
    total = len(sager)
    print(f"  {total} sager mangler kategorisering.")

    if total == 0:
        print("  Alle sager er allerede kategoriseret.")
        conn.close()
        return

    done = 0
    for i in range(0, total, BATCH_SIZE):
        batch = sager[i:i+BATCH_SIZE]
        inserted = categorize_batch(batch, conn)
        done += len(batch)
        print(f"  {done}/{total} sager behandlet, {inserted} kategori-tilknytninger gemt.")
        time.sleep(1)  # Rate limiting

    conn.close()
    print("\n=== Kategorisering fuldført! ===")
    print("Kør nu: python app.py")


if __name__ == "__main__":
    main()

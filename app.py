"""
app.py – Flask webapp for Folketingets afstemningsdatabase.
Kør: python app.py
Åbn: http://localhost:5000
"""

import os
import threading
from flask import Flask, jsonify, render_template, request
from database import get_conn, create_schema

app = Flask(__name__)


def _auto_fetch():
    """Kør fetch_data + categorize hvis databasen er tom (første gang på Railway)."""
    try:
        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM afstemning").fetchone()[0]
        conn.close()
        if count == 0:
            print("[init] Database er tom – starter datahentning i baggrunden...")
            import fetch_data
            fetch_data.main()
            print("[init] Datahentning færdig!")
    except Exception as e:
        print(f"[init] Fejl under datahentning: {e}")


create_schema()
threading.Thread(target=_auto_fetch, daemon=True).start()


# ---------------------------------------------------------------------------
# Hjælpefunktioner
# ---------------------------------------------------------------------------

def rows_to_list(rows):
    return [dict(r) for r in rows]


def paginate(query_rows, page, per_page=50):
    total = len(query_rows)
    start = (page - 1) * per_page
    end = start + per_page
    return query_rows[start:end], total


# ---------------------------------------------------------------------------
# Sider
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API: Kategorier
# ---------------------------------------------------------------------------

@app.route("/api/partier")
def api_partier():
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT p.parti_forkortelse AS forkortelse, pa.navn
        FROM politiker p
        JOIN parti pa ON p.parti_id = pa.id
        WHERE p.id IN (SELECT DISTINCT politiker_id FROM stemme)
          AND p.parti_forkortelse IS NOT NULL
        ORDER BY p.parti_forkortelse
    """).fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))


@app.route("/api/kategorier")
def api_kategorier():
    conn = get_conn()
    rows = conn.execute("SELECT id, navn FROM kategori ORDER BY navn").fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))


# ---------------------------------------------------------------------------
# API: Afstemninger
# ---------------------------------------------------------------------------

@app.route("/api/afstemninger")
def api_afstemninger():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    kategori = request.args.get("kategori")
    typeid = request.args.get("type")
    vedtaget = request.args.get("vedtaget")
    q = request.args.get("q", "").strip()
    dato_fra = request.args.get("dato_fra")
    dato_til = request.args.get("dato_til")
    sort_by = request.args.get("sort", "dato")
    sort_dir = request.args.get("dir", "desc")

    allowed_sorts = {"dato", "sagsnummer", "titel", "vedtaget", "typeid"}
    if sort_by not in allowed_sorts:
        sort_by = "dato"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    conn = get_conn()

    where = ["1=1"]
    params = []

    if kategori:
        where.append("""
            a.sag_id IN (
                SELECT sag_id FROM sag_kategori WHERE kategori_id=?
            )
        """)
        params.append(int(kategori))

    if typeid:
        where.append("a.typeid=?")
        params.append(int(typeid))

    if vedtaget is not None and vedtaget != "":
        where.append("a.vedtaget=?")
        params.append(1 if vedtaget.lower() == "true" else 0)

    if q:
        where.append("(s.titel LIKE ? OR s.titelkort LIKE ? OR s.sagsnummer LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]

    if dato_fra:
        where.append("a.dato >= ?")
        params.append(dato_fra)

    if dato_til:
        where.append("a.dato <= ?")
        params.append(dato_til)

    where_str = " AND ".join(where)

    sort_col_map = {
        "dato": "a.dato",
        "sagsnummer": "s.sagsnummer",
        "titel": "s.titelkort",
        "vedtaget": "a.vedtaget",
        "typeid": "a.typeid",
    }
    order = f"{sort_col_map[sort_by]} {sort_dir.upper()}"

    count_sql = f"""
        SELECT COUNT(*)
        FROM afstemning a
        LEFT JOIN sag s ON a.sag_id = s.id
        WHERE {where_str}
    """
    total = conn.execute(count_sql, params).fetchone()[0]

    offset = (page - 1) * per_page
    sql = f"""
        SELECT
            a.id,
            a.nummer,
            a.vedtaget,
            a.dato,
            a.typeid,
            at.type AS afstemningstype,
            s.id AS sag_id,
            s.sagsnummer,
            s.titel,
            s.titelkort,
            s.periodeid,
            GROUP_CONCAT(k.navn, ', ') AS kategorier
        FROM afstemning a
        LEFT JOIN sag s ON a.sag_id = s.id
        LEFT JOIN afstemningstype at ON a.typeid = at.id
        LEFT JOIN sag_kategori sk ON s.id = sk.sag_id
        LEFT JOIN kategori k ON sk.kategori_id = k.id
        WHERE {where_str}
        GROUP BY a.id
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(sql, params + [per_page, offset]).fetchall()
    conn.close()

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "data": rows_to_list(rows),
    })


# ---------------------------------------------------------------------------
# API: Afstemningsdetalje
# ---------------------------------------------------------------------------

@app.route("/api/afstemning/<int:afstemning_id>")
def api_afstemning(afstemning_id):
    conn = get_conn()

    afstemning = conn.execute("""
        SELECT
            a.id, a.nummer, a.vedtaget, a.konklusion, a.kommentar, a.dato, a.typeid,
            at.type AS afstemningstype,
            s.id AS sag_id, s.sagsnummer, s.titel, s.titelkort, s.resume, s.periodeid,
            GROUP_CONCAT(DISTINCT k.navn) AS kategorier
        FROM afstemning a
        LEFT JOIN sag s ON a.sag_id = s.id
        LEFT JOIN afstemningstype at ON a.typeid = at.id
        LEFT JOIN sag_kategori sk ON s.id = sk.sag_id
        LEFT JOIN kategori k ON sk.kategori_id = k.id
        WHERE a.id=?
        GROUP BY a.id
    """, (afstemning_id,)).fetchone()

    if not afstemning:
        conn.close()
        return jsonify({"error": "Ikke fundet"}), 404

    # Stemmer grupperet på parti
    parti_stemmer = conn.execute("""
        SELECT
            p.parti_forkortelse AS parti,
            st.type AS stemmetype,
            COUNT(*) AS antal
        FROM stemme sm
        JOIN politiker p ON sm.politiker_id = p.id
        JOIN stemmetype st ON sm.stemmetype_id = st.id
        WHERE sm.afstemning_id=?
        GROUP BY p.parti_forkortelse, sm.stemmetype_id
        ORDER BY p.parti_forkortelse, sm.stemmetype_id
    """, (afstemning_id,)).fetchall()

    # Individuelle stemmer
    individuelle = conn.execute("""
        SELECT
            p.id AS politiker_id,
            p.fornavn, p.efternavn, p.parti_forkortelse,
            st.type AS stemmetype,
            sm.stemmetype_id
        FROM stemme sm
        JOIN politiker p ON sm.politiker_id = p.id
        JOIN stemmetype st ON sm.stemmetype_id = st.id
        WHERE sm.afstemning_id=?
        ORDER BY p.parti_forkortelse, p.efternavn
    """, (afstemning_id,)).fetchall()

    # Stemmetal
    stemmetal = conn.execute("""
        SELECT st.type, COUNT(*) AS antal
        FROM stemme sm
        JOIN stemmetype st ON sm.stemmetype_id = st.id
        WHERE sm.afstemning_id=?
        GROUP BY sm.stemmetype_id
    """, (afstemning_id,)).fetchall()

    conn.close()

    return jsonify({
        "afstemning": dict(afstemning),
        "parti_stemmer": rows_to_list(parti_stemmer),
        "individuelle_stemmer": rows_to_list(individuelle),
        "stemmetal": rows_to_list(stemmetal),
    })


# ---------------------------------------------------------------------------
# API: Politikere
# ---------------------------------------------------------------------------

@app.route("/api/politikere")
def api_politikere():
    q = request.args.get("q", "").strip()
    conn = get_conn()
    if q:
        rows = conn.execute("""
            SELECT p.id, p.fornavn, p.efternavn, p.parti_forkortelse, pa.navn AS parti_navn
            FROM politiker p
            LEFT JOIN parti pa ON p.parti_id = pa.id
            WHERE p.id IN (SELECT DISTINCT politiker_id FROM stemme)
              AND (p.fornavn LIKE ? OR p.efternavn LIKE ?
                OR (p.fornavn || ' ' || p.efternavn) LIKE ?
                OR p.parti_forkortelse LIKE ?)
            ORDER BY p.parti_forkortelse, p.efternavn, p.fornavn
            LIMIT 50
        """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    else:
        rows = conn.execute("""
            SELECT p.id, p.fornavn, p.efternavn, p.parti_forkortelse, pa.navn AS parti_navn
            FROM politiker p
            LEFT JOIN parti pa ON p.parti_id = pa.id
            WHERE p.id IN (SELECT DISTINCT politiker_id FROM stemme)
            ORDER BY p.parti_forkortelse, p.efternavn, p.fornavn
        """).fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))


# ---------------------------------------------------------------------------
# API: Politikerprofil
# ---------------------------------------------------------------------------

@app.route("/api/politiker/<int:politiker_id>")
def api_politiker(politiker_id):
    conn = get_conn()
    politiker = conn.execute("""
        SELECT p.id, p.fornavn, p.efternavn, p.parti_forkortelse, pa.navn AS parti_navn
        FROM politiker p
        LEFT JOIN parti pa ON p.parti_id = pa.id
        WHERE p.id=?
    """, (politiker_id,)).fetchone()

    if not politiker:
        conn.close()
        return jsonify({"error": "Ikke fundet"}), 404

    # Samlet statistik
    statistik = conn.execute("""
        SELECT st.type, COUNT(*) AS antal
        FROM stemme sm
        JOIN stemmetype st ON sm.stemmetype_id = st.id
        WHERE sm.politiker_id=?
        GROUP BY sm.stemmetype_id
    """, (politiker_id,)).fetchall()

    # Statistik per kategori
    kat_statistik = conn.execute("""
        SELECT k.navn AS kategori, st.type AS stemmetype, COUNT(*) AS antal
        FROM stemme sm
        JOIN stemmetype st ON sm.stemmetype_id = st.id
        JOIN afstemning a ON sm.afstemning_id = a.id
        JOIN sag_kategori sk ON a.sag_id = sk.sag_id
        JOIN kategori k ON sk.kategori_id = k.id
        WHERE sm.politiker_id=?
        GROUP BY k.id, sm.stemmetype_id
        ORDER BY k.navn, sm.stemmetype_id
    """, (politiker_id,)).fetchall()

    conn.close()
    return jsonify({
        "politiker": dict(politiker),
        "statistik": rows_to_list(statistik),
        "kategori_statistik": rows_to_list(kat_statistik),
    })


@app.route("/api/politiker/<int:politiker_id>/stemmer")
def api_politiker_stemmer(politiker_id):
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    kategori = request.args.get("kategori")
    typeid = request.args.get("type")
    vedtaget = request.args.get("vedtaget")
    q = request.args.get("q", "").strip()
    stemmetype = request.args.get("stemmetype")

    conn = get_conn()

    where = ["sm.politiker_id=?"]
    params = [politiker_id]

    if kategori:
        where.append("a.sag_id IN (SELECT sag_id FROM sag_kategori WHERE kategori_id=?)")
        params.append(int(kategori))

    if typeid:
        where.append("a.typeid=?")
        params.append(int(typeid))

    if vedtaget is not None and vedtaget != "":
        where.append("a.vedtaget=?")
        params.append(1 if vedtaget.lower() == "true" else 0)

    if q:
        where.append("(s.titel LIKE ? OR s.titelkort LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

    if stemmetype:
        where.append("sm.stemmetype_id=?")
        params.append(int(stemmetype))

    where_str = " AND ".join(where)

    total = conn.execute(f"""
        SELECT COUNT(*)
        FROM stemme sm
        JOIN afstemning a ON sm.afstemning_id = a.id
        LEFT JOIN sag s ON a.sag_id = s.id
        WHERE {where_str}
    """, params).fetchone()[0]

    offset = (page - 1) * per_page
    rows = conn.execute(f"""
        SELECT
            a.id AS afstemning_id,
            a.dato,
            a.vedtaget,
            a.typeid,
            at.type AS afstemningstype,
            s.sagsnummer,
            s.titel,
            s.titelkort,
            st.type AS stemmetype,
            sm.stemmetype_id,
            GROUP_CONCAT(DISTINCT k.navn) AS kategorier
        FROM stemme sm
        JOIN afstemning a ON sm.afstemning_id = a.id
        LEFT JOIN sag s ON a.sag_id = s.id
        LEFT JOIN afstemningstype at ON a.typeid = at.id
        LEFT JOIN stemmetype st ON sm.stemmetype_id = st.id
        LEFT JOIN sag_kategori sk ON s.id = sk.sag_id
        LEFT JOIN kategori k ON sk.kategori_id = k.id
        WHERE {where_str}
        GROUP BY sm.id
        ORDER BY a.dato DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()

    conn.close()
    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "data": rows_to_list(rows),
    })


# ---------------------------------------------------------------------------
# API: Statistik
# ---------------------------------------------------------------------------

@app.route("/api/statistik/politiker/<int:politiker_id>")
def api_statistik_politiker(politiker_id):
    conn = get_conn()
    stats = conn.execute("""
        SELECT st.type, st.id, COUNT(*) AS antal
        FROM stemme sm
        JOIN stemmetype st ON sm.stemmetype_id = st.id
        WHERE sm.politiker_id=?
        GROUP BY sm.stemmetype_id
        ORDER BY sm.stemmetype_id
    """, (politiker_id,)).fetchall()
    conn.close()
    return jsonify(rows_to_list(stats))


# ---------------------------------------------------------------------------
# Afstemningstyper
# ---------------------------------------------------------------------------

@app.route("/api/afstemningstyper")
def api_afstemningstyper():
    conn = get_conn()
    rows = conn.execute("SELECT id, type FROM afstemningstype ORDER BY id").fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "folketinget.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS afstemningstype (
            id INTEGER PRIMARY KEY,
            type TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stemmetype (
            id INTEGER PRIMARY KEY,
            type TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS parti (
            id INTEGER PRIMARY KEY,
            forkortelse TEXT NOT NULL,
            navn TEXT
        );

        CREATE TABLE IF NOT EXISTS politiker (
            id INTEGER PRIMARY KEY,
            fornavn TEXT NOT NULL,
            efternavn TEXT NOT NULL,
            parti_forkortelse TEXT,
            parti_id INTEGER,
            FOREIGN KEY (parti_id) REFERENCES parti(id)
        );

        CREATE TABLE IF NOT EXISTS sag (
            id INTEGER PRIMARY KEY,
            titel TEXT,
            titelkort TEXT,
            resume TEXT,
            sagsnummer TEXT,
            typeid INTEGER,
            periodeid INTEGER
        );

        CREATE TABLE IF NOT EXISTS kategori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            navn TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS sag_kategori (
            sag_id INTEGER,
            kategori_id INTEGER,
            PRIMARY KEY (sag_id, kategori_id),
            FOREIGN KEY (sag_id) REFERENCES sag(id),
            FOREIGN KEY (kategori_id) REFERENCES kategori(id)
        );

        CREATE TABLE IF NOT EXISTS afstemning (
            id INTEGER PRIMARY KEY,
            nummer INTEGER,
            konklusion TEXT,
            vedtaget INTEGER,
            kommentar TEXT,
            dato TEXT,
            typeid INTEGER,
            sag_id INTEGER,
            FOREIGN KEY (typeid) REFERENCES afstemningstype(id),
            FOREIGN KEY (sag_id) REFERENCES sag(id)
        );

        CREATE TABLE IF NOT EXISTS stemme (
            id INTEGER PRIMARY KEY,
            afstemning_id INTEGER NOT NULL,
            politiker_id INTEGER NOT NULL,
            stemmetype_id INTEGER NOT NULL,
            FOREIGN KEY (afstemning_id) REFERENCES afstemning(id),
            FOREIGN KEY (politiker_id) REFERENCES politiker(id),
            FOREIGN KEY (stemmetype_id) REFERENCES stemmetype(id)
        );

        CREATE TABLE IF NOT EXISTS fetch_progress (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_stemme_politiker ON stemme(politiker_id);
        CREATE INDEX IF NOT EXISTS idx_stemme_afstemning ON stemme(afstemning_id);
        CREATE INDEX IF NOT EXISTS idx_afstemning_sag ON afstemning(sag_id);
        CREATE INDEX IF NOT EXISTS idx_sag_kategori_sag ON sag_kategori(sag_id);
        CREATE INDEX IF NOT EXISTS idx_sag_kategori_kategori ON sag_kategori(kategori_id);
        CREATE INDEX IF NOT EXISTS idx_politiker_parti ON politiker(parti_id);
    """)
    conn.commit()
    conn.close()
    print("Database schema oprettet.")


def insert_or_replace(table, rows, conn=None):
    """Insert or replace rows (list of dicts) into table."""
    if not rows:
        return
    close = False
    if conn is None:
        conn = get_conn()
        close = True
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    if close:
        conn.commit()
        conn.close()


def get_progress(key):
    conn = get_conn()
    row = conn.execute("SELECT value FROM fetch_progress WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_progress(key, value, conn=None):
    close = False
    if conn is None:
        conn = get_conn()
        close = True
    conn.execute(
        "INSERT OR REPLACE INTO fetch_progress (key, value) VALUES (?, ?)",
        (key, str(value))
    )
    if close:
        conn.commit()
        conn.close()


if __name__ == "__main__":
    create_schema()

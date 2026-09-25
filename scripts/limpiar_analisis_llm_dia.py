"""Borra el analisis LLM (tabla llm_filing_analysis) de un dia concreto,
dejando intactos filings/raw_text/clean_text, para poder relanzar
--llm-first-pass sobre ese mismo dia con otra configuracion (p. ej. otro
numero de workers) sin volver a descargar nada de la SEC.

No toca ninguna otra tabla ni ningun otro dia. No ejecuta VACUUM (no hace
falta para relanzar un benchmark repetidas veces sobre el mismo volumen).

Uso:
    .venv/bin/python3 scripts/limpiar_analisis_llm_dia.py 2026-09-02
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.db import get_connection


def limpiar_analisis_llm(date_filed: str) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM llm_filing_analysis WHERE filing_filename IN "
            "(SELECT filename FROM filings WHERE date_filed = ?)",
            (date_filed,),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python3 scripts/limpiar_analisis_llm_dia.py YYYY-MM-DD")
        sys.exit(1)

    date_filed = sys.argv[1]
    deleted = limpiar_analisis_llm(date_filed)
    print(f"Borradas {deleted} filas de llm_filing_analysis para el {date_filed}.")

    with get_connection() as conn:
        restante = conn.execute(
            """
            SELECT COUNT(*) FROM filings f
            JOIN llm_filing_analysis l ON l.filing_filename = f.filename
            WHERE f.date_filed = ?
            """,
            (date_filed,),
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE date_filed = ?", (date_filed,)
        ).fetchone()[0]
    print(f"Verificacion: {restante} analisis restantes / {total} filings ese dia.")

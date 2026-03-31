#!/usr/bin/env python3
"""
db.py — Esquema SQLite, helpers y sincronización de máquinas.
SENRG7 — Módulo 1 (Bridge)

Ejecutar directamente para inicializar la base de datos:
    python db.py
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# ── Rutas ────────────────────────────────────────────────
PROYECTO_ROOT = Path(__file__).parent.parent
CONFIG_PATH   = PROYECTO_ROOT / "config.json"

# ── Logger ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── DDL — sentencias de creación de tablas ───────────────
DDL_MAQUINAS = """
CREATE TABLE IF NOT EXISTS maquinas (
    id                 INTEGER PRIMARY KEY,
    nombre             TEXT    NOT NULL,
    descripcion        TEXT    DEFAULT '',
    activa             BOOLEAN DEFAULT 1,
    factor_calibracion REAL    DEFAULT 1.0,
    creada_en          DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_LECTURAS = """
CREATE TABLE IF NOT EXISTS lecturas (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    maquina_id INTEGER  NOT NULL,
    corriente  REAL     NOT NULL,
    estado     TEXT     NOT NULL,
    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (maquina_id) REFERENCES maquinas(id)
);
"""

DDL_EVENTOS = """
CREATE TABLE IF NOT EXISTS eventos (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    maquina_id INTEGER  NOT NULL,
    tipo       TEXT     NOT NULL,
    descripcion TEXT    DEFAULT '',
    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (maquina_id) REFERENCES maquinas(id)
);
"""

DDL_SESIONES = """
CREATE TABLE IF NOT EXISTS sesiones (
    token     TEXT     PRIMARY KEY,
    usuario   TEXT     NOT NULL,
    creada_en DATETIME DEFAULT CURRENT_TIMESTAMP,
    expira_en DATETIME NOT NULL
);
"""

DDL_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_lecturas_maquina_ts ON lecturas(maquina_id, timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_eventos_maquina_ts  ON eventos(maquina_id, timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_sesiones_expira     ON sesiones(expira_en);",
]


def cargar_config() -> dict:
    """Lee config.json desde la raíz del proyecto."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = json.load(f)
        # Resolver ruta de BD como absoluta (relativa al root del proyecto)
        config["base_datos"]["_ruta_abs"] = str(
            PROYECTO_ROOT / config["base_datos"]["ruta"]
        )
        return config
    except FileNotFoundError:
        log.error(f"No se encontró config.json en: {CONFIG_PATH}")
        raise
    except json.JSONDecodeError as e:
        log.error(f"config.json tiene formato inválido: {e}")
        raise


@contextmanager
def get_db(config: dict):
    """
    Context manager que abre una conexión SQLite y la cierra al terminar.
    Usa WAL para soportar lectura concurrente (bridge + API en paralelo).
    Hace commit automático; rollback en caso de excepción.
    """
    ruta = config["base_datos"]["_ruta_abs"]
    conn = sqlite3.connect(ruta, check_same_thread=False)
    conn.row_factory = sqlite3.Row          # acceso por nombre de columna
    conn.execute("PRAGMA journal_mode=WAL") # permite lectura concurrente
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000") # espera hasta 5s si BD ocupada
    try:
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        log.error(f"Error SQLite, rollback: {e}")
        raise
    except Exception as e:
        conn.rollback()
        log.error(f"Error inesperado en BD, rollback: {e}")
        raise
    finally:
        conn.close()


def inicializar_db(config: dict) -> None:
    """
    Crea todas las tablas e índices si no existen.
    Sincroniza la lista de máquinas desde config.json.
    Seguro de llamar múltiples veces (idempotente).
    """
    ruta = config["base_datos"]["_ruta_abs"]
    log.info(f"Inicializando base de datos: {ruta}")

    with get_db(config) as conn:
        # Crear tablas
        for ddl in [DDL_MAQUINAS, DDL_LECTURAS, DDL_EVENTOS, DDL_SESIONES]:
            conn.execute(ddl)
        # Crear índices
        for idx in DDL_INDICES:
            conn.execute(idx)

    # Sincronizar máquinas (en transacción separada para aislamiento)
    sincronizar_maquinas(config)
    log.info("Base de datos lista.")


def sincronizar_maquinas(config: dict) -> None:
    """
    Inserta o actualiza las máquinas definidas en config.json.
    Usa UPSERT para no duplicar ni perder datos históricos.
    """
    maquinas = config.get("maquinas", [])
    if not maquinas:
        log.warning("No hay máquinas definidas en config.json")
        return

    with get_db(config) as conn:
        for m in maquinas:
            conn.execute(
                """
                INSERT INTO maquinas (id, nombre, descripcion, activa, factor_calibracion)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    nombre             = excluded.nombre,
                    descripcion        = excluded.descripcion,
                    activa             = excluded.activa,
                    factor_calibracion = excluded.factor_calibracion
                """,
                (
                    m["id"],
                    m["nombre"],
                    m["descripcion"],
                    1 if m["activa"] else 0,
                    m["factor_calibracion"],
                ),
            )
    log.info(f"Sincronizadas {len(maquinas)} máquinas desde config.json")


def limpiar_datos_antiguos(config: dict) -> None:
    """
    Elimina lecturas de más de 30 días y sesiones expiradas.
    Llamar periódicamente (p.ej. al iniciar el bridge cada día).
    """
    with get_db(config) as conn:
        resultado = conn.execute(
            "DELETE FROM lecturas WHERE timestamp < datetime('now', '-30 days')"
        )
        lecturas_borradas = resultado.rowcount

        resultado = conn.execute(
            "DELETE FROM sesiones WHERE expira_en < datetime('now')"
        )
        sesiones_borradas = resultado.rowcount

    log.info(
        f"Limpieza: {lecturas_borradas} lecturas antiguas, "
        f"{sesiones_borradas} sesiones expiradas eliminadas."
    )


def obtener_ultima_lectura(conn: sqlite3.Connection, maquina_id: int) -> sqlite3.Row | None:
    """Retorna la fila más reciente de lecturas para una máquina."""
    return conn.execute(
        "SELECT * FROM lecturas WHERE maquina_id = ? ORDER BY timestamp DESC LIMIT 1",
        (maquina_id,),
    ).fetchone()


def obtener_maquinas_activas(conn: sqlite3.Connection) -> list:
    """Lista de máquinas activas como diccionarios."""
    filas = conn.execute(
        "SELECT * FROM maquinas WHERE activa = 1 ORDER BY id"
    ).fetchall()
    return [dict(f) for f in filas]


# ── Punto de entrada standalone ──────────────────────────
if __name__ == "__main__":
    config = cargar_config()
    inicializar_db(config)
    print(f"\n✓ Base de datos creada: {config['base_datos']['_ruta_abs']}")
    print("  Tablas: maquinas, lecturas, eventos, sesiones")

    # Mostrar máquinas cargadas
    with get_db(config) as conn:
        maquinas = obtener_maquinas_activas(conn)
    print(f"  Máquinas activas: {len(maquinas)}")
    for m in maquinas:
        print(f"    [{m['id']}] {m['nombre']} — factor_cal: {m['factor_calibracion']}")

# Skill: SQLite — Esquema y Queries

## Archivo de base de datos
- **Nombre:** `senrg7.db`
- **Ubicación:** raíz del proyecto (junto a config.json)
- **Creación:** automática al correr `python db.py` por primera vez

## Esquema completo

```sql
-- Máquinas registradas (sincronizadas desde config.json)
CREATE TABLE IF NOT EXISTS maquinas (
    id                  INTEGER PRIMARY KEY,
    nombre              TEXT NOT NULL,
    descripcion         TEXT DEFAULT '',
    activa              BOOLEAN DEFAULT 1,
    factor_calibracion  REAL DEFAULT 1.0,
    creada_en           DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Lecturas de corriente — serie temporal principal
CREATE TABLE IF NOT EXISTS lecturas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina_id  INTEGER NOT NULL,
    corriente   REAL NOT NULL,            -- Amperios RMS, 2 decimales
    estado      TEXT NOT NULL,            -- APAGADA|ENCENDIDA|BORDANDO|SOBRECARGA
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (maquina_id) REFERENCES maquinas(id)
);

-- Eventos detectados por el sistema
CREATE TABLE IF NOT EXISTS eventos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina_id  INTEGER NOT NULL,
    tipo        TEXT NOT NULL,            -- INICIO|PAUSA|FIN|ROTURA_HILO|SOBRECARGA|ERROR|CAMBIO_ESTADO
    descripcion TEXT DEFAULT '',
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (maquina_id) REFERENCES maquinas(id)
);

-- Sesiones de usuario autenticado
CREATE TABLE IF NOT EXISTS sesiones (
    token       TEXT PRIMARY KEY,
    usuario     TEXT NOT NULL,
    creada_en   DATETIME DEFAULT CURRENT_TIMESTAMP,
    expira_en   DATETIME NOT NULL
);

-- Índices para performance en queries frecuentes
CREATE INDEX IF NOT EXISTS idx_lecturas_maquina_ts
    ON lecturas(maquina_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_eventos_maquina_ts
    ON eventos(maquina_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_sesiones_expira
    ON sesiones(expira_en);
```

## Sincronización de máquinas desde config.json
```python
# Usar INSERT OR REPLACE para sincronizar sin duplicar
conn.execute("""
    INSERT INTO maquinas (id, nombre, descripcion, activa, factor_calibracion)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        nombre             = excluded.nombre,
        descripcion        = excluded.descripcion,
        activa             = excluded.activa,
        factor_calibracion = excluded.factor_calibracion
""", (m["id"], m["nombre"], m["descripcion"], m["activa"], m["factor_calibracion"]))
```

## Queries frecuentes

### Última lectura de cada máquina activa
```sql
SELECT m.id, m.nombre, l.corriente, l.estado, l.timestamp
FROM maquinas m
JOIN lecturas l ON l.id = (
    SELECT id FROM lecturas
    WHERE maquina_id = m.id
    ORDER BY timestamp DESC LIMIT 1
)
WHERE m.activa = 1;
```

### Historial de corriente de los últimos N minutos
```sql
SELECT corriente, estado, timestamp
FROM lecturas
WHERE maquina_id = ?
  AND timestamp >= datetime('now', '-? minutes')
ORDER BY timestamp ASC;
```

### Eventos recientes de todas las máquinas
```sql
SELECT e.tipo, e.descripcion, e.timestamp, m.nombre
FROM eventos e
JOIN maquinas m ON m.id = e.maquina_id
ORDER BY e.timestamp DESC
LIMIT 20;
```

### Resumen productivo del día
```sql
SELECT
    maquina_id,
    SUM(CASE WHEN estado = 'BORDANDO'   THEN 1 ELSE 0 END) AS seg_bordando,
    SUM(CASE WHEN estado = 'ENCENDIDA'  THEN 1 ELSE 0 END) AS seg_encendida,
    SUM(CASE WHEN estado = 'APAGADA'    THEN 1 ELSE 0 END) AS seg_apagada,
    SUM(CASE WHEN estado = 'SOBRECARGA' THEN 1 ELSE 0 END) AS seg_sobrecarga
FROM lecturas
WHERE timestamp >= date('now')
GROUP BY maquina_id;
```

### Roturas de hilo del día
```sql
SELECT COUNT(*) as total
FROM eventos
WHERE tipo = 'ROTURA_HILO'
  AND timestamp >= date('now');
```

## Mantenimiento (limpieza periódica)
```python
# Eliminar lecturas de más de 30 días — correr diariamente
conn.execute(
    "DELETE FROM lecturas WHERE timestamp < datetime('now', '-30 days')"
)

# Eliminar sesiones expiradas
conn.execute(
    "DELETE FROM sesiones WHERE expira_en < datetime('now')"
)
```

## Volumen de datos estimado
```
1 máquina × 1 lectura/seg × 86.400 seg/día = 86.400 filas/día
4 máquinas                                  = 345.600 filas/día
30 días de retención                         = ~10 millones de filas

SQLite maneja sin problemas hasta 50 millones de filas.
Tamaño estimado del archivo: ~500MB después de 30 días con 4 máquinas.
```

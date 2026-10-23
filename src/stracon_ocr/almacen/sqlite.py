"""Almacén SQLite: JSON en JSONB, texto completo en FTS5 y archivos originales por hash en disco."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import imagehash

from ..contrato import Documento, Duplicado, Expediente

ESQUEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    expediente TEXT,
    rut TEXT,
    tipo TEXT,
    nombre TEXT,
    contrato_version TEXT,
    json BLOB NOT NULL CHECK (json_valid(json, 8)),
    creado TEXT NOT NULL,
    UNIQUE (sha256, expediente)
);
CREATE INDEX IF NOT EXISTS ix_doc_expediente ON documentos(expediente);
CREATE INDEX IF NOT EXISTS ix_doc_tipo ON documentos(tipo);
CREATE INDEX IF NOT EXISTS ix_doc_rut ON documentos(rut);
CREATE INDEX IF NOT EXISTS ix_doc_sha ON documentos(sha256);
CREATE INDEX IF NOT EXISTS ix_doc_rut_campo ON documentos(json ->> '$.campos.rut.valor_normalizado');
CREATE VIRTUAL TABLE IF NOT EXISTS documentos_fts USING fts5(doc_id UNINDEXED, texto, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS hashes_percepcion (doc_id TEXT NOT NULL, expediente TEXT, tipo TEXT, pagina INTEGER, phash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_phash_doc ON hashes_percepcion(doc_id);
CREATE TABLE IF NOT EXISTS expedientes (id TEXT PRIMARY KEY, json BLOB NOT NULL CHECK (json_valid(json, 8)), actualizado TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trabajos (
    id TEXT PRIMARY KEY, estado TEXT NOT NULL, nombre TEXT, expediente TEXT, documento_id TEXT,
    error TEXT, intentos INTEGER DEFAULT 0, creado TEXT NOT NULL, actualizado TEXT NOT NULL
);
"""


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RepositorioSQLite:
    def __init__(self, ruta: Path) -> None:
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.archivos = self.ruta.parent / "archivos"
        self._lock = threading.Lock()
        with self._con() as c:
            c.executescript(ESQUEMA)

    @contextmanager
    def _con(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = sqlite3.connect(self.ruta, timeout=30)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            try:
                yield con
                con.commit()
            finally:
                con.close()

    # --- documentos ---------------------------------------------------------------------------

    def guardar_documento(self, doc: Documento, texto: str, phashes: list[tuple[int, str]]) -> None:
        rut = (doc.campos.get("rut").valor_normalizado if doc.campos.get("rut") else None)
        with self._con() as c:
            viejos = [r["id"] for r in c.execute("SELECT id FROM documentos WHERE sha256 = ? AND expediente IS ?", (doc.archivo.sha256, doc.expediente))]
            for v in viejos:  # reprocesar el mismo archivo en el mismo expediente reemplaza el resultado
                c.execute("DELETE FROM documentos WHERE id = ?", (v,))
                c.execute("DELETE FROM documentos_fts WHERE doc_id = ?", (v,))
                c.execute("DELETE FROM hashes_percepcion WHERE doc_id = ?", (v,))
            c.execute(
                "INSERT INTO documentos (id, sha256, expediente, rut, tipo, nombre, contrato_version, json, creado) VALUES (?,?,?,?,?,?,?, jsonb(?), ?)",
                (doc.id, doc.archivo.sha256, doc.expediente, rut, doc.clasificacion.tipo, doc.archivo.nombre, doc.contrato_version,
                 doc.model_dump_json(), _ahora()),
            )
            c.execute("INSERT INTO documentos_fts (doc_id, texto) VALUES (?, ?)", (doc.id, texto))
            c.executemany("INSERT INTO hashes_percepcion (doc_id, expediente, tipo, pagina, phash) VALUES (?,?,?,?,?)",
                          [(doc.id, doc.expediente, doc.clasificacion.tipo, n, h) for n, h in phashes])

    def obtener_documento(self, doc_id: str) -> dict[str, Any] | None:
        with self._con() as c:
            r = c.execute("SELECT json(json) AS j FROM documentos WHERE id = ?", (doc_id,)).fetchone()
        return json.loads(r["j"]) if r else None

    def listar_documentos(self, expediente: str | None = None, tipo: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
        sql = """SELECT id, expediente, tipo, nombre, rut, sha256, creado,
                        json ->> '$.resumen.senales' AS senales, json ->> '$.resumen.severidad_max' AS severidad_max,
                        json ->> '$.resumen.requiere_revision' AS requiere_revision,
                        json ->> '$.resumen.estado' AS estado, json ->> '$.resumen.estado_texto' AS estado_texto,
                        json ->> '$.resumen.observaciones' AS observaciones
                 FROM documentos WHERE 1=1"""
        args: list[Any] = []
        if expediente:
            sql += " AND expediente = ?"
            args.append(expediente)
        if tipo:
            sql += " AND tipo = ?"
            args.append(tipo)
        sql += " ORDER BY creado DESC LIMIT ?"
        args.append(limite)
        with self._con() as c:
            return [dict(r) | {"requiere_revision": bool(r["requiere_revision"])} for r in c.execute(sql, args)]

    def documentos_de_expediente(self, expediente: str) -> list[Documento]:
        with self._con() as c:
            filas = c.execute("SELECT json(json) AS j FROM documentos WHERE expediente = ?", (expediente,)).fetchall()
        return [Documento.model_validate_json(f["j"]) for f in filas]

    def buscar_texto(self, consulta: str, limite: int = 50) -> list[dict[str, Any]]:
        with self._con() as c:
            filas = c.execute(
                """SELECT d.id, d.expediente, d.tipo, d.nombre, snippet(documentos_fts, 1, '[', ']', '…', 12) AS fragmento
                   FROM documentos_fts JOIN documentos d ON d.id = documentos_fts.doc_id
                   WHERE documentos_fts MATCH ? ORDER BY rank LIMIT ?""",
                (consulta, limite),
            ).fetchall()
        return [dict(f) for f in filas]

    def duplicados(self, sha256: str, expediente: str | None, phashes: list[tuple[int, str]], distancia: int) -> list[Duplicado]:
        dups: list[Duplicado] = []
        with self._con() as c:
            for r in c.execute("SELECT id, expediente FROM documentos WHERE sha256 = ? AND expediente IS NOT ?", (sha256, expediente)):
                dups.append(Duplicado(tipo="sha256", documento=sha256[:12], otro_expediente=r["expediente"] or "", otro_documento=r["id"]))
            if phashes:
                otros = c.execute("SELECT doc_id, expediente, pagina, phash FROM hashes_percepcion WHERE expediente IS NOT ?", (expediente,)).fetchall()
        for n, h in phashes:
            mio = imagehash.hex_to_hash(h)
            for o in otros:
                d = mio - imagehash.hex_to_hash(o["phash"])
                if d <= distancia and not any(x.otro_documento == o["doc_id"] for x in dups):
                    dups.append(Duplicado(tipo="phash_pagina", documento=f"pagina {n}", otro_expediente=o["expediente"] or "",
                                          otro_documento=o["doc_id"], distancia=int(d)))
        return dups

    # --- expedientes ----------------------------------------------------------------------------

    def guardar_expediente(self, exp: Expediente) -> None:
        with self._con() as c:
            c.execute("INSERT INTO expedientes (id, json, actualizado) VALUES (?, jsonb(?), ?) "
                      "ON CONFLICT(id) DO UPDATE SET json = excluded.json, actualizado = excluded.actualizado",
                      (exp.expediente, exp.model_dump_json(), _ahora()))

    def obtener_expediente(self, expediente: str) -> dict[str, Any] | None:
        with self._con() as c:
            r = c.execute("SELECT json(json) AS j FROM expedientes WHERE id = ?", (expediente,)).fetchone()
        return json.loads(r["j"]) if r else None

    # --- archivos originales ---------------------------------------------------------------------

    def guardar_archivo(self, sha256: str, datos: bytes) -> None:
        ruta = self.archivos / sha256[:2] / sha256
        if not ruta.exists():
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(datos)

    def ruta_archivo(self, sha256: str) -> Path | None:
        ruta = self.archivos / sha256[:2] / sha256
        return ruta if ruta.exists() else None

    # --- trabajos (cola local) ------------------------------------------------------------------

    def crear_trabajo(self, trabajo_id: str, nombre: str, expediente: str | None) -> None:
        with self._con() as c:
            c.execute("INSERT INTO trabajos (id, estado, nombre, expediente, creado, actualizado) VALUES (?, 'pendiente', ?, ?, ?, ?)",
                      (trabajo_id, nombre, expediente, _ahora(), _ahora()))

    def actualizar_trabajo(self, trabajo_id: str, **campos: Any) -> None:
        campos["actualizado"] = _ahora()
        sets = ", ".join(f"{k} = ?" for k in campos)
        with self._con() as c:
            c.execute(f"UPDATE trabajos SET {sets} WHERE id = ?", (*campos.values(), trabajo_id))

    def obtener_trabajo(self, trabajo_id: str) -> dict[str, Any] | None:
        with self._con() as c:
            r = c.execute("SELECT * FROM trabajos WHERE id = ?", (trabajo_id,)).fetchone()
        return dict(r) if r else None

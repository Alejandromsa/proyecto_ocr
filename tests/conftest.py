from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from stracon_ocr.config import Config
from stracon_ocr.pipeline import Procesador

DORADO = Path(__file__).resolve().parents[1] / "datos" / "dorado"
HOY = date(2026, 10, 21)  # fecha de referencia de los documentos ficticios


def mockup(relativo: str) -> Path:
    ruta = DORADO / relativo
    if not ruta.exists():
        pytest.skip(f"Falta el PDF de prueba {relativo} (copiar expedientes del mockup a datos/dorado)")
    return ruta


@pytest.fixture(scope="session")
def reglas_mockup(tmp_path_factory) -> Path:
    """Reglas de producción más los formatos de folio y código ficticios de los PDF de datos/dorado (no forman parte de reglas.json)."""
    reglas = json.loads(Config().reglas.read_text(encoding="utf-8"))
    for tipo in ("certificado_antecedentes", "hoja_vida_conductor"):
        reglas["formatos"][f"{tipo}.folio"] = "^(\\d{12}|\\d{3}-\\d{3}-\\d{4})$"
        reglas["formatos"][f"{tipo}.codigo_verificacion"] = "^([0-9A-F]{12}|[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4})$"
    ruta = tmp_path_factory.mktemp("reglas") / "reglas_mockup.json"
    ruta.write_text(json.dumps(reglas, ensure_ascii=False), encoding="utf-8")
    return ruta


@pytest.fixture(scope="session")
def proc_sin_bd(reglas_mockup: Path) -> Procesador:
    cfg = Config()
    cfg.reglas = reglas_mockup  # los PDF ficticios traen folios con el formato del mockup
    return Procesador(cfg, guardar=False)


@pytest.fixture()
def cfg_tmp(tmp_path) -> Config:
    c = Config()
    c.db = tmp_path / "prueba.db"
    c.trabajadores = 0  # en el mismo proceso; la concurrencia real se prueba aparte
    return c

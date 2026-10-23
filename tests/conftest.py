from __future__ import annotations

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
def proc_sin_bd() -> Procesador:
    return Procesador(Config(), guardar=False)


@pytest.fixture()
def cfg_tmp(tmp_path) -> Config:
    c = Config()
    c.db = tmp_path / "prueba.db"
    c.trabajadores = 0  # en el mismo proceso; la concurrencia real se prueba aparte
    return c

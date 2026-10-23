"""Procesos de trabajo para atender cargas simultáneas.

Cada proceso carga una vez su motor OCR y su conexión a la base, y procesa un documento a la vez. Los núcleos se
reparten entre los procesos para que no compitan. Con `trabajadores = 0` todo corre en el proceso actual (pruebas).
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import replace
from typing import Any

from .config import Config
from .contrato import Documento

_proc = None  # Procesador del proceso de trabajo


def _iniciar(cfg: Config) -> None:
    global _proc
    from .pipeline import Procesador

    from .motores import obtener

    logging.basicConfig(level=logging.WARNING)
    _proc = Procesador(cfg)
    motor = obtener(cfg.motor, cfg)
    if hasattr(motor, "_cargar"):
        motor._cargar()  # el modelo se carga una vez, al arrancar el proceso


def _procesar(kwargs: dict[str, Any]) -> str:
    return _proc.procesar(**kwargs).model_dump_json()


def hilos_por_trabajador(n: int) -> int:
    # medio núcleo lógico por trabajador: los hilos gemelos (hyperthreading) y los núcleos de eficiencia no aceleran
    # el OCR, y con más hilos los procesos se estorban (i7-1270P: 2 × 4 hilos = 32 docs/min; 2 × 8 = 31; 1 × 16 = 26)
    return max(2, (os.cpu_count() or 4) // (2 * max(n, 1)))


class Grupo:
    def __init__(self, cfg: Config, procesador=None) -> None:
        self.n = max(0, cfg.trabajadores)
        self.procesador = procesador  # para trabajadores = 0
        self.pool: ProcessPoolExecutor | None = None
        if self.n:
            cfg_trabajador = replace(cfg, hilos_ocr=cfg.hilos_ocr if cfg.hilos_ocr > 0 else hilos_por_trabajador(self.n))
            self.pool = ProcessPoolExecutor(self.n, initializer=_iniciar, initargs=(cfg_trabajador,))

    def enviar(self, **kwargs: Any) -> Future:
        """Encola un documento; el futuro entrega el Documento o la excepción de la etapa de ingesta."""
        if self.pool is None:
            f: Future = Future()
            try:
                f.set_result(self.procesador.procesar(**kwargs))
            except Exception as e:  # noqa: BLE001 - se entrega en el futuro
                f.set_exception(e)
            return f
        interno = self.pool.submit(_procesar, kwargs)
        externo: Future = Future()

        def listo(fut: Future) -> None:
            try:
                externo.set_result(Documento.model_validate_json(fut.result()))
            except Exception as e:  # noqa: BLE001
                externo.set_exception(e)

        interno.add_done_callback(listo)
        return externo

    async def procesar(self, **kwargs: Any) -> Documento:
        if self.pool is None:
            return await asyncio.to_thread(self.procesador.procesar, **kwargs)
        return await asyncio.wrap_future(self.enviar(**kwargs))

    def calentar(self) -> None:
        """Arranca los procesos (cargan el OCR) para que la primera carga no espere."""
        if self.pool is not None:
            for f in [self.pool.submit(int, 0) for _ in range(self.n)]:
                f.result()

    def cerrar(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)

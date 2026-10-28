"""Configuración por variables de entorno (prefijo SOCR_). Todo tiene un valor por defecto local."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ_PAQUETE = Path(__file__).resolve().parent


def _bool(nombre: str, defecto: bool) -> bool:
    v = os.environ.get(nombre)
    return defecto if v is None else v.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


@dataclass
class Config:
    # Base de datos SQLite (se crea si no existe)
    db: Path = field(default_factory=lambda: Path(os.environ.get("SOCR_DB", "datos/stracon-ocr.db")))
    # Resolución a la que se renderizan las páginas para OCR y forense visual
    dpi: int = field(default_factory=lambda: int(os.environ.get("SOCR_DPI", "200")))
    # Motor OCR principal: rapidocr | tesseract | azure
    motor: str = field(default_factory=lambda: os.environ.get("SOCR_MOTOR", "rapidocr"))
    # En PDF nativo, OCR del render para comparar texto declarado vs visible
    comparar_ocr: bool = field(default_factory=lambda: _bool("SOCR_COMPARAR_OCR", True))
    # Segundo motor (Tesseract) sobre los campos clave, si está instalado
    segundo_motor: bool = field(default_factory=lambda: _bool("SOCR_SEGUNDO_MOTOR", True))
    # Ruta de tesseract.exe (si no está en el PATH)
    tesseract_cmd: str | None = field(default_factory=lambda: os.environ.get("SOCR_TESSERACT"))
    # Azure Document Intelligence (apagado por defecto: solo se usa con --motor azure)
    azure_endpoint: str | None = field(default_factory=lambda: os.environ.get("SOCR_AZURE_ENDPOINT"))
    azure_key: str | None = field(default_factory=lambda: os.environ.get("SOCR_AZURE_KEY"))
    # LLM local de respaldo para campos faltantes (Ollama). Vacío = apagado
    ollama_modelo: str | None = field(default_factory=lambda: os.environ.get("SOCR_OLLAMA_MODELO") or None)
    ollama_url: str = field(default_factory=lambda: os.environ.get("SOCR_OLLAMA_URL", "http://127.0.0.1:11434"))
    # Orígenes permitidos para la webapp (CORS), separados por coma
    cors: list[str] = field(default_factory=lambda: [o.strip() for o in os.environ.get("SOCR_CORS", "*").split(",") if o.strip()])
    # Procesos de trabajo de la API: documentos que se procesan a la vez (0 = en el mismo proceso, para pruebas)
    trabajadores: int = field(default_factory=lambda: int(os.environ.get("SOCR_TRABAJADORES", "2")))
    # Hilos del OCR por proceso (-1 = todos los núcleos; con varios trabajadores se reparten)
    hilos_ocr: int = field(default_factory=lambda: int(os.environ.get("SOCR_HILOS_OCR", "-1")))
    # Tamaño máximo de archivo (MB): subidas a la API y archivos que se descargan de una convocatoria
    max_mb: int = field(default_factory=lambda: int(os.environ.get("SOCR_MAX_MB", "25")))
    # Raíz de los JSON de resultados por convocatoria (pueden contener datos personales: no se versiona)
    resultados: Path = field(default_factory=lambda: Path(os.environ.get("SOCR_RESULTADOS", "datos/resultados")))
    # Dominios (sufijos) permitidos para la URL del flujo de Power Automate, separados por coma. Un host local de
    # esta lista (127.0.0.1, localhost) se acepta también por http: solo para pruebas
    flujo_hosts: list[str] = field(default_factory=lambda: [h.strip().lower() for h in os.environ.get(
        "SOCR_FLUJO_HOSTS", "logic.azure.com,powerplatform.com").split(",") if h.strip()])
    # Archivo de reglas (umbrales, listas de productores, formatos de folio)
    reglas: Path = field(default_factory=lambda: Path(os.environ.get("SOCR_REGLAS", str(RAIZ_PAQUETE / "reglas.json"))))


def cargar() -> Config:
    return Config()

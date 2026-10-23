"""Adaptador opcional a Azure AI Document Intelligence (modelo prebuilt-read).

Apagado por defecto. Envía la página a la nube: usar solo con documentos ficticios hasta tener autorización.
Requiere `uv sync --extra azure` y las variables SOCR_AZURE_ENDPOINT y SOCR_AZURE_KEY.
"""
from __future__ import annotations

import importlib.metadata as md
import io

from PIL import Image

from ..layout import Pal
from .base import caja_de_poligono


class MotorAzureDI:
    nombre = "azure_di"

    def __init__(self, endpoint: str, clave: str, modelo: str = "prebuilt-read") -> None:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        self._cliente = DocumentIntelligenceClient(endpoint, AzureKeyCredential(clave))
        self.modelo = modelo

    def version(self) -> str:
        return f"{self.modelo}/sdk-{md.version('azure-ai-documentintelligence')}"

    def leer(self, imagen: Image.Image) -> list[Pal]:
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

        buf = io.BytesIO()
        imagen.convert("RGB").save(buf, format="PNG")
        poller = self._cliente.begin_analyze_document(self.modelo, AnalyzeDocumentRequest(bytes_source=buf.getvalue()))
        resultado = poller.result()
        palabras: list[Pal] = []
        for pagina in resultado.pages or []:
            for i, w in enumerate(pagina.words or []):
                poly = w.polygon or []
                puntos = list(zip(poly[0::2], poly[1::2]))
                if puntos:
                    palabras.append(Pal(w.content, caja_de_poligono(puntos), float(w.confidence or 0), orden=i, motor=self.nombre))
        return palabras

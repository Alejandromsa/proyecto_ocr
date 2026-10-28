"""Contrato JSON v1.0 (ver docs/contrato-json.md). Todo documento procesado se valida con estos modelos."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, computed_field

CONTRATO_VERSION = "1.0"

BBox = list[float]  # [x0, y0, x1, y1], origen arriba a la izquierda
Severidad = Literal["info", "baja", "media", "alta"]
Familia = Literal["integridad", "metadatos", "estructura", "docx", "visual", "ocr", "duplicado"]
TipoDocumento = Literal[
    "cedula_identidad",
    "licencia_conducir",
    "certificado_estudios_medios",
    "certificado_estudios_profesionales",
    "certificado_antecedentes",
    "hoja_vida_conductor",
    "validacion_emisor",  # copia del portal de verificación del emisor (no es uno de los documentos obligatorios)
    "desconocido",
]
ORDEN_SEVERIDAD = {"info": 0, "baja": 1, "media": 2, "alta": 3}


class Palabra(BaseModel):
    t: str
    bbox: BBox | None = None
    conf: float = 1.0
    fuente: str | None = None  # solo PDF nativo
    tam: float | None = None  # solo PDF nativo


class Bloque(BaseModel):
    bbox: BBox | None = None
    texto: str
    conf: float = 1.0
    palabras: list[Palabra] = []


class Tabla(BaseModel):
    bbox: BBox | None = None
    celdas: list[list[str]] = []


class OcrComparado(BaseModel):
    motor: str
    similitud: float


class Pagina(BaseModel):
    n: int
    ancho: float
    alto: float
    dpi: int | None = None  # None = coordenadas en puntos PDF; si no, píxeles a ese dpi
    rotacion: int = 0
    fuente_texto: Literal["nativo", "ocr", "docx"]
    motor: str
    bloques: list[Bloque] = []
    tablas: list[Tabla] = []
    ocr_comparado: OcrComparado | None = None
    calidad: dict[str, Any] = {}  # métricas de la imagen (nitidez, brillo, resolución, confianza OCR, rotación aplicada)


class Archivo(BaseModel):
    nombre: str
    sha256: str
    mime_real: str | None
    mime_declarado: str | None
    bytes: int
    paginas: int
    origen: Literal["pdf_nativo", "pdf_escaneado", "imagen", "docx", "doc", "desconocido"]
    metadatos: dict[str, Any] = {}  # productor, fechas, revisiones, firmas, EXIF o propiedades del Word


class Alternativa(BaseModel):
    tipo: TipoDocumento
    confianza: float


class Clasificacion(BaseModel):
    tipo: TipoDocumento = "desconocido"
    confianza: float = 0.0
    metodo: Literal["reglas", "estadistico", "llm", "manual"] = "reglas"
    alternativas: list[Alternativa] = []


class Codigo(BaseModel):
    tipo: Literal["qr", "pdf417", "mrz", "otro"]
    contenido: str
    datos: dict[str, Any] = {}
    pagina: int | None = None
    bbox: BBox | None = None
    valido: bool | None = None  # None = formato no reconocido (no es adulteración)


class Campo(BaseModel):
    valor: str | None
    valor_normalizado: Any = None
    pagina: int | None = None
    bbox: BBox | None = None
    conf: float = 1.0
    metodo: Literal["nativo", "regex", "zona", "ocr", "llm", "azure", "qr", "mrz"]
    regla_id: str | None = None
    evidencia: str | None = None


class Senal(BaseModel):
    id: str
    familia: Familia
    severidad: Severidad
    campo: str | None = None
    pagina: int | None = None
    bbox: BBox | None = None
    detalle: str
    evidencia: dict[str, Any] = {}


AccionObservacion = Literal["solicitar_reenvio", "solicitar_original", "solicitar_documento", "solicitar_vigente", "revisar"]


class Observacion(BaseModel):
    """El documento no se puede validar bien. No es una adulteración: el revisor gestiona una aclaración con el postulante
    (en el mockup: estado «Observado» → «Solicitar aclaración»)."""

    id: str  # p. ej. "calidad.imagen_borrosa", "contenido.campos_faltantes", "expediente.documento_faltante"
    motivo: Literal["calidad", "incompleto", "no_reconocido", "archivo", "vigencia", "no_verificable", "faltante"]
    detalle: str  # explicación para el revisor
    accion: AccionObservacion
    mensaje_postulante: str  # texto sugerido para la solicitud de aclaración
    documento: str | None = None  # tipo de documento (en las observaciones del expediente)
    campo: str | None = None
    pagina: int | None = None
    evidencia: dict[str, Any] = {}


ESTADOS_TEXTO = {"senal_alta": "Señal alta", "senal_media": "Señal media", "observado": "Observado", "sin_senales": "Sin señales"}


class Validacion(BaseModel):
    regla: str
    resultado: Literal["ok", "falla", "no_aplica"]
    campo: str | None = None
    detalle: str | None = None
    evidencia: dict[str, Any] = {}  # datos para que el revisor vea por qué (valor leído, cálculo, fechas, formato esperado)


class Versiones(BaseModel):
    pipeline: str
    reglas: str
    motores: dict[str, str] = {}
    modelo_llm: str | None = None


class ErrorEtapa(BaseModel):
    etapa: str
    mensaje: str


class Documento(BaseModel):
    contrato_version: str = CONTRATO_VERSION
    id: str
    expediente: str | None = None
    archivo: Archivo
    clasificacion: Clasificacion = Clasificacion()
    paginas: list[Pagina] = []
    codigos: list[Codigo] = []
    campos: dict[str, Campo] = {}
    senales: list[Senal] = []
    observaciones: list[Observacion] = []
    validaciones: list[Validacion] = []
    versiones: Versiones
    procesado_en: datetime
    duracion_ms: int = 0
    tiempos_ms: dict[str, int] = {}  # duración de cada etapa
    errores: list[ErrorEtapa] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resumen(self) -> dict[str, Any]:
        """Conteos para la webapp. No decide nada: solo resume los hallazgos."""
        por_sev = {s: 0 for s in ORDEN_SEVERIDAD}
        for s in self.senales:
            por_sev[s.severidad] += 1
        maxima = max((s.severidad for s in self.senales), key=ORDEN_SEVERIDAD.get, default=None)
        # estado para el revisor (prioridad: Señal alta > Señal media > Observado > Sin señales)
        estado = ("senal_alta" if por_sev["alta"] else "senal_media" if por_sev["media"]
                  else "observado" if self.observaciones else "sin_senales")
        return {
            "senales": len(self.senales),
            "por_severidad": por_sev,
            "severidad_max": maxima,
            "observaciones": len(self.observaciones),
            "validaciones_fallidas": sum(v.resultado == "falla" for v in self.validaciones),
            "estado": estado,
            "estado_texto": ESTADOS_TEXTO[estado],
            "requiere_revision": bool(por_sev["alta"] or por_sev["media"] or self.observaciones)
            or any(v.resultado == "falla" for v in self.validaciones),
            "vencido": self.vigencia["estado"] == "vencido",
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def vigencia(self) -> dict[str, Any]:
        """Vigencia del documento según las reglas `vigencia.*` (calculada al analizar, con la fecha de ese día).
        estado: vigente | vencido | sin_fecha (aplica pero no se leyó la fecha) | no_aplica (el tipo no vence).
        Un documento vencido además queda «Observado» con la observación contenido.vencido."""
        v = next((x for x in self.validaciones if x.regla.startswith("vigencia.")), None)
        if v is None:
            return {"estado": "no_aplica", "texto": "No aplica", "fecha_vencimiento": None, "dias": None, "regla": None,
                    "campo": None, "detalle": None}
        estado = {"falla": "vencido", "ok": "vigente"}.get(v.resultado, "sin_fecha")
        if estado == "sin_fecha" and v.campo == "fecha_control":
            # la hoja de vida solo trae «Fecha próximo control» en algunos casos: si no está, el documento no vence por esa vía
            return {"estado": "no_aplica", "texto": "Sin fecha de control en el documento", "fecha_vencimiento": None, "dias": None,
                    "regla": v.regla, "campo": v.campo, "detalle": v.detalle}
        vence, hoy = v.evidencia.get("vence"), v.evidencia.get("hoy")
        try:
            dias = (date.fromisoformat(vence) - date.fromisoformat(hoy)).days if vence and hoy else None
        except ValueError:
            dias = None
        texto = {"vigente": "Vigente", "vencido": "Vencido", "sin_fecha": "Sin fecha de vencimiento legible"}[estado]
        return {"estado": estado, "texto": texto, "fecha_vencimiento": vence if estado != "sin_fecha" else None, "dias": dias,
                "regla": v.regla, "campo": v.campo, "detalle": v.detalle}


# --- Expediente ---------------------------------------------------------------------------------


class DocumentoRef(BaseModel):
    id: str
    tipo: TipoDocumento
    sha256: str
    nombre: str


class Identidad(BaseModel):
    rut: str | None = None
    nombre: str | None = None
    fecha_nacimiento: str | None = None


class Discrepancia(BaseModel):
    campo: str
    documentos: list[str]
    valores: list[Any]
    similitud: float | None = None
    severidad: Severidad
    detalle: str


class Fuente(BaseModel):
    documento: str
    fuente: str
    estado: Literal["coincide", "no_coincide", "no_disponible", "pendiente_revisor"]
    folio: str | None = None
    codigo_verificacion: str | None = None
    url: str | None = None


class Duplicado(BaseModel):
    tipo: Literal["sha256", "phash_pagina"]
    documento: str
    otro_expediente: str
    otro_documento: str
    distancia: int = 0


class Expediente(BaseModel):
    contrato_version: str = CONTRATO_VERSION
    expediente: str
    documentos: list[DocumentoRef] = []
    faltantes: list[str] = []
    identidad: Identidad = Identidad()
    discrepancias: list[Discrepancia] = []
    fuentes: list[Fuente] = []
    duplicados: list[Duplicado] = []
    observaciones: list[Observacion] = []  # del expediente (faltantes) y de sus documentos vigentes
    procesado_en: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def estado(self) -> str:
        return "observado" if self.observaciones else "sin_observaciones"


def esquema_json() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"stracon-ocr contrato v{CONTRATO_VERSION}",
        "documento": Documento.model_json_schema(mode="serialization"),
        "expediente": Expediente.model_json_schema(mode="serialization"),
    }

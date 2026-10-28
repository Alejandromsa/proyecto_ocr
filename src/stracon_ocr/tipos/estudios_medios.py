from .base import NOMBRE, RUT, CampoSpec, TipoSpec

# Incluye los títulos de técnico de nivel medio que emite el Mineduc ("Certificado de título técnico profesional"): son
# estudios medios técnico-profesionales, no títulos de educación superior (calibración oct 2026).
NOMBRE_PERSONA = r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü'´ .]{4,80}?)"

TIPO = TipoSpec(
    clave="certificado_estudios_medios",
    nombre="Certificado de estudios medios",
    titulos=("Certificado de estudios medios", "Licencia de enseñanza media", "Licencia de educación media", "Certificado de estudios",
             "Certificado anual de estudios", "Concentración de notas", "Certificado de título técnico profesional"),
    palabras_clave=("Enseñanza media", "Educación media", "Ministerio de Educación", "Liceo", "Colegio", "Establecimiento educacional",
                    "Promedio", "Año de egreso", "Mineduc", "Rol base de datos", "Técnico de nivel medio", "Ayuda Mineduc",
                    "Secretaría regional ministerial de educación", "Año escolar", "Fines laborales"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("establecimiento", ("Establecimiento", "Establecimiento educacional", "Colegio", "Liceo")),
        CampoSpec("anio_egreso", ("Año de egreso", "Año egreso", "Año escolar", "Año"), "anio", clave=True),
        CampoSpec("folio", ("Folio", "N° de folio"), "codigo", clave=True),
        CampoSpec("promedio", ("Promedio", "Promedio general", "Promedio de notas"), "numero"),
        CampoSpec("fecha_emision", ("Fecha de emisión",), "fecha"),
    ),
    # certificados del Mineduc y títulos técnicos: el dato va en una frase
    patrones=(
        ("nombre", r"don\s*\(\s*[nñ]?a\s*\)\s*(.+?)\s*,?\s*R\.?\s*U\.?\s*N\b"),
        ("nombre", r"Por\s*cuanto\s*D\s*on\s*(?:\(\s*[nñ]?a\s*\))?\s*" + NOMBRE_PERSONA + r"\s*(?:R\.?\s*U\.?\s*N|egresad)"),
        ("nombre", r"\bDon\s*\(\s*a\s*\)\s*:\s*" + NOMBRE_PERSONA + r"\s*\.?-?\s*quien"),
        ("establecimiento", r"establecimiento\s*educacional\s*(.+?)\s*,\s*comuna"),
        ("establecimiento", r"egresad[oa]\s*del?\s*((?:Colegio|Liceo|Instituto|Escuela|Complejo)\b.+?)(?:\s+Rol\b|\s+ha\s+cumplido|\s*,|$)"),
        ("anio_egreso", r"Registro\s*N\s*[°º]?\s*\d+\s*del\s*a[ñn]o\s*(\d{4})"),
        ("anio_egreso", r"Registro\s*(?:respectivo\s*)?N\s*[°º]?\s*\d+\s*(?:del\s*)?a[ñn]o\s*(\d{4})"),
        ("anio_egreso", r"a[ñn]o\s*escolar\s*(\d{4})"),
        ("anio_egreso", r"Registro\s*N\s*[°º]?\s*\d+\s*de\s*fecha\s*\d{1,2}/\d{1,2}/(\d{4})"),
        ("anio_egreso", r"con\s*fecha\s*\d{1,2}/\d{1,2}/(\d{4})\s*ha\s*rendido"),
        ("anio_egreso", r"de\s*fecha\s*\d{1,2}\s*de\s*\w+\s*de\s*(\d{4})"),
        ("fecha_emision", r"fecha\s*de\s*emisi[oó]n\s*:?\s*(\d{1,2}\s*de\s*[a-záéíóú]+\s*de\s*\d{4})"),
    ),
    obligatorios=("nombre", "rut"),
)

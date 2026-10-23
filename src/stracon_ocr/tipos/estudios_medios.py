from .base import NOMBRE, RUT, CampoSpec, TipoSpec

TIPO = TipoSpec(
    clave="certificado_estudios_medios",
    nombre="Certificado de estudios medios",
    titulos=("Certificado de estudios medios", "Licencia de enseñanza media", "Licencia de educación media", "Certificado de estudios",
             "Certificado anual de estudios", "Concentración de notas"),
    palabras_clave=("Enseñanza media", "Educación media", "Ministerio de Educación", "Liceo", "Colegio", "Establecimiento educacional",
                    "Promedio", "Año de egreso", "Mineduc", "Rol base de datos", "Técnico de nivel medio"),
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
        ("establecimiento", r"establecimiento\s*educacional\s*(.+?)\s*,\s*comuna"),
        ("establecimiento", r"egresad[oa]\s*del?\s*((?:Colegio|Liceo|Instituto|Escuela|Complejo)\b.+?)(?:\s+Rol\b|\s*,|$)"),
        ("anio_egreso", r"Registro\s*N\s*[°º]?\s*\d+\s*del\s*a[ñn]o\s*(\d{4})"),
        ("anio_egreso", r"con\s*fecha\s*\d{1,2}/\d{1,2}/(\d{4})\s*ha\s*rendido"),
        ("anio_egreso", r"de\s*fecha\s*\d{1,2}\s*de\s*\w+\s*de\s*(\d{4})"),
        ("fecha_emision", r"fecha\s*de\s*emisi[oó]n\s*:?\s*(\d{1,2}\s*de\s*[a-záéíóú]+\s*de\s*\d{4})"),
    ),
    obligatorios=("nombre", "rut"),
)

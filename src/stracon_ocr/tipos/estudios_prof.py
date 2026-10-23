from .base import NOMBRE, RUT, CampoSpec, TipoSpec

TIPO = TipoSpec(
    clave="certificado_estudios_profesionales",
    nombre="Certificado de estudios profesionales",
    titulos=("Certificado de estudios profesionales", "Certificado de título", "Certificado de título profesional", "Certificado de competencias"),
    palabras_clave=("Título", "Universidad", "Instituto profesional", "Centro de formación técnica", "CEIM", "Carrera", "Área", "Vigencia",
                    "Registro de títulos", "Secretario general"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("numero_certificado", ("N° de certificado", "N° certificado", "Folio", "N° de registro"), "codigo", clave=True),
        CampoSpec("institucion", ("Institución", "Institución emisora")),
        CampoSpec("titulo", ("Título", "Título profesional", "Carrera")),
        CampoSpec("area", ("Área", "Especialidad")),
        CampoSpec("fecha_titulacion", ("Fecha de titulación", "Fecha de título"), "fecha"),
        CampoSpec("fecha_emision", ("Fecha de emisión", "Emitido el"), "fecha", clave=True),
        CampoSpec("vigencia", ("Vigencia", "Válido hasta", "Vigente hasta"), "fecha", clave=True),
    ),
    # certificados de título de institutos y universidades: el dato va en una frase
    patrones=(
        ("nombre", r"confiri[oó]\s*a\s*(?:don|do[ñn]a|do\w?a)\s*:?\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ]+?)\s+(?:el|la)\s+t[ií]tulo"),
        ("titulo", r"(?:el|la)\s+t[ií]tulo\s+de\s+(.+?)\s*,\s*y\s+fue"),
        ("fecha_titulacion", r"con\s+fecha\s+(\d{1,2}\s+de\s+[a-záéíóú]+\s+de\s+\d{4})"),
        ("numero_certificado", r"\bN\s*[°º]?\s*([A-Z]?\d{3,})\s+del\s+registro\s+de\s+t[ií]tulos"),
        ("institucion", r"\b((?:Instituto\s+Profesional|Centro\s+de\s+Formaci[oó]n\s+T[eé]cnica)\s+[A-ZÁÉÍÓÚÑ]{2,}"
                        r"(?:\s+(?!Instituto|Universidad|Centro|El\b|La\b|De\b|Del\b)[A-ZÁÉÍÓÚÑ]{2,})?)"),
        ("institucion", r"\b(Universidad\s+(?:de\s+|del\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)?)"),
    ),
    obligatorios=("nombre",),
)

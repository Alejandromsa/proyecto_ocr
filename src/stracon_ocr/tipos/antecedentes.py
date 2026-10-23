from .base import NOMBRE, RUT, CampoSpec, TipoSpec

TIPO = TipoSpec(
    clave="certificado_antecedentes",
    nombre="Certificado de antecedentes",
    titulos=("Certificado de antecedentes", "Certificado antecedentes para fines"),
    palabras_clave=("Antecedentes", "Registro Civil", "Código de verificación", "Sin antecedentes", "Fines especiales", "Fines particulares",
                    "Registro general de condenas"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("folio", ("Folio", "N° de folio"), "codigo", clave=True),
        CampoSpec("codigo_verificacion", ("Código de verificación", "Código verificación", "Código"), "codigo", clave=True),
        CampoSpec("fecha_emision", ("Fecha de emisión", "Fecha emisión"), "fecha", clave=True),
        CampoSpec("resultado", ("Resultado",)),
        CampoSpec("fines", ("Válido para",)),
    ),
    patrones=(
        ("fines", r"v[aá]lido\s*para\s*fines\s*(especiales|particulares)"),
    ),
    obligatorios=("rut", "folio", "codigo_verificacion", "fecha_emision"),
)

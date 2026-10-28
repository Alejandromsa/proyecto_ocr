# stracon-ocr

OCR, forense documental y extracción a JSON para la validación documental de postulantes (STRACON).
Es la pieza real detrás del **Agente 1** (señales de adulteración) y de la parte determinista del **Agente 2**
(contraste entre documentos) del mockup `stracon-validacion-documental`.

La librería **no decide**: entrega hallazgos con evidencia (página, caja, valores) y la decisión es del revisor.

## Qué hace

| Etapa | Qué produce |
|---|---|
| Ingesta | SHA-256, MIME real, páginas normalizadas de PDF, JPG/PNG/HEIC/TIFF, DOCX y DOC (vía LibreOffice) |
| Parseo | Texto nativo con fuente y tamaño por carácter (pdfplumber) u OCR con caja y confianza por palabra (RapidOCR sobre OpenVINO). En PDF nativo también hace OCR del render para comparar lo declarado con lo visible |
| Códigos | QR y PDF417 (zxing-cpp), MRZ TD1 con dígitos ICAO 9303 |
| Clasificación | Seis tipos del MVP por reglas (títulos y frases típicas) |
| Extracción | Campos por rótulo y posición, con procedencia (página, caja, confianza, método, regla) |
| Forense | Revisiones incrementales, productor editor, fechas e Info/XMP, firmas PAdES, tipografías mezcladas en una palabra, texto encimado, objetos que tapan texto, campos rasterizados, imágenes borrosas insertadas, texto nativo ≠ OCR, Word editable con cambios rastreados y rsid, EXIF de editor, ELA por campo, duplicados por SHA-256 y pHash |
| Validación | RUT módulo 11, MRZ, QR frente al OCR, fechas lógicas, vigencias, formato de folio y código |
| Contraste | Por expediente: identidad, RUT, nombre (rapidfuzz), fecha de nacimiento, clases de licencia, fuentes a verificar, duplicados |
| Almacén | SQLite (JSONB + FTS5) detrás de una interfaz `Repositorio`; archivos originales por hash |

**Manual de uso detallado: [docs/manual-de-uso.md](docs/manual-de-uso.md)**

Contrato del JSON: [docs/contrato-json.md](docs/contrato-json.md) · Señales: [docs/catalogo-senales.md](docs/catalogo-senales.md) ·
Análisis de librerías: [docs/analisis-librerias-ocr.md](docs/analisis-librerias-ocr.md) · Integración con la webapp: [docs/api-webapp.md](docs/api-webapp.md)

## Instalación (Windows 11, Python 3.11)

```powershell
python -m pip install --user uv
python -m uv sync --extra tesseract --extra firmas
```

Opcionales fuera de pip:
- **Tesseract 5.4** (instalador UB Mannheim, con `spa.traineddata`): activa el segundo motor forense (`ocr.motores_discrepan`). Sin él, todo funciona con RapidOCR.
  Sin permisos de administrador: extraer el instalador con 7-Zip en `%LOCALAPPDATA%\Programs\Tesseract-OCR` (la librería lo
  busca ahí) y copiar `spa.traineddata` de github.com/tesseract-ocr/tessdata a su carpeta `tessdata`. Así está instalado en este equipo.
- **LibreOffice**: solo para archivos `.doc` antiguos.
- **Ollama** + `SOCR_OLLAMA_MODELO=qwen3.5:4b`: respaldo LLM para campos faltantes (apagado por defecto).
- **Azure Document Intelligence**: `uv sync --extra azure`, `SOCR_AZURE_ENDPOINT`, `SOCR_AZURE_KEY` y `--motor azure` (envía la página a la nube: solo con documentos ficticios).

## Uso

```powershell
# API para la webapp + consola de prueba en http://127.0.0.1:8000 (documentación en /docs)
.venv\Scripts\socr servir

# Un archivo o una carpeta
.venv\Scripts\socr procesar datos\dorado\12794536-5\2-licencia-conducir.pdf -d
# Un expediente completo con contraste entre documentos
.venv\Scripts\socr expediente datos\dorado\12794536-5 --salida exportado\12794536-5
# Buscar en la base
.venv\Scripts\socr buscar "transito"
.venv\Scripts\socr buscar --rut 12794536-5
# Medir detección contra la verdad conocida del mockup
.venv\Scripts\socr evaluar
# Pruebas
.venv\Scripts\python -m pytest
```

Desde Python:

```python
from stracon_ocr.pipeline import Procesador
doc = Procesador().procesar("archivo.pdf", expediente="12794536-5")
print(doc.resumen, [s.detalle for s in doc.senales])
```

## Convocatorias de SharePoint

El mockup `stracon.validacion.documental` pide analizar una convocatoria completa (`<biblioteca>/<convocatoria>/<cargo>/<postulante>/<archivos>`):
`POST /convocatorias` (202), `GET /convocatorias/{id}` (avance) y `GET /convocatorias/{id}/resultados` (cargos, postulantes, documentos y
contraste por postulante). El servicio lee SharePoint por el flujo de Power Automate (`listDeepest` y `getFile`), procesa cada archivo con los
mismos trabajadores de la API y deja el resultado en JSON bajo `SOCR_RESULTADOS`. La URL del flujo es un secreto: llega en cada petición y no
se guarda ni se escribe en logs, JSON ni mensajes de error. Contrato y reglas en [docs/api-convocatorias.md](docs/api-convocatorias.md).

## Configuración (variables de entorno)

| Variable | Defecto | Uso |
|---|---|---|
| `SOCR_DB` | `datos/stracon-ocr.db` | Base SQLite (los originales quedan en `datos/archivos/`) |
| `SOCR_DPI` | `200` | Resolución de render para OCR y forense visual |
| `SOCR_DPI_REINTENTO` | `400` | Resolución del reintento cuando un PDF escaneado de un tipo conocido sale sin campos obligatorios (p. ej. el nombre); se conserva la lectura nueva solo si mejora. `0` = sin reintento |
| `SOCR_MOTOR` | `rapidocr` | `rapidocr`, `tesseract` o `azure` |
| `SOCR_COMPARAR_OCR` | `1` | En PDF nativo, OCR del render para comparar |
| `SOCR_SEGUNDO_MOTOR` | `1` | Tesseract en páginas OCR, si está instalado |
| `SOCR_TRABAJADORES` | `2` | Procesos de trabajo de la API: documentos que se procesan a la vez; el resto espera en cola sin errores. `0` = en el mismo proceso |
| `SOCR_HILOS_OCR` | `-1` | Hilos del OCR por proceso; `-1` = la mitad de los núcleos lógicos repartida entre los trabajadores |
| `SOCR_CORS` | `*` | Orígenes permitidos para la webapp, separados por coma |
| `SOCR_MAX_MB` | `25` | Tamaño máximo por archivo: subidas a la API y archivos que se descargan de una convocatoria (los mayores quedan `omitido`) |
| `SOCR_RESULTADOS` | `datos/resultados` | Raíz de los JSON por convocatoria (`<id>/convocatoria.json`, `<id>/<cargo>/<postulante>/<archivo>.json`); puede contener datos personales y no se versiona |
| `SOCR_FLUJO_HOSTS` | `logic.azure.com,powerplatform.com` | Dominios permitidos para la URL del flujo de Power Automate de las convocatorias. Solo para pruebas locales se puede agregar `127.0.0.1` o `localhost` (únicos hosts que aceptan http) |
| `SOCR_REGLAS` | `src/stracon_ocr/reglas.json` | Umbrales, editores sospechosos, formatos de folio, fuentes |

Los formatos de folio y código de `reglas.json` son los del Registro Civil real (folio de 12 dígitos y código de 12
hexadecimales). Los de otros emisores (Mineduc, instituciones) aún no tienen regla: ver `docs/formatos-emisores.md`.
Los folios ficticios de `datos/dorado` solo existen en las pruebas (`reglas_mockup` en `tests/conftest.py`).

## Estructura

```
src/stracon_ocr/
  contrato.py      modelos Pydantic del JSON (v1.0)        pipeline.py   orquestación y tiempos por etapa
  ingesta.py       tipos de archivo y páginas              layout.py     palabras, segmentos y texto encimado
  motores/         pdf_nativo, rapidocr, tesseract, azure_di
  codigos.py       QR, PDF417, MRZ                         clasificacion.py · extraccion.py · tipos/ (6 tipos)
  forense/         pdf_metadatos, pdf_estructura, docx, imagen, ocr_senales
  validacion.py · contraste.py · llm.py (Ollama opcional)
  almacen/         repositorio.py (interfaz), sqlite.py, resultados.py (JSON por convocatoria: interfaz ResultadosJSON)
  convocatorias.py · flujo.py   análisis de convocatorias completas de SharePoint (cliente del flujo de Power Automate)
  api.py · vista.py · web/index.html · cli.py · reglas.json
herramientas/generar_verdad_mockup.py   datos/dorado/ (PDF del mockup)   tests/
```

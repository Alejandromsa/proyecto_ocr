# Manual de uso de stracon-ocr

Versión 0.1.0 · contrato JSON 1.0 · reglas 2026.10.21 · 22 de octubre de 2026

## Contenido

1. [Qué es y qué no hace](#1-qué-es-y-qué-no-hace)
2. [Instalación](#2-instalación)
3. [Consola web de prueba](#3-consola-web-de-prueba)
4. [Línea de comandos](#4-línea-de-comandos-socr)
5. [API HTTP para la webapp](#5-api-http-para-la-webapp)
6. [Uso desde Python](#6-uso-desde-python)
7. [Cómo leer el resultado](#7-cómo-leer-el-resultado)
8. [Configuración](#8-configuración)
9. [Datos guardados](#9-datos-guardados)
10. [Evaluación y calibración](#10-evaluación-y-calibración)
11. [Cómo extenderlo](#11-cómo-extenderlo)
12. [Solución de problemas](#12-solución-de-problemas)
13. [Limitaciones conocidas](#13-limitaciones-conocidas)

---

## 1. Qué es y qué no hace

`stracon-ocr` recibe los documentos de un postulante (PDF, foto, escaneo o Word) y devuelve un JSON con:

- **El texto** de cada página, con su ubicación.
- **El tipo de documento:** cédula, licencia, estudios medios, estudios profesionales, antecedentes u hoja de vida del conductor.
- **Los campos** (RUT, nombre, folio, fechas…), cada uno con la página y la caja donde se leyó.
- **Las señales de posible adulteración**, con severidad y evidencia.
- **Las validaciones:** dígito verificador del RUT, fechas, vigencias y formatos.
- **El contraste entre los documentos** del mismo postulante: identidad, discrepancias, documentos faltantes y folios a verificar.

**No decide.** Ninguna señal aprueba ni rechaza un documento: son hallazgos para que el revisor los valide.
**No consulta a los emisores.** El Registro Civil y CEIM no tienen API pública: la librería deja el folio, el código y el enlace,
y el estado de esa verificación queda como `pendiente_revisor`. «Fuente no disponible» nunca es una discrepancia.

Hay tres formas de usarlo:

| Forma | Para quién | Sección |
|---|---|---|
| Consola web de prueba (`http://127.0.0.1:8000`) | Probar documentos y mostrar resultados sin programar | [3](#3-consola-web-de-prueba) |
| Línea de comandos `socr` | Procesar carpetas, exportar, evaluar | [4](#4-línea-de-comandos-socr) |
| API HTTP | La webapp | [5](#5-api-http-para-la-webapp) |

---

## 2. Instalación

### 2.1 Requisitos

- Windows 10/11, macOS o Linux. Probado en Windows 11 con un i7-1270P y 16 GB de RAM, sin GPU.
- **Python 3.11** (no 3.12: algunas dependencias se fijaron para 3.11).
- Unos 2 GB libres para el entorno y los modelos de OCR.
- Internet **solo para instalar**. El procesamiento es 100 % local, salvo que se active Azure a propósito.

### 2.2 Pasos

Todos los comandos se ejecutan **desde la carpeta del proyecto** (`C:\Users\<usuario>\Desktop\stracon-ocr`). Las rutas por
defecto de la base de datos son relativas a esa carpeta.

```powershell
cd C:\Users\<usuario>\Desktop\stracon-ocr

# 1. Instalar uv (gestor de paquetes). En este equipo uv no queda en el PATH: se usa como "python -m uv"
python -m pip install --user uv

# 2. Crear el entorno e instalar todo (OCR, forense, API, pruebas)
python -m uv sync --extra tesseract --extra firmas
```

`uv sync` crea la carpeta `.venv` con Python 3.11 y todas las dependencias. Los ejecutables quedan en `.venv\Scripts\`.

### 2.3 Verificar la instalación

```powershell
.venv\Scripts\socr --help                 # lista los comandos
.venv\Scripts\python -m pytest -q         # 23 pruebas; ~1 minuto; deben pasar todas
```

Las pruebas usan los PDF de `datos\dorado`. Si esa carpeta no existe, las pruebas que la necesitan se saltan: la carpeta
se arma copiando `stracon-validacion-documental\expedientes\*`.

### 2.4 Componentes opcionales

| Componente | Para qué | Cómo activarlo |
|---|---|---|
| **Tesseract 5.4** (instalado en este equipo) | Segundo motor de OCR en fotos y escaneos: compara su lectura con la de RapidOCR en los campos clave (señal `ocr.motores_discrepan`) | Instalador de UB Mannheim marcando el idioma **Spanish**. Sin permisos de administrador: extraer el instalador con 7-Zip en `%LOCALAPPDATA%\Programs\Tesseract-OCR` y copiar `spa.traineddata` (github.com/tesseract-ocr/tessdata) a su carpeta `tessdata`. Se detecta solo en esas dos rutas; si está en otra, `SOCR_TESSERACT=<ruta a tesseract.exe>`. La consola muestra «tesseract ✓» |
| **LibreOffice** | Leer archivos `.doc` (Word antiguo) | Instalarlo. Se detecta solo `soffice` |
| **Ollama** | Respaldo con un LLM local para campos que las reglas no encuentran | Instalar Ollama, `ollama pull qwen3.5:4b` y `SOCR_OLLAMA_MODELO=qwen3.5:4b`. Suma 30–60 s por documento |
| **Azure Document Intelligence** | Comparar con un OCR en la nube | `python -m uv sync --extra tesseract --extra firmas --extra azure`, variables `SOCR_AZURE_ENDPOINT` y `SOCR_AZURE_KEY`, y elegir el motor `azure`. **Envía las páginas a la nube**: solo con documentos ficticios hasta tener autorización |

Sin ninguno de estos, todo funciona con RapidOCR.

---

## 3. Consola web de prueba

### 3.1 Arrancar

```powershell
.venv\Scripts\socr servir
```

Abrir **http://127.0.0.1:8000** en el navegador. La primera vez tarda unos segundos más porque carga los modelos de OCR.
Para detenerla: `Ctrl+C` en la terminal.

La barra superior muestra la versión, el contrato, las reglas y qué motores están disponibles (✓ / ✗).

### 3.2 Procesar documentos

1. Arrastrar uno o varios archivos al recuadro **Procesar**, o hacer clic para elegirlos (PDF, JPG, PNG, HEIC, TIFF, WEBP, DOCX, DOC).
2. **Expediente (RUT del postulante):** opcional pero recomendado. Agrupa los documentos y permite el contraste entre ellos.
   Se escribe sin puntos, con guion: `12794536-5`.
3. **Tipo de documento:** dejar en «Clasificar automáticamente». Elegir un tipo solo si la clasificación se equivoca.
4. Pulsar **Procesar**. Tarda unos 3 s por página.

Con varios archivos y un RUT, se procesan juntos y se calcula el contraste del expediente al final.

### 3.3 Leer el resultado

Al terminar se abre la ficha del documento:

- **Cabecera:** nombre del archivo y un indicador:
  - «Requiere revisión» (rojo o naranjo): hay señales de severidad media o alta.
  - «Revisar validaciones»: no hay señales, pero falla alguna regla (por ejemplo, un formato de folio).
  - «Sin hallazgos».
- **Datos del procesamiento:** tipo detectado y su confianza, origen (PDF nativo, escaneado, imagen o Word), páginas,
  tiempo y enlace al **original**.
- **Imagen de la página:** las señales aparecen en cajas numeradas (rojo = alta, naranjo = media, amarillo = baja) y los
  campos extraídos en azul. Con varias páginas, los botones de arriba cambian de página.
- **Señales:** la lista numerada coincide con los números de la imagen. Cada una trae severidad, identificador, campo,
  página y una explicación en lenguaje simple.
- **Campos extraídos:** el valor tal como aparece, el valor normalizado (RUT sin puntos, fechas ISO) y el método con su
  confianza (ver [7.3](#73-campos)).
- **Validaciones:** `ok`, `falla` o `no_aplica`, con el detalle.
- **Códigos:** QR, PDF417 o MRZ, si se encontraron.
- **Metadatos del archivo** y **JSON completo** (desplegables).
- **Expediente**, si el documento tiene RUT: identidad, faltantes, discrepancias y fuentes a verificar, con su enlace.

### 3.4 Lista de documentos y búsqueda

El panel **Documentos** muestra los últimos procesados. En el campo de filtro:

- Un RUT (`12794536-5`) lista solo los documentos de ese expediente.
- Cualquier otro texto busca dentro del contenido de los documentos, sin importar las tildes (`transito` encuentra «Tránsito»).

**Contrastar expediente** recalcula el contraste del RUT escrito en «Expediente», con todos sus documentos guardados.

---

## 4. Línea de comandos (`socr`)

Todos los comandos: `.venv\Scripts\socr <comando> --help` muestra sus opciones.

### 4.1 `procesar`: un archivo o una carpeta

```powershell
.venv\Scripts\socr procesar datos\dorado\12794536-5\2-licencia-conducir.pdf -d
```

| Opción | Uso |
|---|---|
| `RUTA` | Un archivo, o una carpeta (procesa todos sus archivos soportados) |
| `--expediente 12794536-5` | RUT del postulante. Con una carpeta, por defecto es el nombre de la carpeta |
| `--tipo licencia_conducir` | Fuerza el tipo (ver claves en [7.2](#72-tipos-y-campos)) |
| `--motor rapidocr` | `rapidocr` (defecto), `tesseract` o `azure` |
| `--salida carpeta` | Escribe un JSON por archivo |
| `--no-guardar` | No lo guarda en la base: solo muestra el resultado |
| `-d` / `--detalle` | Muestra campos, señales, validaciones fallidas y errores |

Salida típica:

```
2-licencia-conducir.pdf   licencia_conducir   campos= 6 señales=[alta:1] 2722 ms
    folio                  'LC-2023-118407' → 'LC-2023-118407' (zona, 1.00)
    [ALTA ] pdf.fuentes_mezcladas_campo: 'LC-2023-118407' mezcla tipografías: '7' en CourierNewPS-ItalicMT 17.25 pt dentro de Georgia-Bold (campo folio)
```

### 4.2 `expediente`: un postulante completo con contraste

```powershell
.venv\Scripts\socr expediente datos\dorado\12794536-5 --salida exportado\12794536-5
```

Procesa todos los archivos de la carpeta y muestra la identidad, los faltantes, las discrepancias, las fuentes a verificar
y los duplicados con otros expedientes. Con `--salida` escribe un JSON por documento y `expediente.json`.
`--rut` cambia el RUT si la carpeta no se llama como él.

### 4.3 `buscar`

```powershell
.venv\Scripts\socr buscar "sin antecedentes"     # texto completo, sin tildes
.venv\Scripts\socr buscar --rut 12794536-5        # documentos de un expediente
.venv\Scripts\socr buscar --tipo cedula_identidad
```

La búsqueda acepta la sintaxis de SQLite FTS5: `"frase exacta"`, `palabra1 AND palabra2`, `prefijo*`.

### 4.4 `exportar`

```powershell
.venv\Scripts\socr exportar <id-del-documento> --salida exportado
.venv\Scripts\socr exportar --rut 12794536-5 --salida exportado\12794536-5
```

### 4.5 `servir`

```powershell
.venv\Scripts\socr servir                                  # http://127.0.0.1:8000, solo este equipo
.venv\Scripts\socr servir --host 0.0.0.0 --puerto 8080     # visible en la red local (sin autenticación: cuidado)
```

### 4.6 `esquema`

```powershell
.venv\Scripts\socr esquema --salida docs\contrato-v1.schema.json
```

Exporta el JSON Schema del contrato, para validar el JSON desde otros sistemas.

### 4.7 `evaluar`

Ver la [sección 10](#10-evaluación-y-calibración).

---

## 5. API HTTP para la webapp

Con `socr servir` corriendo, la documentación interactiva queda en **http://127.0.0.1:8000/docs**: cada endpoint se puede
probar desde el navegador con «Try it out». La guía completa de integración (flujo, campos del JSON y correspondencia con el
mockup) está en [api-webapp.md](api-webapp.md).

### 5.1 Endpoints principales

| Método y ruta | Qué hace |
|---|---|
| `GET /salud` | Estado, versión y motores disponibles |
| `GET /tipos` | Tipos de documento y sus campos |
| `POST /documentos` | Procesa un archivo y devuelve el JSON (espera el resultado) |
| `POST /trabajos` | Encola uno o más archivos y responde al instante; luego `GET /trabajos/{id}` |
| `POST /expedientes/{rut}/documentos` | Procesa varios archivos de un postulante y devuelve el contraste |
| `POST /expedientes/{rut}/contraste` · `GET /expedientes/{rut}` | Recalcula o lee el contraste |
| `GET /documentos` · `GET /documentos/{id}` | Lista resumida · JSON completo |
| `GET /documentos/{id}/paginas/{n}/imagen` | PNG de la página con las señales y los campos dibujados |
| `GET /documentos/{id}/archivo` | Archivo original |
| `GET /buscar?q=` | Búsqueda de texto completo |

### 5.2 Ejemplos con `curl.exe` (viene con Windows)

```powershell
# Un documento (espera el resultado)
curl.exe -X POST http://127.0.0.1:8000/documentos -F "archivo=@datos\dorado\12794536-5\2-licencia-conducir.pdf" -F "expediente=12794536-5"

# Todo un expediente
curl.exe -X POST http://127.0.0.1:8000/expedientes/12794536-5/documentos `
  -F "archivos=@datos\dorado\12794536-5\1-cedula-identidad.pdf" `
  -F "archivos=@datos\dorado\12794536-5\2-licencia-conducir.pdf"

# En segundo plano: encolar y consultar
curl.exe -X POST http://127.0.0.1:8000/trabajos -F "archivos=@foto.jpg" -F "expediente=12794536-5"
curl.exe "http://127.0.0.1:8000/trabajos/<id>?incluir_documento=true"

# Imagen con las señales
curl.exe -o pagina1.png "http://127.0.0.1:8000/documentos/<id>/paginas/1/imagen?dpi=110"
```

Campos de `POST /documentos`: `archivo` (obligatorio), `expediente`, `tipo`, `motor` y `guardar` (`false` para no guardar).

### 5.3 Códigos de error

| Código | Causa |
|---|---|
| 400 | Archivo vacío, motor no disponible (por ejemplo, `tesseract` sin instalar) o búsqueda mal escrita |
| 404 | Documento, página, trabajo o expediente inexistente |
| 413 | El archivo supera `SOCR_MAX_MB` (25 MB por defecto) |
| 415 | Formato no soportado (por ejemplo, `.txt` o `.xlsx`) |
| 422 | Tipo de documento desconocido |

Si una etapa interna falla (por ejemplo, la lectura de códigos), la respuesta igual es 200: el error queda en
`errores[]` y el resto del JSON es válido.

---

## 6. Uso desde Python

```python
from stracon_ocr.pipeline import Procesador

proc = Procesador()                                   # usa datos/stracon-ocr.db
doc = proc.procesar("ruta/archivo.pdf", expediente="12794536-5")

print(doc.clasificacion.tipo, doc.resumen)
for s in doc.senales:
    print(s.severidad, s.id, s.detalle)
print(doc.campos["rut"].valor_normalizado)

docs, exp = proc.procesar_carpeta("datos/dorado/12794536-5")   # expediente completo
print(exp.discrepancias, exp.faltantes)

sin_bd = Procesador(guardar=False)                    # no guarda nada
json_texto = sin_bd.procesar("archivo.pdf").model_dump_json(indent=2)
```

`procesar` también acepta bytes: `proc.procesar(datos=contenido, nombre="archivo.pdf")`.

---

## 7. Cómo leer el resultado

El detalle de cada campo del JSON está en [contrato-json.md](contrato-json.md). Esta sección explica cómo interpretarlo.

### 7.1 Severidad de las señales

| Severidad | Significado | Qué hacer |
|---|---|---|
| **alta** | Indicio fuerte de alteración (tipografía distinta dentro de una palabra, texto encimado, PDF modificado después de emitido, editor de PDF) | Revisar la evidencia y la imagen; pedir aclaración o el original si se confirma |
| **media** | Hay que mirar (imagen borrosa insertada, fechas de metadatos incoherentes, confianza OCR anómala) | Revisar; suele tener explicación legítima |
| **baja** | Contexto (ELA en una foto, motores OCR que leen distinto) | Solo como apoyo |
| **info** | Se registra, no requiere acción | — |

`resumen.requiere_revision` es verdadero si hay alguna señal media o alta, alguna observación o alguna validación que falla.

**Estado del documento** (`resumen.estado_texto`), con esta prioridad:

| Estado | Significa | Qué hace el revisor |
|---|---|---|
| **Señal alta** | Hay un indicio fuerte de adulteración | Revisar la evidencia (sección 7.5) |
| **Señal media** | Hay un indicio que conviene mirar | Revisar |
| **Observado** | El documento **no se puede validar** (ilegible, incompleto, solo el reverso, imagen de un documento electrónico, vencido, archivo dañado) | Pedir aclaración al postulante con el mensaje sugerido |
| **Sin señales** | Nada que observar | Confirmar |

### 7.2 Observaciones: cuándo pedir una aclaración

Una observación no es una adulteración: indica que falta evidencia para validar. Cada una trae el **motivo**, la
**acción** y un **mensaje para el postulante** que se puede copiar tal cual (botón «Copiar mensaje» en la consola).

| Observación | Cuándo aparece | Acción |
|---|---|---|
| `archivo.ilegible` | El archivo está dañado o protegido con contraseña | Solicitar reenvío |
| `calidad.*` | La foto o el escaneo no se lee bien (borroso, oscuro, sobreexpuesto, baja resolución) | Solicitar reenvío |
| `contenido.tipo_no_reconocido` | No se reconoce qué documento es | Solicitar reenvío |
| `contenido.campos_faltantes` | No se leen datos obligatorios (por ejemplo, el RUT o el folio) | Solicitar reenvío |
| `contenido.solo_reverso` | Llegó solo el reverso de la licencia o la cédula | Solicitar el frente |
| `contenido.imagen_de_electronico` | Llegó una foto o un escaneo de un certificado electrónico | Solicitar el PDF original |
| `contenido.vencido` | El documento está vencido (antecedentes con más de 60 días, licencia vencida) | Solicitar documento vigente |
| `expediente.documento_faltante` | Falta uno de los 6 documentos del expediente | Solicitar el documento |

En el expediente, un reverso suelto no queda observado si también llegó el anverso de ese documento. Cuando el postulante
envía la versión corregida, se procesa de nuevo y el estado se recalcula.

### 7.2.1 Tipos y campos

| Clave | Documento | Campos (además de `nombre` y `rut`) |
|---|---|---|
| `cedula_identidad` | Cédula de identidad | nombres, apellidos, numero_documento, fecha_nacimiento, fecha_emision, fecha_vencimiento, nacionalidad, sexo |
| `licencia_conducir` | Licencia de conducir | clases, folio, fecha_emision, fecha_vencimiento, municipalidad, restricciones |
| `certificado_estudios_medios` | Certificado de estudios medios | establecimiento, anio_egreso, folio, promedio |
| `certificado_estudios_profesionales` | Certificado de estudios profesionales | numero_certificado, institucion, titulo, area, fecha_titulacion, fecha_emision, vigencia |
| `certificado_antecedentes` | Certificado de antecedentes | folio, codigo_verificacion, fecha_emision, resultado |
| `hoja_vida_conductor` | Hoja de vida del conductor | folio, codigo_verificacion, clases, fecha_control, fecha_emision, anotaciones |

`desconocido` significa que no se reconoció el tipo: se puede reprocesar indicando el tipo.

### 7.3 Campos

| Método | Cómo se obtuvo | Confianza |
|---|---|---|
| `zona` | Junto a su rótulo (a la derecha o debajo) | 1,0 en PDF nativo; la del OCR en imágenes |
| `regex` | En una frase del texto (por ejemplo, el primer RUT válido) | Ídem |
| `ocr` | En un PDF con texto, el valor solo estaba como imagen | La del OCR; va acompañado de `pdf.campo_rasterizado` |
| `llm` | LLM local de respaldo (si está activo) | 0,5 fija |

Un campo que no aparece en `campos` no se encontró. Las validaciones que dependen de él quedan en `no_aplica`.

### 7.4 Señales más frecuentes

| Señal | Lo que vio | Cómo verificarlo |
|---|---|---|
| `pdf.fuentes_mezcladas_campo` | Un carácter con otra tipografía o tamaño dentro de una palabra | Ampliar la imagen: el carácter se ve distinto. La evidencia dice cuál y en qué fuente |
| `pdf.texto_superpuesto` | Dos textos distintos en el mismo lugar | La evidencia trae lo visible y lo que hay debajo |
| `pdf.objeto_tapa_texto` | Un rectángulo blanco o una imagen sobre texto que no se ve | El texto oculto viene en la evidencia |
| `pdf.campo_rasterizado` | El valor de un campo es una imagen y no texto | Comparar con el resto de los campos, que sí son texto |
| `pdf.imagen_baja_nitidez` | Firma, timbre o dato pegado con poca resolución | Mirar la caja en la imagen |
| `pdf.revisiones_incrementales` | El PDF se modificó después de generarse | La evidencia indica qué páginas cambiaron |
| `meta.productor_no_esperado` | El PDF pasó por un editor (iLovePDF, Word, Photoshop…) | Ver `archivo.metadatos.info` |
| `docx.certificado_en_word` | Un certificado oficial llegó como Word editable | Pedir el PDF del emisor |
| `img.exif_editor` | La foto pasó por un editor de imágenes | Ver `archivo.metadatos.exif` |
| `dup.archivo_identico` | El mismo archivo ya lo entregó otro postulante | La evidencia indica el otro expediente |

El catálogo completo, con el método de cada señal, está en [catalogo-senales.md](catalogo-senales.md).

### 7.5 Expediente

- **identidad:** RUT, nombre y fecha de nacimiento de referencia, tomados de la cédula (o de la carpeta y los demás documentos si no hay cédula).
- **faltantes:** cuáles de los 6 tipos no se entregaron.
- **discrepancias:**
  - RUT distinto entre documentos o frente al del expediente: alta.
  - Nombre con similitud bajo 0,90: alta; entre 0,90 y 0,97: posible error de tipeo, media.
  - Fecha de nacimiento distinta entre documentos.
  - Clases de la licencia distintas de las de la hoja de vida.
- **fuentes:** folio, código y enlace de verificación de cada documento emitido por un tercero, en estado `pendiente_revisor`.
- **duplicados:** archivos o páginas iguales en otros expedientes.
- **observaciones y estado:** faltantes y observaciones de los documentos vigentes; el expediente queda «observado» si hay alguna.

Si se entregan dos versiones del mismo tipo, el contraste usa la que tiene más datos (el anverso antes que el reverso) y,
a igualdad, la última procesada (por ejemplo, una aclaración).

---

## 8. Configuración

### 8.1 Variables de entorno

Se definen antes de arrancar, en la misma terminal:

```powershell
$env:SOCR_DB = "D:\stracon\produccion.db"
.venv\Scripts\socr servir
```

| Variable | Defecto | Uso |
|---|---|---|
| `SOCR_DB` | `datos/stracon-ocr.db` | Base SQLite. Los originales se guardan en la carpeta `archivos` junto a ella |
| `SOCR_DPI` | `200` | Resolución de render para el OCR. Más alto = más lento, rara vez más preciso |
| `SOCR_MOTOR` | `rapidocr` | Motor por defecto: `rapidocr`, `tesseract` o `azure` |
| `SOCR_COMPARAR_OCR` | `1` | En PDF con texto, hace OCR del render para comparar lo declarado con lo visible. Con `0` es más rápido, pero se pierden `pdf.texto_nativo_distinto_ocr`, `pdf.campo_rasterizado` por OCR y la verificación de texto tapado |
| `SOCR_SEGUNDO_MOTOR` | `1` | Usa Tesseract como segundo motor en páginas escaneadas, si está instalado |
| `SOCR_TESSERACT` | — | Ruta a `tesseract.exe` si no está en la ubicación estándar |
| `SOCR_CORS` | `*` | Orígenes web autorizados, separados por coma. En producción, poner solo el de la webapp |
| `SOCR_MAX_MB` | `25` | Tamaño máximo por archivo en la API |
| `SOCR_TRABAJADORES` | `2` | Procesos de la API que procesan documentos a la vez. Si llegan más, esperan en cola: no hay errores ni rechazos. `0` = todo en el proceso de la API (solo pruebas) |
| `SOCR_HILOS_OCR` | `-1` | Hilos del OCR en cada proceso. `-1` = la mitad de los núcleos lógicos repartida entre los procesos |
| `SOCR_REGLAS` | `src/stracon_ocr/reglas.json` | Archivo de reglas alternativo |
| `SOCR_OLLAMA_MODELO` / `SOCR_OLLAMA_URL` | apagado / `http://127.0.0.1:11434` | LLM local de respaldo |
| `SOCR_AZURE_ENDPOINT` / `SOCR_AZURE_KEY` | — | Credenciales de Azure Document Intelligence |

### 8.2 Archivo de reglas (`reglas.json`)

Se edita sin tocar código y se aplica al reiniciar `socr servir`. Conviene cambiar `version` en cada ajuste: queda
registrada en cada JSON (`versiones.reglas`) para saber con qué reglas se procesó.

| Sección | Qué controla |
|---|---|
| `umbrales.palabras_min_pdf_nativo` | Con menos palabras de texto, la página se trata como escaneada (5) |
| `umbrales.cer_campo_nativo_ocr` | Diferencia máxima entre el texto del PDF y el OCR de un campo (0,34) |
| `umbrales.nitidez_min_imagen` | Bajo este valor, una imagen insertada se considera borrosa (60) |
| `umbrales.zscore_confianza` · `confianza_min_campo` | Cuándo la confianza OCR de un campo es anómala (3,0 y 0,8) |
| `umbrales.segundos_mod_creacion` | Diferencia tolerada entre las fechas de creación y modificación del PDF (120 s) |
| `umbrales.dias_vigencia_antecedentes` | Antigüedad máxima del certificado de antecedentes (60 días) |
| `umbrales.nombre_discrepancia` · `nombre_posible_tipeo` | Similitud de nombres (0,90 y 0,97) |
| `umbrales.phash_distancia_pagina` | Distancia máxima para considerar dos páginas casi idénticas (2) |
| `umbrales.ela_ratio_zona` | Cuánto más error de compresión puede tener un campo que el resto de la foto (2,5) |
| `productores_editores` | Programas que delatan edición si aparecen en Producer o Creator del PDF |
| `software_imagen_editores` | Ídem, en el EXIF de las fotos |
| `tipos_emitidos_como_pdf_nativo` | Tipos que el emisor entrega como PDF con texto (un escaneo de ellos es señal) |
| `tipos_oficiales` | Tipos que no deberían llegar en Word, y lista de documentos esperados en el expediente |
| `formatos` | Expresión regular del formato válido de un campo por tipo (`tipo.campo`) |
| `verificacion_fuentes` | Emisor y enlace de verificación por tipo |

> **Importante:** los `formatos` actuales son los de los documentos **ficticios** del mockup. Antes de usar documentos
> reales hay que reemplazarlos por los formatos reales de cada emisor, o quitarlos.

Ejemplo, agregar un editor sospechoso y cambiar el formato del folio de antecedentes:

```json
"productores_editores": ["ilovepdf", "smallpdf", "…", "mi-editor"],
"formatos": { "certificado_antecedentes.folio": "^\\d{9}$" }
```

---

## 9. Datos guardados

| Qué | Dónde |
|---|---|
| JSON de cada documento, contrastes, trabajos y texto para búsqueda | `datos/stracon-ocr.db` (SQLite) |
| Archivos originales (por hash SHA-256) | `datos/archivos/<2 primeros caracteres>/<hash>` |
| PDF de prueba del mockup | `datos/dorado/<RUT>/` |
| Verdad conocida y último resultado de la evaluación | `datos/verdad-mockup.json` · `datos/evaluacion.json` |

- **Reprocesar** el mismo archivo en el mismo expediente reemplaza el resultado anterior. En otro expediente, se guarda aparte y dispara `dup.archivo_identico`.
- **Respaldo:** copiar la carpeta `datos` con el servidor detenido.
- **Empezar de cero:** con el servidor detenido, borrar `datos/stracon-ocr.db*` y `datos/archivos`.
- La base contiene **datos personales** de los postulantes: tratarla como información sensible.

---

## 10. Evaluación y calibración

```powershell
.venv\Scripts\socr evaluar
```

Procesa todos los archivos de `datos/dorado` y los compara con `datos/verdad-mockup.json`, que indica qué señal debe
disparar cada documento alterado. Muestra una línea por archivo (`OK` o `!!`) y un resumen:

```json
{ "documentos": 135, "con_alteracion": 11, "recall_alteraciones": 1.0,
  "documentos_limpios": 124, "limpios_con_falso_positivo": 0, "clasificados": 135,
  "rut_extraido_igual_carpeta": 135, "campos_promedio": 6.0, "latencia_p50_s": 2.61, "latencia_p95_s": 3.53 }
```

- **recall_alteraciones:** fracción de alteraciones detectadas (meta MVP: ≥ 0,9).
- **limpios_con_falso_positivo:** documentos sin alteración que igual tuvieron una señal media o alta (meta: ≈ 0).
- `--hoy 2026-10-21` fija la fecha de referencia de las vigencias, para que el resultado no cambie con el tiempo.

**Calibrar con documentos reales:**

1. Crear una carpeta con subcarpetas por RUT, con documentos auténticos anonimizados y, si se puede, copias alteradas conocidas.
2. Crear un archivo de verdad con el formato de `verdad-mockup.json`: `{"documentos": {"<RUT>/<archivo>": ["<señal esperada>"]}}`. Los documentos limpios no se listan.
3. Ejecutar `.venv\Scripts\socr evaluar --carpeta <carpeta> --verdad <archivo> --salida resultado.json`.
4. Ajustar los umbrales de `reglas.json` y repetir hasta llegar a la meta. El detalle por archivo queda en `resultado.json`.

Para regenerar la verdad del mockup: `.venv\Scripts\python herramientas\generar_verdad_mockup.py`.

---

## 11. Cómo extenderlo

### 11.1 Agregar un rótulo a un campo existente

Si un emisor escribe «Nro. Folio» en vez de «Folio», agregar el texto a `etiquetas` del campo en `src/stracon_ocr/tipos/<tipo>.py`:

```python
CampoSpec("folio", ("Folio", "N° de folio", "Nro. Folio"), "codigo", clave=True),
```

Los rótulos se comparan sin tildes, en mayúsculas y con «N°», «Nº», «Nro.» o «Número» equivalentes a «N».

### 11.2 Agregar un tipo de documento

1. Crear `src/stracon_ocr/tipos/<nuevo>.py`:

   ```python
   from .base import NOMBRE, RUT, CampoSpec, TipoSpec

   TIPO = TipoSpec(
       clave="certificado_afp",
       nombre="Certificado de afiliación AFP",
       titulos=("Certificado de afiliación",),
       palabras_clave=("AFP", "Superintendencia de Pensiones", "afiliado"),
       campos=(NOMBRE, RUT, CampoSpec("fecha_afiliacion", ("Fecha de afiliación",), "fecha", clave=True)),
   )
   ```

2. Registrarlo en `src/stracon_ocr/tipos/__init__.py` (importar el módulo y sumarlo a la tupla).
3. Agregar la clave a `TipoDocumento` en `src/stracon_ocr/contrato.py`.
4. Si debe estar en todos los expedientes, agregarla a `tipos_oficiales` de `reglas.json`.
5. Agregar una prueba con un documento de ejemplo y correr `pytest`.

Clases de campo disponibles: `texto`, `nombre`, `rut`, `fecha`, `anio`, `codigo`, `clases`, `numero`.

### 11.3 Agregar una señal

Las señales viven en `src/stracon_ocr/forense/` y devuelven objetos `Senal` con `id`, `familia`, `severidad`, `detalle`
(texto para el revisor) y `evidencia`. Después de agregar una:

1. Documentarla en [catalogo-senales.md](catalogo-senales.md).
2. Agregar una prueba que genere el documento alterado y verifique que la señal aparece.
3. Correr `socr evaluar` para confirmar que no aparecen falsos positivos en los documentos limpios.

### 11.4 Otra base de datos

`src/stracon_ocr/almacen/repositorio.py` define la interfaz. Para Azure DocumentDB o PostgreSQL se escribe otra
implementación con los mismos métodos y se pasa a `Procesador(repo=...)`. El contrato JSON no cambia.

---

## 12. Solución de problemas

| Síntoma | Causa | Solución |
|---|---|---|
| `uv : El término 'uv' no se reconoce` | uv no está en el PATH | Usar `python -m uv …` |
| `failed to remove file …socr.exe … está siendo utilizado` al hacer `uv sync` | Hay un `socr` corriendo | Detener `socr servir` u otros comandos y repetir |
| La página no carga en `http://127.0.0.1:8000` | El servidor no está corriendo o usa otro puerto | Mirar la terminal de `socr servir`; usar `--puerto` |
| `error while attempting to bind on address` | El puerto está ocupado | `socr servir --puerto 8001` |
| Tarda 8 s o más por página | OpenVINO no está instalado y se usa ONNX Runtime | `python -m uv sync --extra tesseract --extra firmas` y verificar que `versiones.motores.rapidocr` diga `(openvino)` |
| `/salud` muestra `tesseract: false` | No está instalado o está en otra ruta | Instalarlo o definir `SOCR_TESSERACT` |
| 415 con un `.doc` | Falta LibreOffice | Instalarlo, o convertir el archivo a `.docx` o PDF |
| Un campo no aparece | El rótulo del documento no está en la lista del tipo | Agregarlo ([11.1](#111-agregar-un-rótulo-a-un-campo-existente)) |
| Tipo `desconocido` o equivocado | El título o las frases no coinciden con las reglas | Indicar el tipo a mano y agregar el título a `titulos` del tipo |
| `formato.folio` falla en documentos reales | El emisor usa un formato distinto del Registro Civil | Ajustar `formatos` en `reglas.json` |
| Muchas señales `img.ela_zona` en fotos de WhatsApp | La recompresión confunde a ELA | Es severidad baja: ignorarla o subir `ela_ratio_zona` |
| Errores en `errores[]` del JSON | Falló una etapa; el resto sigue siendo válido | Leer el mensaje; si se repite, reportar con el archivo |
| Un comando de PowerShell falla con «modo de lenguaje» | PowerShell del equipo está en modo restringido | Usar los comandos de este manual tal cual, o `cmd` |

---

## 13. Limitaciones conocidas

- **Calibrado solo con documentos ficticios.** Los umbrales y formatos deben ajustarse con documentos reales antes de producción.
- **Cédula:** la lectura de QR y MRZ no se probó con cédulas reales (el mockup no trae reverso ni QR válido).
- **Alteraciones sin rastro en el archivo** (un valor cambiado sin otra tipografía ni capas): solo se detectan al contrastar con el emisor (Agente 2) o con otros documentos.
- **Señales pendientes:** doble compresión JPEG, sellos clonados, foto de pantalla, geometría de caracteres, línea base, posición respecto de la plantilla y foto o firma repetidas.
- **Sin autenticación** en la API: exponerla solo detrás del backend de la webapp.
- **Volumen por instancia:** 2 documentos a la vez (unos 32 PDF por minuto en un i7-1270P); los demás esperan en cola
  sin errores. Más volumen = más instancias o Azure (ver `docs/api-webapp.md`, Producción).
- **Cobertura:** 6 de los 16 documentos del expediente.
- **Sin probar contra el servicio real:** el adaptador de Azure y el respaldo con Ollama.

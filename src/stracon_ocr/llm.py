"""Respaldo opcional con un LLM local (Ollama) para campos que las reglas no encontraron.

Apagado por defecto (SOCR_OLLAMA_MODELO vacío). Nunca reemplaza un campo ya extraído y nunca se usa
como fuente forense: el campo queda marcado con metodo="llm" y confianza 0,5.
"""
from __future__ import annotations

import json
import urllib.request

from .config import Config
from .contrato import Campo
from .extraccion import normalizar_valor
from .tipos import CampoSpec


def completar(texto: str, faltantes: list[CampoSpec], tipo: str, cfg: Config) -> dict[str, Campo]:
    if not (cfg.ollama_modelo and faltantes and texto.strip()):
        return {}
    esquema = {
        "type": "object",
        "properties": {c.nombre: {"type": ["string", "null"]} for c in faltantes},
        "required": [c.nombre for c in faltantes],
    }
    prompt = (
        f"Extrae del siguiente documento chileno ({tipo}) los campos {', '.join(c.nombre for c in faltantes)}. "
        "Copia el valor tal como aparece en el texto. Si un campo no aparece, usa null. No inventes datos.\n\n" + texto[:6000]
    )
    cuerpo = json.dumps({"model": cfg.ollama_modelo, "prompt": prompt, "format": esquema, "stream": False,
                         "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(f"{cfg.ollama_url}/api/generate", data=cuerpo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        respuesta = json.loads(json.loads(r.read())["response"])
    campos = {}
    for c in faltantes:
        v = respuesta.get(c.nombre)
        if isinstance(v, str) and v.strip() and v.strip().lower() in texto.lower():  # solo valores presentes en el texto
            campos[c.nombre] = Campo(valor=v.strip(), valor_normalizado=normalizar_valor(c.clase, v), conf=0.5, metodo="llm",
                                     regla_id=f"{tipo}.{c.nombre}.llm.v1", evidencia=f"LLM local {cfg.ollama_modelo}")
    return campos

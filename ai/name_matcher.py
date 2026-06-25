"""
ai/name_matcher.py — Motor de búsqueda por nombre con:
  - Fuzzy matching (rapidfuzz)
  - Normalización fonética venezolana
  - Base de apodos venezolanos
  - Embeddings semánticos (sentence-transformers)
"""
import unicodedata
from typing import List, Tuple
from rapidfuzz import fuzz, process
from loguru import logger

# ── Tabla de equivalencias fonéticas venezolanas ──────────────────────
EQUIVALENCIAS_FONETICAS = {
    "X":  "S",   # Xiomara → Siomara
    "Y":  "I",   # Yoleida → Ioleida
    "V":  "B",   # Venezuela
    "LL": "Y",   # Llaves → Yaves
    "GE": "JE",  # Génesis → Jenesis
    "GI": "JI",
    "QU": "K",   # Quique → Kike
    "Z":  "S",   # González → Gonsales
    "H":  "",    # Silenciosa al inicio
}

# ── Apodos venezolanos más comunes ────────────────────────────────────
APODOS = {
    "JOSE":       ["PEPE", "CHEPE", "JOSELITO", "JOSE LUIS"],
    "JESUS":      ["CHUCHO", "CHUY", "JESSE", "JESS"],
    "FRANCISCO":  ["PANCHO", "PACO", "FRANK", "CISCO"],
    "MARIA":      ["MARY", "MARU", "MARI", "MARUJA", "MARISOL"],
    "JUAN":       ["JUANCHO", "JUANITO", "JOHNNY"],
    "PEDRO":      ["PEDRITO", "PERICO", "PETE"],
    "CARLOS":     ["CARLITOS", "CHARLY", "CARL"],
    "MIGUEL":     ["MIGUE", "MIGUELITO", "MIKE"],
    "RAFAEL":     ["RAFA", "RAFAELITO"],
    "ANTONIO":    ["TOÑO", "TONY", "TONI"],
    "MANUEL":     ["MANOLO", "MANU", "MANUELITO"],
    "ALEJANDRO":  ["ALEX", "ALEJO", "ALE"],
    "ROBERTO":    ["BETO", "ROBERT", "ROB"],
    "FERNANDO":   ["NANDO", "FER", "FERCHO"],
    "EDUARDO":    ["EDDY", "EDUARDO", "LALO"],
    "MERCEDES":   ["MECHE", "MERCHE", "MERCY"],
    "CONCEPCION": ["CONCHA", "CONCHITA", "CONCHI"],
    "GUADALUPE":  ["LUPE", "LUPITA", "LUPIS"],
    "CARMEN":     ["CARMENCITA", "CARMENCHU"],
    "ROSA":       ["ROSITA", "ROSARIO", "CHARITO"],
    "ANA":        ["ANITA", "ANABEL"],
    "PATRICIA":   ["PATY", "PATTY", "TRICIA"],
    "BEATRIZ":    ["BEA", "BETTY", "BETY"],
    "ADRIANA":    ["ADY", "ADRI"],
    "ANDREA":     ["ANDY", "ANDRECITA"],
    "JESSICA":    ["JESS", "YESSICA", "JESSY"],
    "VALENTINA":  ["VALE", "VALEN"],
    "GERARDO":    ["GERO", "JERRY"],
    "GONZALO":    ["GONZO", "GONZA"],
    "HECTOR":     ["HECTORCITO"],
    "RICARDO":    ["RICKY", "RICHARD", "RICHY"],
    "LUISA":      ["LUISITA", "LUISE"],
    "DANIEL":     ["DANI", "DANNY"],
    "DAVID":      ["DAVY", "DAVE"],
    "ANDRES":     ["ANDY", "ANDRE"],
    "ENRIQUE":    ["QUIQUE", "HENRY", "ENRIQUITO"],
}

# Mapa inverso: apodo → nombre real
APODO_A_NOMBRE: dict[str, str] = {}
for nombre_real, apodos in APODOS.items():
    for apodo in apodos:
        APODO_A_NOMBRE[apodo.upper()] = nombre_real


def normalizar(texto: str) -> str:
    """
    Normaliza un nombre venezolano para comparación:
    elimina acentos, aplica equivalencias fonéticas, convierte a mayúsculas.
    """
    if not texto:
        return ""
    # Quitar acentos
    texto = unicodedata.normalize("NFD", texto.upper())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    # Aplicar equivalencias fonéticas (orden importa: primero los de 2 letras)
    for orig, dest in sorted(EQUIVALENCIAS_FONETICAS.items(), key=lambda x: -len(x[0])):
        texto = texto.replace(orig, dest)
    return texto.strip()


def expandir_candidatos(nombre: str) -> List[str]:
    """
    Dado un nombre, genera todos sus variantes posibles:
    original, normalizado, nombre real si es apodo, y variantes del nombre real.
    """
    nombre_up = nombre.upper().strip()
    nombre_norm = normalizar(nombre)
    candidatos = {nombre_up, nombre_norm}

    # ¿Es un apodo conocido?
    nombre_real = APODO_A_NOMBRE.get(nombre_up)
    if nombre_real:
        candidatos.add(nombre_real)
        candidatos.add(normalizar(nombre_real))
        # Agregar también otros apodos del mismo nombre real
        for ap in APODOS.get(nombre_real, []):
            candidatos.add(ap.upper())
            candidatos.add(normalizar(ap))

    # También probar si el nombre buscado es un nombre real con apodos
    if nombre_up in APODOS:
        for ap in APODOS[nombre_up]:
            candidatos.add(ap.upper())
            candidatos.add(normalizar(ap))

    return list(candidatos)


def calcular_score_nombre(nombre_buscado: str, nombre_candidato: str) -> float:
    """
    Calcula el score de similitud entre dos nombres.
    Combina fuzzy matching con normalización fonética.
    Retorna un valor entre 0.0 y 1.0.
    """
    candidatos = expandir_candidatos(nombre_buscado)
    candidato_norm = normalizar(nombre_candidato)
    candidato_up = nombre_candidato.upper().strip()

    mejor_score = 0.0
    for c in candidatos:
        # Comparar con el candidato normalizado y el original
        for objetivo in [candidato_norm, candidato_up]:
            s1 = fuzz.token_sort_ratio(c, objetivo) / 100.0
            s2 = fuzz.token_set_ratio(c, objetivo)  / 100.0
            s3 = fuzz.ratio(c, objetivo)             / 100.0
            mejor_score = max(mejor_score, s1, s2, s3)

    return mejor_score


def buscar_por_nombre(
    nombre_buscado: str,
    personas: List[dict],
    umbral: float = 0.72,
    top_k: int = 10,
) -> List[Tuple[dict, float]]:
    """
    Busca en una lista de personas por nombre usando fuzzy + fonética venezolana.

    Args:
        nombre_buscado: Nombre a buscar
        personas: Lista de dicts con al menos 'nombre' y opcionalmente 'apellidos'
        umbral: Score mínimo (0.0–1.0) para incluir en resultados
        top_k: Máximo de resultados

    Returns:
        Lista de (persona_dict, score) ordenada de mayor a menor score
    """
    resultados = []
    for p in personas:
        nombre_completo = f"{p.get('nombre', '')} {p.get('apellidos', '')}".strip()
        score = calcular_score_nombre(nombre_buscado, nombre_completo)

        # Bonus si la cédula coincide exactamente
        cedula_buscada = nombre_buscado.replace("-", "").replace(".", "")
        if cedula_buscada.isdigit() and cedula_buscada == str(p.get("cedula", "")):
            score = max(score, 0.98)

        if score >= umbral:
            resultados.append((p, score))

    resultados.sort(key=lambda x: x[1], reverse=True)
    return resultados[:top_k]

import logging
import pandas as pd
from pathlib import Path
import fitz  # PyMuPDF
import docx

logger = logging.getLogger(__name__)

def extraer_texto_de_documento(ruta_archivo: str) -> str:
    """Extrae el texto de un archivo PDF, DOCX, XLSX, XLS o CSV."""
    ruta = Path(ruta_archivo)
    texto = ""
    ext = ruta.suffix.lower()

    try:
        if ext == ".pdf":
            doc = fitz.open(ruta)
            for page in doc:
                texto += page.get_text()
            doc.close()
        elif ext == ".docx":
            doc = docx.Document(ruta)
            texto = "\n".join([para.text for para in doc.paragraphs])
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(ruta, engine="openpyxl" if ext == ".xlsx" else None)
            texto = df.to_csv(index=False)
        elif ext == ".csv":
            df = pd.read_csv(ruta)
            texto = df.to_csv(index=False)
        elif ext == ".txt":
            with open(ruta, "r", encoding="utf-8") as f:
                texto = f.read()
        else:
            logger.warning(f"Formato no soportado para extracción de texto: {ext}")
            return ""
    except Exception as e:
        logger.error(f"Error extrayendo texto de {ruta_archivo}: {e}")
        return ""
        
    return texto.strip()

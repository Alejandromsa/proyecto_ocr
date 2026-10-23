"""Candados compartidos. pdfium (pypdfium2) no es seguro entre hilos: todo render o apertura de PDF con pdfium
dentro de un mismo proceso pasa por PDFIUM."""
import threading

PDFIUM = threading.RLock()

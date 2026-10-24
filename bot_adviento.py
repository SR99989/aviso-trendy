"""
Bot que vigila la tienda Trendy (maquillajetrendyshop.com, plataforma VTEX)
y te avisa por Telegram cuando aparece el calendario de adviento.

Uso:
    pip install requests beautifulsoup4
    export TELEGRAM_TOKEN="..."   TELEGRAM_CHAT_ID="..."
    python bot_adviento.py
"""
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
import requests
from bs4 import BeautifulSoup

# ---------- CONFIGURACIÓN ----------
BASE = "https://www.maquillajetrendyshop.com"

# Palabras que debe tener el nombre del producto para considerarlo el calendario
PALABRAS = ["adviento"]

# Ventana en la que se espera el lanzamiento: revisa más seguido
VENTANA_INICIO = date(2026, 11, 25)
VENTANA_FIN = date(2026, 12, 2)
INTERVALO_VENTANA = 120    # cada 2 minutos durante la ventana
INTERVALO_NORMAL = 3600    # cada hora el resto del tiempo

# Telegram: crea el bot con @BotFather y obtén tu chat_id con @userinfobot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")  # <-- pega tu token entre las ULTIMAS comillas: ("TELEGRAM_TOKEN", "AQUI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # <-- pega tu número Id entre las ULTIMAS comillas
# -----------------------------------

# Google Calendar (cuenta de servicio): ver instrucciones del paso a paso
CALENDAR_ID = os.getenv("CALENDAR_ID", "")
CREDENCIALES = os.getenv("CREDENCIALES", "credenciales.json")
ZONA_HORARIA = "America/Bogota"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AvisoAdviento/1.0)"}


CARRITOS: dict[str, str] = {}


def texto_aviso(nombre: str, enlace: str) -> str:
    texto = f"¡Disponible en Trendy! {nombre}\n\nVer producto: {enlace}"
    if enlace in CARRITOS:
        texto += f"\n\nMETER AL CARRITO (toca aquí): {CARRITOS[enlace]}"
    return texto


def notificar(mensaje: str) -> None:
    print(mensaje)
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("Telegram: faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID (los secretos no llegaron)")
        return
    # TELEGRAM_CHAT_ID puede tener varios Id separados por coma: "111,222,333"
    for chat_id in re.split(r"[,\s;]+", TELEGRAM_CHAT_ID):
        if not chat_id:
            continue
        etiqueta = "..." + chat_id[-4:]  # no se imprime el Id completo
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": chat_id, "text": mensaje},
                timeout=15,
            )
            if r.ok:
                print(f"Telegram: enviado a {etiqueta}")
            else:
                print(f"Telegram NO aceptó el mensaje para {etiqueta}:", r.status_code, r.text)
        except requests.RequestException as e:
            print(f"Error enviando a Telegram ({etiqueta}):", e)


def avisar_calendario(titulo: str, detalle: str) -> None:
    """Crea un evento en tu Google Calendar que empieza en 2 minutos."""
    if not CALENDAR_ID:
        return
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            CREDENCIALES, scopes=["https://www.googleapis.com/auth/calendar.events"]
        )
        servicio = build("calendar", "v3", credentials=creds)
        inicio = datetime.now() + timedelta(minutes=2)
        fin = inicio + timedelta(minutes=30)
        evento = {
            "summary": titulo,
            "description": detalle,
            "start": {"dateTime": inicio.isoformat(timespec="seconds"), "timeZone": ZONA_HORARIA},
            "end": {"dateTime": fin.isoformat(timespec="seconds"), "timeZone": ZONA_HORARIA},
        }
        servicio.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        print("Evento creado en el calendario")
    except Exception as e:  # noqa: BLE001
        print("No se pudo crear el evento en el calendario:", e)


def coincide(texto: str) -> bool:
    texto = texto.lower()
    return any(p in texto for p in PALABRAS)


def buscar_por_api() -> dict[str, str]:
    """API pública de catálogo de VTEX. Devuelve {enlace: nombre} de productos con stock."""
    encontrados = {}
    for termino in PALABRAS:
        r = requests.get(
            f"{BASE}/api/catalog_system/pub/products/search",
            params={"ft": termino},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        for prod in r.json():
            nombre = prod.get("productName", "")
            if not coincide(nombre):
                continue
            enlace = prod.get("link", BASE)
            for item in prod.get("items", []):
                vendedor = next(
                    (v for v in item.get("sellers", [])
                     if v.get("commertialOffer", {}).get("AvailableQuantity", 0) > 0),
                    None,
                )
                if vendedor:
                    encontrados[enlace] = nombre
                    # Enlace que, al abrirlo, mete el producto al carrito
                    CARRITOS[enlace] = (
                        f"{BASE}/checkout/cart/add?sku={item.get('itemId')}"
                        f"&qty=1&seller={vendedor.get('sellerId', '1')}&sc=1"
                    )
                    break
    return encontrados


def buscar_por_html() -> dict[str, str]:
    """Plan B: enlaces de la portada y de la búsqueda cuyo texto/URL coincide."""
    encontrados = {}
    for url in (BASE + "/", f"{BASE}/adviento?_q=adviento&map=ft"):
        r = requests.get(url, headers=HEADERS, timeout=20)
        if not r.ok:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            texto = a.get_text(" ", strip=True)
            # ignora el enlace fijo de "Términos Calendario 2026" del pie de página
            if "servicio-al-cliente" in a["href"]:
                continue
            if coincide(texto) or coincide(a["href"]):
                encontrados[requests.compat.urljoin(url, a["href"])] = texto or a["href"]
    return encontrados


def revisar() -> dict[str, str]:
    try:
        return buscar_por_api()
    except (requests.RequestException, ValueError) as e:
        print("Falló la API de VTEX, uso el plan B:", e)
        return buscar_por_html()


def intervalo_actual() -> int:
    hoy = date.today()
    if VENTANA_INICIO <= hoy <= VENTANA_FIN:
        return INTERVALO_VENTANA
    return INTERVALO_NORMAL


ARCHIVO_ESTADO = "avisados.txt"


def una_vez() -> None:
    """Una sola revisión (para GitHub Actions). Recuerda lo ya avisado en un archivo."""
    previos = set()
    if os.path.exists(ARCHIVO_ESTADO):
        with open(ARCHIVO_ESTADO, encoding="utf-8") as f:
            previos = set(f.read().split())
    nuevos = []
    try:
        for enlace, nombre in revisar().items():
            if enlace not in previos:
                notificar(texto_aviso(nombre, enlace))
                avisar_calendario("¡Calendario de adviento Trendy disponible!", f"{nombre}\n{enlace}")
                nuevos.append(enlace)
    except requests.RequestException as e:
        print("Error de red:", e)
    if nuevos:
        with open(ARCHIVO_ESTADO, "a", encoding="utf-8") as f:
            f.write("\n".join(nuevos) + "\n")
    print("Revisión terminada.")


def main() -> None:
    if "--prueba" in sys.argv:
        notificar("PRUEBA OK: el aviso de Trendy está funcionando.")
        avisar_calendario("PRUEBA: calendario de adviento Trendy", "Si ves esto, el calendario funciona.")
        return
    if "--una-vez" in sys.argv:
        una_vez()
        return
    ya_avisados: set[str] = set()
    notificar("Bot iniciado, vigilando " + BASE)
    while True:
        try:
            for enlace, nombre in revisar().items():
                if enlace not in ya_avisados:
                    notificar(texto_aviso(nombre, enlace))
                    avisar_calendario("¡Calendario de adviento Trendy disponible!", f"{nombre}\n{enlace}")
                    ya_avisados.add(enlace)
        except requests.RequestException as e:
            print("Error de red:", e)
        time.sleep(intervalo_actual())


if __name__ == "__main__":
    main()

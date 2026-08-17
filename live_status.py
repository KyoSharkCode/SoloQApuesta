import os
import sys
import json
import time
import requests
from urllib.parse import quote

# ================= CONFIGURACIÓN =================
API_KEY    = os.getenv("RIOT_API_KEY", "").strip()
REGION_ACC = "americas"
REGION_LOL = "la1"
# =================================================

HEADERS = {"X-Riot-Token": API_KEY}

# Importa la lista de jugadores desde actualizar_datos.py
# Si no se puede importar, usa la lista completa de respaldo
try:
    from actualizar_datos import JUGADORES as JUGADORES_RAW
    LISTA_JUGADORES = [f"{j['name']}#{j['tag']}" for j in JUGADORES_RAW]
    print(f"✅ {len(LISTA_JUGADORES)} jugadores importados desde actualizar_datos.py")
except ImportError:
    print("⚠️ No se pudo importar actualizar_datos.py. Usando lista de respaldo local.")
    LISTA_JUGADORES = [
        "Pinea#Pinea",
        "Galactic Shark#AYK",
        "El Buñuelito#KyA",
        "ゆうき まこと#1411",
        "adrianNOOBYT#LAN",
        "Ostia#LAN",
    ]

DDRAGON_VERSION = "14.20.1"


def cargar_diccionarios_ddragon():
    """Descarga los diccionarios de campeones y hechizos de invocador."""
    diccionario_campeones = {}
    diccionario_hechizos  = {}

    try:
        url_champ = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
        champ_data = requests.get(url_champ, timeout=15).json()["data"]
        diccionario_campeones = {int(info["key"]): nombre for nombre, info in champ_data.items()}
        print(f"✅ Diccionario de campeones cargado ({len(diccionario_campeones)} entradas)")
    except Exception as e:
        print(f"⚠️ No se pudo descargar el diccionario de campeones: {e}")

    try:
        url_summ = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/data/es_ES/summoner.json"
        summ_data = requests.get(url_summ, timeout=15).json()["data"]
        diccionario_hechizos = {
            int(info["key"]): {"nombre": info["name"], "icono": info["image"]["full"]}
            for info in summ_data.values()
        }
        print(f"✅ Diccionario de hechizos cargado ({len(diccionario_hechizos)} entradas)")
    except Exception as e:
        print(f"⚠️ No se pudo descargar el diccionario de hechizos: {e}")

    return diccionario_campeones, diccionario_hechizos


def get_con_reintento(url, headers, timeout=10, max_reintentos=2):
    """GET con reintento ante rate limit (429) o error de servidor (5xx)."""
    resp = None
    for intento in range(max_reintentos + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException:
            if intento == max_reintentos:
                raise
            time.sleep(2)
            continue

        if resp.status_code == 429 and intento < max_reintentos:
            espera = int(resp.headers.get("Retry-After", 2))
            print(f"    ⏳ Rate limit, esperando {espera}s...")
            time.sleep(espera)
            continue

        if resp.status_code >= 500 and intento < max_reintentos:
            time.sleep(2)
            continue

        return resp
    return resp


def estados_son_iguales(estado_anterior, estado_nuevo):
    """
    Compara dos dicts de estado de forma robusta usando json.dumps ordenado.
    Evita falsos positivos por diferencias en el orden de las keys.
    """
    try:
        anterior_str = json.dumps(estado_anterior, sort_keys=True, ensure_ascii=False)
        nuevo_str    = json.dumps(estado_nuevo,    sort_keys=True, ensure_ascii=False)
        return anterior_str == nuevo_str
    except Exception:
        return False


def actualizar_estado_en_vivo(jugadores):
    if not API_KEY:
        print("🚨 No se encontró RIOT_API_KEY en las variables de entorno.")
        sys.exit(1)

    # Cargar estado anterior para comparación inteligente
    data_cargada = {}
    if os.path.exists("live_data.json"):
        try:
            with open("live_data.json", "r", encoding="utf-8") as f:
                data_cargada = json.load(f)
            print(f"📂 live_data.json anterior cargado correctamente")
        except Exception as e:
            print(f"⚠️ No se pudo leer live_data.json anterior: {e}")

    datos_json  = {}
    errores_auth = 0
    diccionario_campeones, diccionario_hechizos = cargar_diccionarios_ddragon()

    for jugador in jugadores:
        print(f"\nRevisando: {jugador}")
        try:
            nombre, tag = jugador.split("#", 1)
        except ValueError:
            print(f"  ❌ Formato incorrecto: {jugador}")
            datos_json[jugador] = {"en_partida": False}
            continue

        # PASO 1: PUUID
        url_acc = (
            f"https://{REGION_ACC}.api.riotgames.com"
            f"/riot/account/v1/accounts/by-riot-id/{quote(nombre)}/{quote(tag)}"
        )
        try:
            res_acc = get_con_reintento(url_acc, HEADERS)
        except requests.exceptions.RequestException as e:
            print(f"  ❌ Error de red: {e}")
            datos_json[jugador] = {"en_partida": False}
            continue

        if res_acc.status_code in (401, 403):
            errores_auth += 1

        if res_acc.status_code != 200:
            print(f"  ❌ Account v1 → {res_acc.status_code}")
            datos_json[jugador] = {"en_partida": False}
            continue

        puuid = res_acc.json().get("puuid", "")
        print(f"  ✓ PUUID obtenido")

        # PASO 2: Spectator v5
        url_spec = (
            f"https://{REGION_LOL}.api.riotgames.com"
            f"/lol/spectator/v5/active-games/by-summoner/{puuid}"
        )
        try:
            res_spec = get_con_reintento(url_spec, HEADERS)
        except requests.exceptions.RequestException as e:
            print(f"  ❌ Error de red: {e}")
            datos_json[jugador] = {"en_partida": False}
            continue

        if res_spec.status_code in (401, 403):
            errores_auth += 1

        if res_spec.status_code == 200:
            print(f"  🟢 ¡Está en partida!")
            info_partida = {"en_partida": True}
            try:
                game_data    = res_spec.json()
                participante = next(
                    (p for p in game_data.get("participants", []) if p.get("puuid") == puuid),
                    None
                )
                if participante:
                    champ_id = participante.get("championId")
                    equipo   = "blue" if participante.get("teamId") == 100 else "red"
                    spell1   = diccionario_hechizos.get(participante.get("spell1Id"), {"nombre": "?", "icono": ""})
                    spell2   = diccionario_hechizos.get(participante.get("spell2Id"), {"nombre": "?", "icono": ""})
                    info_partida.update({
                        "campeon":  diccionario_campeones.get(champ_id, "Desconocido"),
                        "equipo":   equipo,
                        "hechizos": [spell1, spell2],
                    })
            except Exception as e:
                print(f"  ⚠️ No se pudo leer el detalle de la partida: {e}")
            datos_json[jugador] = info_partida

        elif res_spec.status_code == 404:
            print(f"  💤 Fuera de partida.")
            datos_json[jugador] = {"en_partida": False}

        elif res_spec.status_code in (401, 403):
            print(f"  🚨 API key inválida o expirada ({res_spec.status_code})")
            datos_json[jugador] = {"en_partida": False}

        else:
            print(f"  ⚠️ Spectator v5 → {res_spec.status_code}")
            datos_json[jugador] = {"en_partida": False}

        time.sleep(1)

    # ── Failsafe: si la key falló para todos, no sobreescribir ──
    if jugadores and errores_auth >= len(jugadores):
        print("\n🚨 API key inválida o expirada para todos los jugadores.")
        print("🚫 No se sobreescribe live_data.json para conservar el último estado válido.")
        sys.exit(1)  # exit(1) = fallo real, GitHub Actions lo marca en rojo

    # ── Comparación inteligente — solo guarda si algo cambió ──
    if estados_son_iguales(data_cargada, datos_json):
        print("\n💤 Sin cambios en el estado de las partidas.")
        print("🛑 live_data.json no se sobrescribe. Ahorrando despliegue.")
        sys.exit(0)  # exit(0) = éxito, nadie jugó pero no es un error

    # Si llegamos aquí, alguien entró o salió de partida → guardamos
    with open("live_data.json", "w", encoding="utf-8") as f:
        json.dump(datos_json, f, ensure_ascii=False, indent=4)
    print("\n✅ live_data.json actualizado — se detectaron cambios de estado.")


if __name__ == "__main__":
    actualizar_estado_en_vivo(LISTA_JUGADORES)

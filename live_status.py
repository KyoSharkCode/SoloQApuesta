import os
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

try:
    from actualizar_datos import JUGADORES as JUGADORES_RAW
    LISTA_JUGADORES = [f"{j['name']}#{j['tag']}" for j in JUGADORES_RAW]
    print(f"✅ Se han importado {len(LISTA_JUGADORES)} jugadores correctamente desde actualizar_datos.py")
except ImportError:
    print("⚠️ No se pudo importar actualizar_datos.py. Usando lista de respaldo local.")
    LISTA_JUGADORES = [
        "Pinea#Pinea",
        "Galactic Shark#AYK"
    ]


DDRAGON_VERSION = "14.20.1"


def cargar_diccionarios_ddragon():
    """Descarga los diccionarios de campeones y hechizos de invocador (id numérico -> nombre/icono)."""
    diccionario_campeones = {}
    diccionario_hechizos = {}
    try:
        url_champ = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/data/es_ES/champion.json"
        champ_data = requests.get(url_champ, timeout=15).json()["data"]
        diccionario_campeones = {int(info["key"]): nombre for nombre, info in champ_data.items()}
    except Exception as e:
        print(f"⚠️ No se pudo descargar el diccionario de campeones: {e}")

    try:
        url_summ = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/data/es_ES/summoner.json"
        summ_data = requests.get(url_summ, timeout=15).json()["data"]
        diccionario_hechizos = {
            int(info["key"]): {"nombre": info["name"], "icono": info["image"]["full"]}
            for info in summ_data.values()
        }
    except Exception as e:
        print(f"⚠️ No se pudo descargar el diccionario de hechizos: {e}")

    return diccionario_campeones, diccionario_hechizos


def get_con_reintento(url, headers, timeout=10, max_reintentos=2):
    """GET con reintento simple ante rate limit (429) o error de servidor (5xx)."""
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
            print(f"    ⏳ Rate limit alcanzado, esperando {espera}s...")
            time.sleep(espera)
            continue

        if resp.status_code >= 500 and intento < max_reintentos:
            time.sleep(2)
            continue

        return resp
    return resp


def actualizar_estado_en_vivo(jugadores):
    if not API_KEY:
        print("🚨 No se encontró RIOT_API_KEY en las variables de entorno.")
        raise SystemExit(1)

    datos_json = {}
    errores_auth = 0  # NUEVO: cuenta cuántos jugadores fallaron por 401/403 (key inválida/expirada)
    diccionario_campeones, diccionario_hechizos = cargar_diccionarios_ddragon()

    for jugador in jugadores:
        print(f"Revisando estado de: {jugador}")
        try:
            nombre, tag = jugador.split("#", 1)
        except ValueError:
            print(f"  ❌ Error de formato en: {jugador}")
            datos_json[jugador] = {"en_partida": False}
            continue

        # PASO 1: Obtener PUUID — key va en el header, no en la URL
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

        # PASO 2: Spectator v5 — key va en el header, no en la URL
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
                game_data = res_spec.json()
                participante = next(
                    (p for p in game_data.get("participants", []) if p.get("puuid") == puuid),
                    None
                )
                if participante:
                    champ_id = participante.get("championId")
                    equipo = "blue" if participante.get("teamId") == 100 else "red"
                    spell1 = diccionario_hechizos.get(participante.get("spell1Id"), {"nombre": "?", "icono": ""})
                    spell2 = diccionario_hechizos.get(participante.get("spell2Id"), {"nombre": "?", "icono": ""})
                    info_partida.update({
                        "campeon": diccionario_campeones.get(champ_id, "Desconocido"),
                        "equipo": equipo,
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

    # NUEVO: si la key falló por auth para TODOS los jugadores, algo está mal con la key,
    # no con el estado real de nadie. No sobreescribimos live_data.json con un falso "todos offline".
    if jugadores and errores_auth >= len(jugadores):
        print("\n🚨 La API key parece inválida o expirada para TODOS los jugadores.")
        print("🚫 No se sobreescribe live_data.json para no borrar el último estado válido conocido.")
        raise SystemExit(1)

    with open('live_data.json', 'w', encoding='utf-8') as f:
        json.dump(datos_json, f, ensure_ascii=False, indent=4)

    print("\n✅ ¡Archivo 'live_data.json' actualizado con éxito!")

if __name__ == "__main__":
    actualizar_estado_en_vivo(LISTA_JUGADORES)

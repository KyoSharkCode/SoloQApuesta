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
    ]

DDRAGON_VERSION = "16.16.1"


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

                # Mapeo de queue IDs a nombres legibles — Alex pidió que en vez
                # de mostrar "Modo <id>" para lo que no reconocíamos, se
                # agrupe todo en 5 categorías: los 4 modos "normales" de
                # Grieta del Invocador (cada uno con su propio nombre),
                # LoL Classic, ARAM, Arena, y "Modo Destacado" como cajón de
                # sastre para cualquier otro modo rotativo/especial (URF,
                # Nexus Blitz, Clash, Modo Definitivo, personalizadas, etc.)
                # — así nunca más se ve un id crudo sin traducir.
                QUEUE_NAMES = {
                    490:  "Partida Rápida",   # Quickplay
                    420:  "Solo/Duo",         # Ranked Solo/Duo
                    400:  "Reclutamiento",    # Normal Draft Pick
                    440:  "Flex",             # Ranked Flex
                    430:  "LoL Classic",      # Normal Blind Pick (el modo "clásico" original)
                    450:  "ARAM",
                    1700: "Arena",
                    1710: "Arena",
                    1720: "Arena",
                }
                queue_id   = game_data.get("gameQueueConfigId", 0)
                # FIX: antes cualquier queue_id no mapeado (URF, Nexus Blitz,
                # Modo Definitivo, Clash, personalizadas, tutorial, un modo
                # rotativo nuevo que Riot agregue después...) se mostraba
                # como "Modo 2400" — un número crudo que no le dice nada a
                # nadie. Ahora todo lo que no sea uno de los modos de arriba
                # cae en "Modo Destacado".
                modo_juego = QUEUE_NAMES.get(queue_id, "Modo Destacado")

                if participante:
                    champ_id = participante.get("championId")
                    equipo   = "blue" if participante.get("teamId") == 100 else "red"
                    spell1   = diccionario_hechizos.get(participante.get("spell1Id"), {"nombre": "?", "icono": ""})
                    spell2   = diccionario_hechizos.get(participante.get("spell2Id"), {"nombre": "?", "icono": ""})
                    info_partida.update({
                        "campeon":    diccionario_campeones.get(champ_id, "Desconocido"),
                        "equipo":     equipo,
                        "hechizos":   [spell1, spell2],
                        "modo_juego": modo_juego,
                        # FIX: gameId + teamId temporales, para poder cruzar
                        # más abajo si alguien más del grupo está en la misma
                        # partida y equipo (dúo en vivo). Se quitan del
                        # resultado final antes de guardar.
                        "_gameId":    game_data.get("gameId"),
                        "_teamId":    participante.get("teamId"),
                    })
                    print(f"  🎮 Modo: {modo_juego}")
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

    # ── Dúo en vivo ────────────────────────────────────────────────────────
    # Si dos (o más) del grupo están en la misma partida Y el mismo equipo,
    # se marcan como dúo entre sí. Igual que en actualizar_datos.py, Riot no
    # expone quién va de premade en la API pública, así que se infiere por
    # coincidir en partida+equipo — con solo 6 personas en el grupo, es una
    # señal muy confiable.
    grupos_por_partida = {}
    for jugador, info in datos_json.items():
        if info.get("en_partida") and info.get("_gameId") is not None:
            clave = (info["_gameId"], info.get("_teamId"))
            grupos_por_partida.setdefault(clave, []).append(jugador)

    for miembros in grupos_por_partida.values():
        if len(miembros) < 2:
            continue
        for jugador in miembros:
            companeros = [m for m in miembros if m != jugador]
            datos_json[jugador]["duo_con"] = [m.split("#", 1)[0] for m in companeros]
            # FIX: además del nombre, se guarda el campeón y los hechizos de
            # cada compañero de dúo — ya están en datos_json (cada uno se
            # calculó arriba con su propia consulta a Spectator v5), así que
            # esto no cuesta ninguna llamada extra a la API.
            datos_json[jugador]["duo_detalle"] = [
                {
                    "nombre":   m.split("#", 1)[0],
                    "campeon":  datos_json[m].get("campeon", "Desconocido"),
                    "hechizos": datos_json[m].get("hechizos", []),
                }
                for m in companeros
            ]
            nombres_legibles = ', '.join(m.split('#', 1)[0] for m in companeros)
            print(f"  🎮🎮 {jugador} está jugando en dúo con: {nombres_legibles}")

    # Limpiar los campos temporales antes de guardar
    for info in datos_json.values():
        info.pop("_gameId", None)
        info.pop("_teamId", None)

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

import os
import json
import time
import requests
from urllib.parse import quote
from datetime import datetime

REGION_API  = "americas"   
REGION_GAME = "la1"        

JUGADORES = [
    {"name": "Pinea",          "tag": "Pinea"},
    {"name": "Galactic Shark", "tag": "AYK"},
    {"name": "El Buñuelito",   "tag": "KyA"},
    {"name": "ゆうき まこと",     "tag": "1411"},
    {"name": "adrianNOOBYT",     "tag": "LAN"},
    {"name": "Ostia",     "tag": "LAN"},
]

MAX_PUNTOS_HISTORIAL = 300  # tope de puntos de LP guardados por jugador


def get_con_reintento(url, headers, timeout=15, max_reintentos=2):
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


def obtener_datos():
    API_KEY = os.getenv("RIOT_API_KEY", "").strip()
    if not API_KEY:
        raise ValueError("🚨 No se encontró RIOT_API_KEY en los Secrets de GitHub.")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Riot-Token": API_KEY
    }

    # 1. Obtener diccionario de campeones (para las maestrías)
    print("📚 Descargando diccionario de campeones...")
    url_ddragon = "https://ddragon.leagueoflegends.com/cdn/14.20.1/data/es_ES/champion.json"
    champ_data = requests.get(url_ddragon).json()["data"]
    diccionario_campeones = {int(info["key"]): nombre for nombre, info in champ_data.items()}

    # NUEVO: guardamos el jugador COMPLETO anterior (no solo su progreso_lp),
    # así si falla la actualización de alguien podemos conservar su último dato bueno.
    datos_antiguos = {}
    if os.path.exists("datos.json"):
        try:
            with open("datos.json", "r", encoding="utf-8") as f:
                data_cargada = json.load(f)
                lista_antigua = data_cargada if isinstance(data_cargada, list) else data_cargada.get("jugadores", [])
                for p in lista_antigua:
                    datos_antiguos[p["nombre"]] = p
        except Exception as e:
            print(f"⚠️ No se pudo leer datos.json anterior, se continúa sin histórico: {e}")

    lista_final = []
    fecha_actual = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"🔍 Consultando: {nombre_completo}")
        anterior = datos_antiguos.get(nombre_completo)

        try:
            # PUUID
            name_enc = quote(jugador["name"])
            tag_enc  = quote(jugador["tag"])
            url_account = f"https://{REGION_API}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_enc}"
            puuid = get_con_reintento(url_account, headers).json()["puuid"]

            # Icono
            url_summoner = f"https://{REGION_GAME}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            icono_id = get_con_reintento(url_summoner, headers).json().get("profileIconId", 1)

            # Rango y LP
            url_league = f"https://{REGION_GAME}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
            league_data = get_con_reintento(url_league, headers).json()

            rango, division, lp, winrate = "Unranked", "", 0, "0%"
            for mode in league_data:
                if mode.get("queueType") == "RANKED_SOLO_5x5":
                    rango    = mode["tier"].capitalize()
                    division = mode["rank"]
                    lp       = mode["leaguePoints"]
                    total    = mode["wins"] + mode["losses"]
                    winrate  = f"{round(mode['wins'] / total * 100)}%" if total > 0 else "0%"
                    break

            # NUEVO: solo agregamos un punto nuevo al historial si el LP realmente cambió,
            # y limitamos el tamaño total para que datos.json y la gráfica no crezcan sin control.
            # NUEVO: cada punto ahora guarda también rango+división, no solo el LP,
            # para poder ubicarlo correctamente en la escalera de elo en la gráfica.
            historial_lp_jugador = list((anterior or {}).get("progreso_lp", []))
            punto_anterior = historial_lp_jugador[-1] if historial_lp_jugador else None
            mismo_punto = (
                punto_anterior is not None and
                punto_anterior.get("lp") == lp and
                punto_anterior.get("rango") == rango and
                punto_anterior.get("division") == division
            )
            if not mismo_punto:
                historial_lp_jugador.append({
                    "fecha": fecha_actual,
                    "lp": lp,
                    "rango": rango,
                    "division": division
                })
            historial_lp_jugador = historial_lp_jugador[-MAX_PUNTOS_HISTORIAL:]

            # Top 3 Maestrías
            url_mast = f"https://{REGION_GAME}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
            mast_data = get_con_reintento(url_mast, headers).json()
            # NUEVO: ordenamos explícitamente por puntos; no confiamos en el orden de la API
            if isinstance(mast_data, list):
                mast_data = sorted(mast_data, key=lambda m: m.get("championPoints", 0), reverse=True)
            maestrias = []
            for m in mast_data[:3]:
                c_nombre = diccionario_campeones.get(m["championId"], "Desconocido")
                maestrias.append({
                    "campeon": c_nombre,
                    "nivel": m["championLevel"],
                    "puntos": f"{m['championPoints']:,}".replace(",", ".")
                })

            # Últimas 10 partidas
            url_ids = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=10"
            ids = get_con_reintento(url_ids, headers).json()

            historial = []
            roles_count = {"TOP": 0, "JUNGLE": 0, "MIDDLE": 0, "BOTTOM": 0, "UTILITY": 0}
            campeones_count = {}

            for match_id in ids:
                url_match = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/{match_id}"
                md = get_con_reintento(url_match, headers).json()
                pp = next((p for p in md["info"]["participants"] if p["puuid"] == puuid), None)

                if pp:
                    k, d, a = pp["kills"], pp["deaths"], pp["assists"]
                    kda = "Perfect" if d == 0 else f"{round((k+a)/d, 2)}"
                    campeon_jugado = pp["championName"]

                    rol_api = pp.get("teamPosition", "")
                    if rol_api in roles_count:
                        roles_count[rol_api] += 1

                    campeones_count[campeon_jugado] = campeones_count.get(campeon_jugado, 0) + 1

                    historial.append({
                        "campeon":   campeon_jugado,
                        "kda":       f"{k}/{d}/{a} ({kda})",
                        "resultado": "Victoria" if pp["win"] else "Derrota",
                        "duracion":  f"{md['info']['gameDuration'] // 60}min",
                    })

            mapa_roles = {"TOP": "Top", "JUNGLE": "Jungla", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support", "N/A": "Unranked"}

            # Calcular Top 2 Roles
            roles_ordenados = sorted(roles_count.items(), key=lambda x: x[1], reverse=True)
            top_2_roles = [{"rol": mapa_roles.get(r[0]), "cantidad": r[1]} for r in roles_ordenados if r[1] > 0][:2]
            rol_mas_jugado = top_2_roles[0]["rol"] if top_2_roles else "Desconocido"

            # Calcular Top 3 Campeones (Recientes)
            campeones_ordenados = sorted(campeones_count.items(), key=lambda x: x[1], reverse=True)[:3]
            top_3_recientes = [{"campeon": c[0], "cantidad": c[1]} for c in campeones_ordenados]

            lista_final.append({
                "nombre":           nombre_completo,
                "icono":            icono_id,
                "rango":            f"{rango} {division}".strip(),
                "lp":               lp,
                "winrate":          winrate,
                "rol_principal":    rol_mas_jugado,
                "top_roles":        top_2_roles,
                "top_recientes":    top_3_recientes,
                "maestrias":        maestrias,
                "progreso_lp":      historial_lp_jugador,
                "historial":        historial,
            })
            print(f"  ✓ {nombre_completo} actualizado correctamente.")

        except Exception as e:
            print(f"🚨 Error con {nombre_completo}: {e}")
            # NUEVO: si falló, conservamos su último dato bueno en vez de borrarlo del ranking
            if anterior:
                print(f"  ↩️ Conservando los últimos datos guardados de {nombre_completo}.")
                lista_final.append(anterior)
            else:
                print(f"  ⚠️ No hay datos previos de {nombre_completo}; se omite en esta actualización.")

    datos_exportar = {
        "ultimaActualizacion": datetime.now().isoformat(),
        "jugadores": lista_final
    }

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_exportar, f, indent=2, ensure_ascii=False)
    print("\n✅ datos.json actualizado correctamente con maestrías y perfiles.")

if __name__ == "__main__":
    obtener_datos()

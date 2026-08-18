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

# NUEVO: mismas constantes de la "escalera de elo" que usa el frontend (index.html),
# replicadas aquí para poder estimar cuánto LP dio/quitó cada partida.
TIER_ORDER = ['IRON','BRONZE','SILVER','GOLD','PLATINUM','EMERALD','DIAMOND','MASTER','GRANDMASTER','CHALLENGER']
DIVISIONLESS = ['MASTER','GRANDMASTER','CHALLENGER']
DIV_NUM = {'IV': 0, 'III': 1, 'II': 2, 'I': 3}
MASTER_PLUS_BASE = 7 * 4 * 100


def elo_score_simple(rango, division, lp):
    tier = (rango or "").upper()
    if tier not in TIER_ORDER:
        return None
    ti = TIER_ORDER.index(tier)
    if tier in DIVISIONLESS:
        return MASTER_PLUS_BASE + max(0, lp or 0)
    dn = DIV_NUM.get((division or "").upper())
    if dn is None:
        return None
    return (ti * 4 + dn) * 100 + max(0, min(100, lp or 0))


def calcular_lp_por_partida(md, progreso_lp_ordenado):
    """Estima el LP ganado/perdido en una partida comparando el snapshot de LP tomado
    justo DESPUÉS de que terminó contra el snapshot inmediatamente anterior.
    Es una aproximación (Riot no expone el LP post-partida en la API de partidas):
    si el bot no llegó a tomar una lectura entre dos partidas jugadas muy seguido,
    el delta de ambas puede quedar atribuido de forma imprecisa a una sola."""
    info = md.get("info", {})
    fin_ms = info.get("gameEndTimestamp")
    if fin_ms is None:
        fin_ms = info.get("gameCreation", 0) + info.get("gameDuration", 0) * 1000
    fin_seg = fin_ms / 1000

    punto_despues = None
    idx_despues = None
    for i, punto in enumerate(progreso_lp_ordenado):
        try:
            ts_punto = datetime.fromisoformat(punto["fecha"]).timestamp()
        except (KeyError, ValueError):
            continue
        if ts_punto >= fin_seg:
            punto_despues, idx_despues = punto, i
            break

    if punto_despues is None or idx_despues == 0:
        return None  # sin lectura posterior registrada, o es el primer punto (sin "antes")

    punto_antes = progreso_lp_ordenado[idx_despues - 1]
    s_antes = elo_score_simple(punto_antes.get("rango"), punto_antes.get("division"), punto_antes.get("lp"))
    s_despues = elo_score_simple(punto_despues.get("rango"), punto_despues.get("division"), punto_despues.get("lp"))
    if s_antes is None or s_despues is None:
        return None
    return s_despues - s_antes


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

            # Últimas 10 partidas (para el historial visible, SIN acotar por fecha)
            url_ids = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=10"
            ids_recientes = get_con_reintento(url_ids, headers).json()

            # NUEVO: partidas de los últimos 7 días (para los Destacados de la Semana)
            hace_7_dias = int(time.time()) - 7 * 24 * 60 * 60
            url_ids_semana = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&startTime={hace_7_dias}&count=25"
            ids_semana = get_con_reintento(url_ids_semana, headers).json()

            # Unimos ambas listas sin duplicar: una partida puede estar en las dos a la vez,
            # y así no pedimos su detalle dos veces.
            ids_a_consultar = list(dict.fromkeys(ids_recientes + ids_semana))
            detalles_por_id = {}
            for match_id in ids_a_consultar:
                url_match = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/{match_id}"
                detalles_por_id[match_id] = get_con_reintento(url_match, headers).json()

            historial = []
            roles_count = {"TOP": 0, "JUNGLE": 0, "MIDDLE": 0, "BOTTOM": 0, "UTILITY": 0}
            campeones_count = {}

            for match_id in ids_recientes:
                md = detalles_por_id.get(match_id)
                if not md:
                    continue
                pp = next((p for p in md["info"]["participants"] if p["puuid"] == puuid), None)

                if pp:
                    k, d, a = pp["kills"], pp["deaths"], pp["assists"]
                    kda = "Perfect" if d == 0 else f"{round((k+a)/d, 2)}"
                    campeon_jugado = pp["championName"]

                    rol_api = pp.get("teamPosition", "")
                    if rol_api in roles_count:
                        roles_count[rol_api] += 1

                    campeones_count[campeon_jugado] = campeones_count.get(campeon_jugado, 0) + 1

                    # NUEVO: estimamos el cambio de LP de esta partida usando el historial de LP
                    lp_change = calcular_lp_por_partida(md, historial_lp_jugador)

                    historial.append({
                        "campeon":   campeon_jugado,
                        "kda":       f"{k}/{d}/{a} ({kda})",
                        "resultado": "Victoria" if pp["win"] else "Derrota",
                        "duracion":  f"{md['info']['gameDuration'] // 60}min",
                        "lp_change": lp_change,
                    })

            # NUEVO: agregados de la ÚLTIMA SEMANA (7 días) para "Destacados de la Semana"
            total_kills_semana = 0
            total_pentakills_semana = 0
            total_primeras_sangre_semana = 0
            vision_scores_semana = []
            k_semana = d_semana = a_semana = 0
            campeones_ganados_semana = set()

            for match_id in ids_semana:
                md = detalles_por_id.get(match_id)
                if not md:
                    continue
                pp = next((p for p in md["info"]["participants"] if p["puuid"] == puuid), None)
                if not pp:
                    continue

                total_kills_semana += pp["kills"]
                total_pentakills_semana += pp.get("pentaKills", 0)
                total_primeras_sangre_semana += 1 if pp.get("firstBloodKill") else 0
                vision_scores_semana.append(pp.get("visionScore", 0))
                k_semana += pp["kills"]; d_semana += pp["deaths"]; a_semana += pp["assists"]
                if pp["win"]:
                    campeones_ganados_semana.add(pp["championName"])

            vision_promedio_semana = round(sum(vision_scores_semana) / len(vision_scores_semana)) if vision_scores_semana else 0
            kda_perfecto_semana = len(ids_semana) > 0 and d_semana == 0
            kda_promedio_semana = (k_semana + a_semana) if kda_perfecto_semana else (round((k_semana + a_semana) / d_semana, 2) if d_semana > 0 else 0)

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
                "kills_semana":             total_kills_semana,
                "pentakills_semana":        total_pentakills_semana,
                "vision_promedio_semana":   vision_promedio_semana,
                "primeras_sangre_semana":   total_primeras_sangre_semana,
                "kda_promedio_semana":      kda_promedio_semana,
                "kda_perfecto_semana":      kda_perfecto_semana,
                "campeones_ganados_semana": len(campeones_ganados_semana),
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

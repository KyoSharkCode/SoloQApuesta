import os
import json
import time
import calendar
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
    {"name": "adrianNOOBYT",   "tag": "LAN"},
    {"name": "Ostia",          "tag": "LAN"},
]

MAX_PUNTOS_HISTORIAL = 300

TIER_ORDER       = ['IRON','BRONZE','SILVER','GOLD','PLATINUM','EMERALD','DIAMOND','MASTER','GRANDMASTER','CHALLENGER']
DIVISIONLESS     = ['MASTER','GRANDMASTER','CHALLENGER']
DIV_NUM          = {'IV': 0, 'III': 1, 'II': 2, 'I': 3}
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
    info   = md.get("info", {})
    fin_ms = info.get("gameEndTimestamp")
    if fin_ms is None:
        fin_ms = info.get("gameCreation", 0) + info.get("gameDuration", 0) * 1000
    fin_seg = fin_ms / 1000

    punto_despues, idx_despues = None, None
    for i, punto in enumerate(progreso_lp_ordenado):
        try:
            ts_punto = datetime.fromisoformat(punto["fecha"]).timestamp()
        except (KeyError, ValueError):
            continue
        if ts_punto >= fin_seg:
            punto_despues, idx_despues = punto, i
            break

    if punto_despues is None or idx_despues == 0:
        return None

    punto_antes = progreso_lp_ordenado[idx_despues - 1]
    s_antes   = elo_score_simple(punto_antes.get("rango"), punto_antes.get("division"), punto_antes.get("lp"))
    s_despues = elo_score_simple(punto_despues.get("rango"), punto_despues.get("division"), punto_despues.get("lp"))
    if s_antes is None or s_despues is None:
        return None
    return s_despues - s_antes


def get_con_reintento(url, headers, timeout=15, max_reintentos=2):
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


def obtener_datos():
    API_KEY = os.getenv("RIOT_API_KEY", "").strip()
    if not API_KEY:
        raise ValueError("🚨 No se encontró RIOT_API_KEY en los Secrets de GitHub.")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Riot-Token": API_KEY
    }

    # ── DDragon — descargado UNA sola vez para todos los jugadores ──────────
    print("📚 Descargando diccionarios de campeones y hechizos...")
    url_ddragon_champ = "https://ddragon.leagueoflegends.com/cdn/14.20.1/data/es_ES/champion.json"
    champ_data = requests.get(url_ddragon_champ).json()["data"]
    diccionario_campeones = {int(info["key"]): nombre for nombre, info in champ_data.items()}

    url_ddragon_spell = "https://ddragon.leagueoflegends.com/cdn/14.20.1/data/es_ES/summoner.json"
    spell_data = requests.get(url_ddragon_spell).json()["data"]
    diccionario_hechizos = {int(info["key"]): info["id"] for _, info in spell_data.items()}

    # ── Límites de tiempo ────────────────────────────────────────────────────
    ahora_utc = datetime.utcnow()
    mes = ahora_utc.month
    offset_h = 2 if 3 < mes < 10 else 1

    ahora_spain_h = (ahora_utc.hour + offset_h) % 24
    if ahora_spain_h >= 6:
        hoy_6am_spain  = ahora_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        inicio_dia_utc = calendar.timegm(hoy_6am_spain.timetuple()) + (6 - offset_h) * 3600
    else:
        from datetime import timedelta
        ayer_utc       = ahora_utc - timedelta(days=1)
        ayer_6am_spain = ayer_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        inicio_dia_utc = calendar.timegm(ayer_6am_spain.timetuple()) + (6 - offset_h) * 3600

    hace_7_dias = int(time.time()) - 7 * 24 * 60 * 60
    print(f"📅 Inicio del día (hora España 6AM → UTC ts): {inicio_dia_utc}")

    # ── Cargar datos anteriores ──────────────────────────────────────────────
    datos_antiguos  = {}
    ultimo_match_id = {}
    if os.path.exists("datos.json"):
        try:
            with open("datos.json", "r", encoding="utf-8") as f:
                data_cargada = json.load(f)
                lista_antigua = data_cargada if isinstance(data_cargada, list) else data_cargada.get("jugadores", [])
                for p in lista_antigua:
                    datos_antiguos[p["nombre"]] = p
                    hist = p.get("historial", [])
                    if hist:
                        ultimo_match_id[p["nombre"]] = hist[0].get("match_id", "")
        except Exception as e:
            print(f"⚠️ No se pudo leer datos.json anterior: {e}")

    lista_final  = []
    fecha_actual = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"\n🔍 Consultando: {nombre_completo}")
        anterior = datos_antiguos.get(nombre_completo)

        try:
            # ── PUUID ──
            name_enc    = quote(jugador["name"])
            tag_enc     = quote(jugador["tag"])
            url_account = f"https://{REGION_API}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_enc}"
            puuid       = get_con_reintento(url_account, headers).json()["puuid"]

            # ── Icono ──
            url_summoner = f"https://{REGION_GAME}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            icono_id     = get_con_reintento(url_summoner, headers).json().get("profileIconId", 1)

            # ── Rango y LP ──
            url_league  = f"https://{REGION_GAME}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
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

            # ── Historial LP ──
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
                    "fecha":    fecha_actual,
                    "lp":       lp,
                    "rango":    rango,
                    "division": division
                })
            historial_lp_jugador = historial_lp_jugador[-MAX_PUNTOS_HISTORIAL:]

            # ── Top 3 Maestrías ──
            url_mast  = f"https://{REGION_GAME}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
            mast_data = get_con_reintento(url_mast, headers).json()
            if isinstance(mast_data, list):
                mast_data = sorted(mast_data, key=lambda m: m.get("championPoints", 0), reverse=True)
            maestrias = []
            for m in mast_data[:3]:
                c_nombre = diccionario_campeones.get(m["championId"], "Desconocido")
                maestrias.append({
                    "campeon": c_nombre,
                    "nivel":   m["championLevel"],
                    "puntos":  f"{m['championPoints']:,}".replace(",", ".")
                })

            # ── IDs recientes (últimas 10) para detectar si hay partidas nuevas ──
            url_ids      = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=10"
            ids_recientes = get_con_reintento(url_ids, headers).json()

            match_mas_reciente  = ids_recientes[0] if ids_recientes else ""
            sin_partidas_nuevas = bool(
                match_mas_reciente and
                match_mas_reciente == ultimo_match_id.get(nombre_completo, "")
            )

            # ════════════════════════════════════════════════════════════════
            # CASO A: Sin partidas nuevas — evitar descargar detalles de semana
            # Reutilizar historial + stats semana del anterior.
            # Solo recalcular partidas_hoy y primera_victoria_hoy desde el
            # historial guardado (sin requests adicionales a Riot).
            # ════════════════════════════════════════════════════════════════
            if sin_partidas_nuevas and anterior:
                print(f"  ⏭️ Sin partidas nuevas — reutilizando todo, recalculando solo hoy.")

                historial                  = anterior.get("historial", [])
                kills_recientes_total      = anterior.get("kills_recientes", 0)
                pentakills_recientes_total = anterior.get("pentakills_recientes", 0)
                vision_promedio_reciente   = anterior.get("vision_promedio_reciente", 0)
                rol_mas_jugado             = anterior.get("rol_principal", "Desconocido")
                top_2_roles                = anterior.get("top_roles", [])
                top_3_recientes            = anterior.get("top_recientes", [])

                # Stats semana: reutilizar directamente (no cambiaron)
                total_kills_semana           = anterior.get("kills_semana", 0)
                total_pentakills_semana      = anterior.get("pentakills_semana", 0)
                total_primeras_sangre_semana = anterior.get("primeras_sangre_semana", 0)
                total_asistencias_semana     = anterior.get("asistencias_semana", 0)
                vision_promedio_semana       = anterior.get("vision_promedio_semana", 0)
                kda_promedio_semana          = anterior.get("kda_promedio_semana", 0)
                kda_perfecto_semana          = anterior.get("kda_perfecto_semana", False)
                campeones_ganados_semana_n   = anterior.get("campeones_ganados_semana", 0)
                n_semana                     = anterior.get("partidas_semana", 0)

                # Recalcular partidas de HOY y primera victoria desde historial guardado
                # El historial guarda match_id pero no timestamp — usamos primera_victoria_hoy
                # anterior si su timestamp sigue siendo de hoy (>= inicio_dia_utc)
                partidas_hoy_count   = 0
                primera_victoria_hoy = None

                pv_anterior = anterior.get("primera_victoria_hoy")
                if pv_anterior and isinstance(pv_anterior, dict):
                    ts_pv = pv_anterior.get("timestamp", 0)
                    # Validar que sigue siendo de hoy y no es un remake
                    # duracion guardada en formato "M:SS" — convertir a segundos
                    dur_str = pv_anterior.get("duracion", "99:00")
                    try:
                        partes     = dur_str.split(":")
                        dur_seg    = int(partes[0]) * 60 + int(partes[1])
                    except Exception:
                        dur_seg    = 999
                    es_hoy    = isinstance(ts_pv, (int, float)) and (ts_pv / 1000) >= inicio_dia_utc
                    es_remake = dur_seg < 210
                    if es_hoy and not es_remake:
                        primera_victoria_hoy = pv_anterior
                        partidas_hoy_count   = anterior.get("max_partidas_en_un_dia", 0)

                max_partidas_en_un_dia = partidas_hoy_count

            # ════════════════════════════════════════════════════════════════
            # CASO B: Hay partidas nuevas — descargar y procesar todo
            # ════════════════════════════════════════════════════════════════
            else:
                # IDs de la semana
                url_ids_semana = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&startTime={hace_7_dias}&count=25"
                ids_semana     = get_con_reintento(url_ids_semana, headers).json()

                # Descargar detalles sin duplicar
                ids_a_consultar = list(dict.fromkeys(ids_recientes + ids_semana))
                detalles_por_id = {}
                for match_id in ids_a_consultar:
                    url_match = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/{match_id}"
                    detalles_por_id[match_id] = get_con_reintento(url_match, headers).json()

                # ── Historial visible (últimas 10) ──
                historial              = []
                roles_count            = {"TOP": 0, "JUNGLE": 0, "MIDDLE": 0, "BOTTOM": 0, "UTILITY": 0}
                campeones_count        = {}
                kills_recientes_total  = 0
                pentakills_recientes_total = 0
                vision_scores_recientes    = []

                for match_id in ids_recientes:
                    md = detalles_por_id.get(match_id)
                    if not md:
                        continue
                    pp = next((p for p in md["info"]["participants"] if p["puuid"] == puuid), None)
                    if not pp:
                        continue

                    dur_seg     = md["info"].get("gameDuration", 999)
                    campeon_jug = pp["championName"]

                    # Remake — aparece en historial pero NO suma stats
                    if dur_seg < 210:
                        historial.append({
                            "match_id":  match_id,
                            "campeon":   campeon_jug,
                            "kda":       "—",
                            "resultado": "Remake",
                            "duracion":  f"{dur_seg // 60}:{dur_seg % 60:02d}",
                            "lp_change": None,
                        })
                        continue

                    k, d, a = pp["kills"], pp["deaths"], pp["assists"]
                    kda     = "Perfect" if d == 0 else f"{round((k + a) / d, 2)}"
                    rol_api = pp.get("teamPosition", "")
                    if rol_api in roles_count:
                        roles_count[rol_api] += 1
                    campeones_count[campeon_jug] = campeones_count.get(campeon_jug, 0) + 1

                    kills_recientes_total      += pp["kills"]
                    pentakills_recientes_total += pp.get("pentaKills", 0)
                    vision_scores_recientes.append(pp.get("visionScore", 0))

                    lp_change = calcular_lp_por_partida(md, historial_lp_jugador)
                    historial.append({
                        "match_id":  match_id,
                        "campeon":   campeon_jug,
                        "kda":       f"{k}/{d}/{a} ({kda})",
                        "resultado": "Victoria" if pp["win"] else "Derrota",
                        "duracion":  f"{dur_seg // 60}min",
                        "lp_change": lp_change,
                    })

                vision_promedio_reciente = (
                    round(sum(vision_scores_recientes) / len(vision_scores_recientes))
                    if vision_scores_recientes else 0
                )
                mapa_roles      = {"TOP": "Top", "JUNGLE": "Jungla", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support"}
                roles_ordenados = sorted(roles_count.items(), key=lambda x: x[1], reverse=True)
                top_2_roles     = [{"rol": mapa_roles.get(r[0], r[0]), "cantidad": r[1]} for r in roles_ordenados if r[1] > 0][:2]
                rol_mas_jugado  = top_2_roles[0]["rol"] if top_2_roles else "Desconocido"
                campeones_ord   = sorted(campeones_count.items(), key=lambda x: x[1], reverse=True)[:3]
                top_3_recientes = [{"campeon": c[0], "cantidad": c[1]} for c in campeones_ord]

                # ── Agregados semanales ──
                total_kills_semana           = 0
                total_pentakills_semana      = 0
                total_primeras_sangre_semana = 0
                total_asistencias_semana     = 0
                vision_scores_semana         = []
                k_s = d_s = a_s             = 0
                campeones_ganados_semana     = set()
                partidas_semana_count        = 0
                partidas_hoy_count           = 0
                primera_victoria_hoy         = None

                for match_id in ids_semana:
                    md = detalles_por_id.get(match_id)
                    if not md:
                        continue
                    # Ignorar remakes — partidas de menos de 3:30 min
                    if md["info"].get("gameDuration", 999) < 210:
                        continue
                    pp = next((p for p in md["info"]["participants"] if p["puuid"] == puuid), None)
                    if not pp:
                        continue

                    fin_ms  = md["info"].get("gameEndTimestamp") or (md["info"].get("gameCreation", 0) + md["info"].get("gameDuration", 0) * 1000)
                    fin_seg = fin_ms / 1000

                    total_kills_semana           += pp["kills"]
                    total_pentakills_semana      += pp.get("pentaKills", 0)
                    total_primeras_sangre_semana += 1 if pp.get("firstBloodKill") else 0
                    total_asistencias_semana     += pp["assists"]
                    vision_scores_semana.append(pp.get("visionScore", 0))
                    k_s += pp["kills"]
                    d_s += pp["deaths"]
                    a_s += pp["assists"]
                    if pp["win"]:
                        campeones_ganados_semana.add(pp["championName"])
                    partidas_semana_count += 1

                    if fin_seg >= inicio_dia_utc:
                        partidas_hoy_count += 1

                    if pp["win"] and fin_seg >= inicio_dia_utc:
                        if primera_victoria_hoy is None or fin_seg < (primera_victoria_hoy["timestamp"] / 1000):
                            k_pv, d_pv, a_pv = pp["kills"], pp["deaths"], pp["assists"]
                            kda_pv = "Perfect" if d_pv == 0 else f"{round((k_pv + a_pv) / d_pv, 2)}"
                            equipo = "blue" if pp.get("teamId") == 100 else "red"
                            spell1 = diccionario_hechizos.get(pp.get("summoner1Id", 0), "SummonerFlash")
                            spell2 = diccionario_hechizos.get(pp.get("summoner2Id", 0), "SummonerDot")
                            primera_victoria_hoy = {
                                "timestamp": int(fin_seg * 1000),
                                "campeon":   pp["championName"],
                                "kda":       f"{k_pv}/{d_pv}/{a_pv} ({kda_pv})",
                                "equipo":    equipo,
                                "duracion":  f"{md['info']['gameDuration'] // 60}:{md['info']['gameDuration'] % 60:02d}",
                                "hechizos":  [spell1, spell2],
                            }

                n_semana               = partidas_semana_count
                max_partidas_en_un_dia = partidas_hoy_count
                vision_promedio_semana = round(sum(vision_scores_semana) / len(vision_scores_semana)) if vision_scores_semana else 0
                kda_perfecto_semana    = n_semana > 0 and d_s == 0
                kda_promedio_semana    = 0 if kda_perfecto_semana else (round((k_s + a_s) / d_s, 2) if d_s > 0 else 0)
                campeones_ganados_semana_n = len(campeones_ganados_semana)

            print(f"    📊 {nombre_completo}: semana={n_semana}p, hoy={max_partidas_en_un_dia}p, asistencias={total_asistencias_semana}, primera_victoria={'sí' if primera_victoria_hoy else 'no'}")

            lista_final.append({
                "nombre":                    nombre_completo,
                "icono":                     icono_id,
                "rango":                     f"{rango} {division}".strip(),
                "lp":                        lp,
                "winrate":                   winrate,
                "rol_principal":             rol_mas_jugado,
                "top_roles":                 top_2_roles,
                "top_recientes":             top_3_recientes,
                "maestrias":                 maestrias,
                "progreso_lp":               historial_lp_jugador,
                "historial":                 historial,
                # Últimas 10 partidas
                "kills_recientes":           kills_recientes_total,
                "pentakills_recientes":      pentakills_recientes_total,
                "vision_promedio_reciente":  vision_promedio_reciente,
                # Semana (7 días)
                "kills_semana":              total_kills_semana,
                "pentakills_semana":         total_pentakills_semana,
                "vision_promedio_semana":    vision_promedio_semana,
                "primeras_sangre_semana":    total_primeras_sangre_semana,
                "kda_promedio_semana":       kda_promedio_semana,
                "kda_perfecto_semana":       kda_perfecto_semana,
                "campeones_ganados_semana":  campeones_ganados_semana_n,
                "asistencias_semana":        total_asistencias_semana,
                "partidas_semana":           n_semana,
                # Diarios (desde 6AM España de hoy)
                "max_partidas_en_un_dia":    max_partidas_en_un_dia,
                "primera_victoria_hoy":      primera_victoria_hoy,
            })
            print(f"  ✓ {nombre_completo} actualizado correctamente.")

        except Exception as e:
            print(f"🚨 Error con {nombre_completo}: {e}")
            if anterior:
                print(f"  ↩️ Conservando últimos datos de {nombre_completo}.")
                anterior.setdefault("kills_recientes",          0)
                anterior.setdefault("pentakills_recientes",     0)
                anterior.setdefault("vision_promedio_reciente", 0)
                anterior.setdefault("kills_semana",             0)
                anterior.setdefault("pentakills_semana",        0)
                anterior.setdefault("vision_promedio_semana",   0)
                anterior.setdefault("primeras_sangre_semana",   0)
                anterior.setdefault("kda_promedio_semana",      0)
                anterior.setdefault("kda_perfecto_semana",      False)
                anterior.setdefault("campeones_ganados_semana", 0)
                anterior.setdefault("asistencias_semana",       0)
                anterior.setdefault("partidas_semana",          0)
                anterior.setdefault("max_partidas_en_un_dia",   0)
                anterior.setdefault("primera_victoria_hoy",     None)
                lista_final.append(anterior)
            else:
                print(f"  ⚠️ Sin datos previos de {nombre_completo}; se omite.")

    datos_exportar = {
        "ultimaActualizacion": datetime.now().isoformat(),
        "jugadores": lista_final
    }
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_exportar, f, indent=2, ensure_ascii=False)
    print("\n✅ datos.json actualizado correctamente.")


if __name__ == "__main__":
    obtener_datos()

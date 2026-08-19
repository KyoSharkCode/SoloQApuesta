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
        # FIX: puntos corruptos (no-dict) en progreso_lp no deben tumbar el
        # cálculo de todo el jugador — se ignoran en vez de propagar el error.
        if not isinstance(punto, dict):
            continue
        try:
            ts_punto = datetime.fromisoformat(punto["fecha"]).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        if ts_punto >= fin_seg:
            punto_despues, idx_despues = punto, i
            break

    if punto_despues is None or idx_despues == 0:
        return None

    punto_antes = progreso_lp_ordenado[idx_despues - 1]
    if not isinstance(punto_antes, dict):
        return None
    s_antes   = elo_score_simple(punto_antes.get("rango"), punto_antes.get("division"), punto_antes.get("lp"))
    s_despues = elo_score_simple(punto_despues.get("rango"), punto_despues.get("division"), punto_despues.get("lp"))
    if s_antes is None or s_despues is None:
        return None
    return s_despues - s_antes


def calcular_agregados_semana(detalle):
    """
    Recalcula los destacados semanales a partir del detalle de partidas
    (una entrada por partida, con su timestamp "fin_seg" y sus stats).

    Se usa SIEMPRE, haya o no partidas nuevas — así la ventana de 7 días
    se recalcula localmente (sin pedirle nada a la API de Riot) y una
    partida que ya pasó de los 7 días se cae del conteo aunque el
    jugador no haya vuelto a jugar. Antes, si no había partidas nuevas,
    se reutilizaban los totales viejos tal cual y una partida de hace
    8+ días se seguía contando como "de esta semana" para siempre.
    """
    n   = len(detalle)
    k_s = sum(d.get("kills", 0) for d in detalle)
    d_s = sum(d.get("muertes", 0) for d in detalle)
    a_s = sum(d.get("asistencias", 0) for d in detalle)
    vision_scores = [d.get("vision", 0) for d in detalle]
    kda_perfecto  = n > 0 and d_s == 0

    # Dúo más frecuente de la semana — cuenta con quién del grupo se repitió
    # más veces en el mismo equipo entre las partidas de los últimos 7 días.
    conteo_duo = {}
    for d in detalle:
        for companero in d.get("duo_con", []) or []:
            conteo_duo[companero] = conteo_duo.get(companero, 0) + 1
    duo_mas_frecuente = None
    if conteo_duo:
        nombre_duo, partidas_duo = max(conteo_duo.items(), key=lambda kv: kv[1])
        duo_mas_frecuente = {"nombre": nombre_duo, "partidas": partidas_duo}

    # El Farmeador — mejor CS/min de UNA sola partida de la semana (no promedio).
    # "cs" y "duracion_seg" solo existen en partidas guardadas después de este
    # cambio; las entradas viejas (guardadas antes) no las tienen y se ignoran
    # aquí hasta que se caigan de la ventana de 7 días por sí solas.
    mejor_cs_min = None
    for d in detalle:
        if "cs" not in d or not d.get("duracion_seg"):
            continue
        dur_min = max(d["duracion_seg"] / 60, 1)
        cs_por_min = round(d["cs"] / dur_min, 1)
        if mejor_cs_min is None or cs_por_min > mejor_cs_min["cs_por_min"]:
            mejor_cs_min = {"cs_por_min": cs_por_min, "cs": d["cs"], "campeon": d.get("campeon")}

    # El Defensor — mayor daño recibido en UNA sola partida de la semana.
    mayor_danio_recibido = None
    for d in detalle:
        if "damage_taken" not in d:
            continue
        if mayor_danio_recibido is None or d["damage_taken"] > mayor_danio_recibido["damage_taken"]:
            mayor_danio_recibido = {"damage_taken": d["damage_taken"], "campeon": d.get("campeon")}

    return {
        "kills_semana":             k_s,
        "pentakills_semana":        sum(d.get("pentakills", 0) for d in detalle),
        "primeras_sangre_semana":   sum(1 for d in detalle if d.get("primera_sangre")),
        "asistencias_semana":       a_s,
        "vision_promedio_semana":   round(sum(vision_scores) / len(vision_scores)) if vision_scores else 0,
        "kda_perfecto_semana":      kda_perfecto,
        "kda_promedio_semana":      0 if kda_perfecto else (round((k_s + a_s) / d_s, 2) if d_s > 0 else 0),
        "campeones_ganados_semana": len({d.get("campeon") for d in detalle if d.get("victoria") and d.get("campeon")}),
        "partidas_semana":          n,
        "duo_mas_frecuente":        duo_mas_frecuente,
        "mejor_cs_min_semana":      mejor_cs_min,
        "mayor_danio_recibido_semana": mayor_danio_recibido,
    }


def detectar_duo(md, pp, puuid_propio, nombre_por_puuid):
    """
    Devuelve los nombres (sin tag) de otros jugadores del grupo que estaban
    en el MISMO equipo en esta partida. Riot ya no expone en la API pública
    quién iba de premade, así que se infiere: misma partida + mismo equipo
    + ser parte del grupo que seguimos — en un grupo de 6 amigos, caer así
    por puro azar del matchmaking es muy raro, así que es una señal confiable
    de que jugaron en dúo/grupo.
    """
    equipo_propio = pp.get("teamId")
    duo = []
    for otro in md.get("info", {}).get("participants", []):
        otro_puuid = otro.get("puuid")
        if not otro_puuid or otro_puuid == puuid_propio:
            continue
        if otro.get("teamId") != equipo_propio:
            continue
        nombre_otro = nombre_por_puuid.get(otro_puuid)
        if nombre_otro:
            duo.append(nombre_otro.split("#")[0])
    return duo


def calcular_titulares_grupo(lista_final):
    """
    Para el historial de logros: determina quién del grupo tiene ahora mismo
    cada badge semanal "grande". Se compara el resultado de esta función
    contra el de la corrida anterior — si cambió el líder de una categoría,
    se genera un evento ("X le quitó el badge a Y"). No pide nada nuevo a
    Riot: usa los mismos campos *_semana que ya se calcularon arriba para
    cada jugador.
    """
    def lider(extractor_valor, extractor_detalle=None):
        mejor, mejor_valor = None, 0
        for j in lista_final:
            v = extractor_valor(j) or 0
            if v > mejor_valor:
                mejor_valor, mejor = v, j
        if mejor is None:
            return None
        return {
            "jugador": mejor["nombre"].split("#", 1)[0],
            "valor":   mejor_valor,
            "detalle": extractor_detalle(mejor) if extractor_detalle else None,
        }

    return {
        "agresivo": lider(
            lambda j: j.get("primeras_sangre_semana"),
            lambda j: f"{j['primeras_sangre_semana']} primera{'s' if j['primeras_sangre_semana'] != 1 else ''} sangre"),
        "kda_player": lider(
            lambda j: 999 if j.get("kda_perfecto_semana") else j.get("kda_promedio_semana"),
            lambda j: "KDA Perfecto" if j.get("kda_perfecto_semana") else f"{j['kda_promedio_semana']} KDA"),
        "champion_pool": lider(
            lambda j: j.get("campeones_ganados_semana"),
            lambda j: f"{j['campeones_ganados_semana']} campeones distintos ganados"),
        "asistente": lider(
            lambda j: j.get("asistencias_semana"),
            lambda j: f"{j['asistencias_semana']} asistencias"),
        "farmeador": lider(
            lambda j: (j.get("cs_min_semana") or {}).get("cs_por_min"),
            lambda j: f"{j['cs_min_semana']['cs_por_min']} CS/min con {j['cs_min_semana']['campeon']}"),
        "defensor": lider(
            lambda j: (j.get("danio_recibido_semana") or {}).get("damage_taken"),
            lambda j: f"{j['danio_recibido_semana']['damage_taken']:,} de daño recibido con {j['danio_recibido_semana']['campeon']}"),
    }


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
    # Metadata a nivel de grupo (no por jugador) que persiste entre corridas
    # para el historial de logros — quién tenía cada badge la corrida pasada,
    # los eventos ya generados, y quién tenía la primera victoria de hoy.
    titulares_anteriores    = {}
    eventos_previos         = []
    titular_dia_anterior    = None
    dia_referencia_anterior = None
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
                if isinstance(data_cargada, dict):
                    titulares_anteriores    = data_cargada.get("titulares", {}) or {}
                    eventos_previos         = data_cargada.get("eventos", []) or []
                    titular_dia_anterior    = data_cargada.get("titular_primera_victoria_dia")
                    dia_referencia_anterior = data_cargada.get("dia_referencia_eventos")
        except Exception as e:
            print(f"⚠️ No se pudo leer datos.json anterior: {e}")

    eventos_nuevos = []  # eventos que se generan en ESTA corrida (historial de logros)
    lista_final  = []
    # FIX: se guarda explícitamente en UTC con sufijo "Z". Antes se usaba
    # datetime.now() sin zona horaria, y el navegador (JS) interpretaba esa
    # fecha como hora LOCAL del usuario en vez de UTC, desfasando ~1-2h
    # todo el historial de LP y los filtros de "última semana" en el front.
    fecha_actual = ahora_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── PUUIDs de todo el grupo ──────────────────────────────────────────────
    # Se resuelven todos aquí arriba (antes se pedía uno por uno más abajo,
    # ya en el bucle de cada jugador) para poder cruzar partidas entre sí y
    # detectar cuándo dos del grupo jugaron juntos (dúo) — necesitamos los 6
    # PUUID disponibles desde el principio, no solo el del jugador en turno.
    print("🔗 Resolviendo PUUID de los 6 jugadores...")
    puuid_por_jugador = {}
    for jugador in JUGADORES:
        nombre_tmp = f"{jugador['name']}#{jugador['tag']}"
        try:
            name_enc    = quote(jugador["name"])
            tag_enc     = quote(jugador["tag"])
            url_account = f"https://{REGION_API}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_enc}"
            puuid_por_jugador[nombre_tmp] = get_con_reintento(url_account, headers).json()["puuid"]
        except Exception as e:
            print(f"  ⚠️ No se pudo resolver PUUID de {nombre_tmp}: {e}")
    nombre_por_puuid = {v: k for k, v in puuid_por_jugador.items()}

    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"\n🔍 Consultando: {nombre_completo}")
        anterior = datos_antiguos.get(nombre_completo)

        try:
            # ── PUUID (ya resuelto arriba, junto con el de todo el grupo) ──
            puuid = puuid_por_jugador.get(nombre_completo)
            if not puuid:
                raise ValueError(f"No se pudo resolver el PUUID de {nombre_completo}")

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
            # FIX: se descartan entradas corruptas (no-dict) que puedan venir
            # de datos.json — antes un valor suelto (p.ej. un int) en vez de
            # {"fecha":...,"lp":...} tumbaba calcular_lp_por_partida con
            # 'int' object is not subscriptable y dejaba al jugador congelado
            # para siempre (el dato corrupto nunca se limpiaba solo).
            historial_lp_jugador = [
                p for p in (anterior or {}).get("progreso_lp", []) if isinstance(p, dict)
            ]
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

                # FIX: Stats semana — antes se reutilizaban tal cual ("no
                # cambiaron"), pero la ventana de 7 días se mueve con el
                # tiempo aunque el jugador no juegue. Ahora se recalculan
                # localmente desde el detalle guardado, descartando
                # partidas que ya se salieron de los últimos 7 días —
                # sin pedirle nada nuevo a la API de Riot.
                partidas_semana_detalle = [
                    d for d in (anterior.get("partidas_semana_detalle") or [])
                    if isinstance(d, dict) and d.get("fin_seg", 0) >= hace_7_dias
                ]
                agregados_semana              = calcular_agregados_semana(partidas_semana_detalle)
                total_kills_semana            = agregados_semana["kills_semana"]
                total_pentakills_semana       = agregados_semana["pentakills_semana"]
                total_primeras_sangre_semana  = agregados_semana["primeras_sangre_semana"]
                total_asistencias_semana      = agregados_semana["asistencias_semana"]
                vision_promedio_semana        = agregados_semana["vision_promedio_semana"]
                kda_promedio_semana           = agregados_semana["kda_promedio_semana"]
                kda_perfecto_semana           = agregados_semana["kda_perfecto_semana"]
                campeones_ganados_semana_n    = agregados_semana["campeones_ganados_semana"]
                n_semana                      = agregados_semana["partidas_semana"]
                duo_semana                    = agregados_semana["duo_mas_frecuente"]
                cs_min_semana                 = agregados_semana["mejor_cs_min_semana"]
                danio_semana                  = agregados_semana["mayor_danio_recibido_semana"]

                # Recalcular partidas de HOY y primera victoria desde historial guardado
                # El historial guarda match_id pero no timestamp — usamos primera_victoria_hoy
                # anterior si su timestamp sigue siendo de hoy (>= inicio_dia_utc)
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

                # FIX: max_partidas_en_un_dia YA NO depende de si hubo
                # victoria hoy — antes, un jugador con partidas jugadas
                # hoy pero sin ninguna victoria veía este contador
                # reseteado a 0 en cada corrida sin partidas nuevas.
                # Se conserva el conteo anterior mientras siga siendo el
                # mismo "día" de referencia (6AM España); si cambió de
                # día, se resetea a 0.
                if anterior.get("dia_referencia") == inicio_dia_utc:
                    max_partidas_en_un_dia = anterior.get("max_partidas_en_un_dia", 0)
                else:
                    max_partidas_en_un_dia = 0

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
                    # FIX: hechizos de invocador por partida, para mostrarlos
                    # en el historial (index y perfil) igual que ya se
                    # muestran en la tarjeta de "primera victoria del día".
                    spell1_jug  = diccionario_hechizos.get(pp.get("summoner1Id", 0), "SummonerFlash")
                    spell2_jug  = diccionario_hechizos.get(pp.get("summoner2Id", 0), "SummonerDot")
                    # FIX: detecta si alguien más del grupo estaba en el
                    # mismo equipo en esta partida (dúo/grupo).
                    duo_con_jug = detectar_duo(md, pp, puuid, nombre_por_puuid)

                    # Remake — aparece en historial pero NO suma stats
                    if dur_seg < 210:
                        historial.append({
                            "match_id":  match_id,
                            "campeon":   campeon_jug,
                            "kda":       "—",
                            "resultado": "Remake",
                            "duracion":  f"{dur_seg // 60}:{dur_seg % 60:02d}",
                            "lp_change": None,
                            "hechizos":  [spell1_jug, spell2_jug],
                            "duo_con":   duo_con_jug,
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
                        "hechizos":  [spell1_jug, spell2_jug],
                        "duo_con":   duo_con_jug,
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
                # Se guarda el detalle por partida (no solo el total) para
                # que en corridas futuras sin partidas nuevas (CASO A) se
                # pueda recalcular la ventana de 7 días localmente.
                partidas_semana_detalle = []
                partidas_hoy_count      = 0
                primera_victoria_hoy    = None

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

                    partidas_semana_detalle.append({
                        "fin_seg":        fin_seg,
                        "kills":          pp["kills"],
                        "muertes":        pp["deaths"],
                        "asistencias":    pp["assists"],
                        "pentakills":     pp.get("pentaKills", 0),
                        "primera_sangre": bool(pp.get("firstBloodKill")),
                        "vision":         pp.get("visionScore", 0),
                        "campeon":        pp["championName"],
                        "victoria":       bool(pp["win"]),
                        "duo_con":        detectar_duo(md, pp, puuid, nombre_por_puuid),
                        # Para "El Farmeador" (CS/min) y "El Defensor" (daño
                        # recibido) — ya vienen en la misma partida que ya se
                        # descarga, no cuestan ninguna llamada extra a Riot.
                        "cs":             pp.get("totalMinionsKilled", 0) + pp.get("neutralMinionsKilled", 0),
                        "duracion_seg":   md["info"].get("gameDuration", 0),
                        "damage_taken":   pp.get("totalDamageTaken", 0),
                    })

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

                max_partidas_en_un_dia = partidas_hoy_count

                agregados_semana              = calcular_agregados_semana(partidas_semana_detalle)
                total_kills_semana            = agregados_semana["kills_semana"]
                total_pentakills_semana       = agregados_semana["pentakills_semana"]
                total_primeras_sangre_semana  = agregados_semana["primeras_sangre_semana"]
                total_asistencias_semana      = agregados_semana["asistencias_semana"]
                vision_promedio_semana        = agregados_semana["vision_promedio_semana"]
                kda_promedio_semana           = agregados_semana["kda_promedio_semana"]
                kda_perfecto_semana           = agregados_semana["kda_perfecto_semana"]
                campeones_ganados_semana_n    = agregados_semana["campeones_ganados_semana"]
                n_semana                      = agregados_semana["partidas_semana"]
                duo_semana                    = agregados_semana["duo_mas_frecuente"]
                cs_min_semana                 = agregados_semana["mejor_cs_min_semana"]
                danio_semana                  = agregados_semana["mayor_danio_recibido_semana"]

            print(f"    📊 {nombre_completo}: semana={n_semana}p, hoy={max_partidas_en_un_dia}p, asistencias={total_asistencias_semana}, primera_victoria={'sí' if primera_victoria_hoy else 'no'}")

            # ── Récord de LP de temporada ──────────────────────────────────
            # Compara el elo actual contra el mejor que este jugador haya
            # alcanzado desde que se le sigue la pista. Se guarda un score
            # numérico comparable (elo_score_simple) + una etiqueta legible.
            # Si supera su propio récord, se marca para generar un evento de
            # "historial de logros" más abajo — no pide nada nuevo a Riot,
            # solo compara rango/división/lp que ya se acaban de consultar.
            score_actual          = elo_score_simple(rango, division, lp)
            record_anterior_score = (anterior or {}).get("record_lp_score")
            record_anterior_label = (anterior or {}).get("record_lp_label")
            hubo_nuevo_record_lp  = False
            if score_actual is not None and (record_anterior_score is None or score_actual > record_anterior_score):
                record_lp_score      = score_actual
                record_lp_label      = f"{rango} {division}".strip() + (f" ({lp} LP)" if lp else "")
                # Solo cuenta como "evento" si ya existía un récord previo que
                # superar — evita generar un aviso falso en el primer run de
                # cada jugador (cuando todavía no hay nada que "batir").
                hubo_nuevo_record_lp = record_anterior_score is not None
            else:
                record_lp_score = record_anterior_score if record_anterior_score is not None else score_actual
                record_lp_label = record_anterior_label if record_anterior_label is not None else f"{rango} {division}".strip()

            if hubo_nuevo_record_lp:
                eventos_nuevos.append({
                    "timestamp":        fecha_actual,
                    "tipo":             "record_lp",
                    "icono":            "📈",
                    "categoria_label":  "Récord de temporada",
                    "jugador_nuevo":    jugador["name"],
                    "jugador_anterior": None,
                    "detalle":          record_lp_label,
                })
                print(f"  📈 Nuevo evento: {jugador['name']} alcanzó un nuevo récord de temporada ({record_lp_label})")

            lista_final.append({
                "nombre":                    nombre_completo,
                "icono":                     icono_id,
                "rango":                     f"{rango} {division}".strip(),
                "lp":                        lp,
                "winrate":                   winrate,
                # Récord de LP de temporada — {"record_lp_score":.., "record_lp_label":..}
                "record_lp_score":           record_lp_score,
                "record_lp_label":           record_lp_label,
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
                # Detalle de partidas de los últimos 7 días (una entrada por
                # partida) — permite recalcular los agregados de arriba en
                # corridas futuras sin volver a pedirle nada a Riot.
                "partidas_semana_detalle":   partidas_semana_detalle,
                # Dúo más frecuente de la semana — {"nombre":..,"partidas":N} o None
                "duo_semana":                duo_semana,
                # El Farmeador — mejor CS/min en una sola partida de la semana
                # {"cs_por_min":.., "cs":.., "campeon":..} o None
                "cs_min_semana":             cs_min_semana,
                # El Defensor — mayor daño recibido en una sola partida de la semana
                # {"damage_taken":.., "campeon":..} o None
                "danio_recibido_semana":     danio_semana,
                # Diarios (desde 6AM España de hoy)
                "max_partidas_en_un_dia":    max_partidas_en_un_dia,
                "primera_victoria_hoy":      primera_victoria_hoy,
                # FIX: referencia del "día" (6AM España) usado para calcular
                # max_partidas_en_un_dia — permite saber si ese conteo sigue
                # siendo válido en la siguiente corrida o si ya es otro día.
                "dia_referencia":            inicio_dia_utc,
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
                anterior.setdefault("partidas_semana_detalle",  [])
                anterior.setdefault("duo_semana",               None)
                anterior.setdefault("cs_min_semana",            None)
                anterior.setdefault("danio_recibido_semana",    None)
                anterior.setdefault("record_lp_score",          None)
                anterior.setdefault("record_lp_label",          None)
                anterior.setdefault("max_partidas_en_un_dia",   0)
                anterior.setdefault("primera_victoria_hoy",     None)
                anterior.setdefault("dia_referencia",           None)
                lista_final.append(anterior)
            else:
                print(f"  ⚠️ Sin datos previos de {nombre_completo}; se omite.")

    # ══════════════════════════════════════════════════════════════════
    # HISTORIAL DE LOGROS (2/2) — badges semanales grupales + primera
    # victoria del día. Se compara contra lo que había la corrida pasada
    # (titulares_anteriores, titular_dia_anterior, cargados arriba) para
    # detectar cambios de líder. Todo sale de lista_final, ya calculado —
    # no pide nada nuevo a Riot.
    # ══════════════════════════════════════════════════════════════════
    CATEGORIAS_BADGE = {
        "farmeador":     {"icono": "🌾", "label": "El Farmeador"},
        "defensor":      {"icono": "🛡️", "label": "El Defensor"},
        "agresivo":      {"icono": "🩸", "label": "Agresivo"},
        "kda_player":    {"icono": "📊", "label": "KDA Player"},
        "asistente":     {"icono": "🤝", "label": "El Asistente"},
        "champion_pool": {"icono": "🎭", "label": "Maestro del Champion Pool"},
    }

    titulares_nuevos = calcular_titulares_grupo(lista_final)

    for categoria, meta in CATEGORIAS_BADGE.items():
        actual = titulares_nuevos.get(categoria)
        if not actual:
            continue
        anterior_tit     = titulares_anteriores.get(categoria)
        jugador_anterior = anterior_tit.get("jugador") if anterior_tit else None
        if actual["jugador"] != jugador_anterior:
            eventos_nuevos.append({
                "timestamp":        fecha_actual,
                "tipo":             "badge_semanal",
                "categoria":        categoria,
                "categoria_label":  meta["label"],
                "icono":            meta["icono"],
                "jugador_nuevo":    actual["jugador"],
                "jugador_anterior": jugador_anterior,
                "detalle":          actual.get("detalle"),
            })
            print(f"  🏅 Nuevo evento: {actual['jugador']} reclamó {meta['label']}" +
                  (f" (se lo quitó a {jugador_anterior})" if jugador_anterior else " (por primera vez)"))

    # Primera victoria del día — quién tiene el timestamp más temprano de hoy
    titular_dia_actual = None
    mejor_ts = None
    for j in lista_final:
        pv = j.get("primera_victoria_hoy")
        if isinstance(pv, dict) and isinstance(pv.get("timestamp"), (int, float)):
            if mejor_ts is None or pv["timestamp"] < mejor_ts:
                mejor_ts = pv["timestamp"]
                titular_dia_actual = j["nombre"].split("#", 1)[0]

    # Si ya es otro "día" (6AM España), el titular de ayer ya no aplica
    if dia_referencia_anterior != inicio_dia_utc:
        titular_dia_anterior = None

    if titular_dia_actual and titular_dia_actual != titular_dia_anterior:
        eventos_nuevos.append({
            "timestamp":        fecha_actual,
            "tipo":             "primera_victoria_dia",
            "icono":            "🌅",
            "categoria_label":  "Primera Victoria del Día",
            "jugador_nuevo":    titular_dia_actual,
            "jugador_anterior": None,
            "detalle":          None,
        })
        print(f"  🌅 Nuevo evento: {titular_dia_actual} consiguió la primera victoria del día")

    # Se guardan como máximo los últimos 40 eventos, más recientes primero.
    eventos_totales = (eventos_nuevos + eventos_previos)[:40]

    datos_exportar = {
        "ultimaActualizacion": datetime.now().isoformat(),
        "jugadores": lista_final,
        # Metadata de grupo para el historial de logros (popup + feed en el index)
        "titulares": titulares_nuevos,
        "eventos": eventos_totales,
        "titular_primera_victoria_dia": titular_dia_actual if titular_dia_actual else titular_dia_anterior,
        "dia_referencia_eventos": inicio_dia_utc,
    }
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_exportar, f, indent=2, ensure_ascii=False)
    print("\n✅ datos.json actualizado correctamente.")
    if eventos_nuevos:
        print(f"🏆 {len(eventos_nuevos)} evento(s) nuevo(s) de logros esta corrida.")


if __name__ == "__main__":
    obtener_datos()

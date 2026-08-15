import os
import json
import requests
from urllib.parse import quote
from datetime import datetime

REGION_API  = "americas"   
REGION_GAME = "la1"        

JUGADORES = [
    {"name": "Pinea",          "tag": "Pinea"},
    {"name": "Galactic Shark", "tag": "AYK"},
    {"name": "El Buñuelito",   "tag": "KyA"},
    {"name": "ゆうき まこと",    "tag": "1411"},
]

def obtener_datos():
    API_KEY = os.getenv("RIOT_API_KEY", "").strip()
    if not API_KEY:
        raise ValueError("🚨 No se encontró RIOT_API_KEY en los Secrets de GitHub.")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Riot-Token": API_KEY
    }

    datos_antiguos = {}
    if os.path.exists("datos.json"):
        try:
            with open("datos.json", "r", encoding="utf-8") as f:
                data_cargada = json.load(f)
                lista_antigua = data_cargada if isinstance(data_cargada, list) else data_cargada.get("jugadores", [])
                for p in lista_antigua:
                    datos_antiguos[p["nombre"]] = p.get("progreso_lp", [])
        except:
            pass

    lista_final = []
    # Usamos formato ISO 8601 para que Chart.js entienda las fechas reales
    fecha_actual = datetime.now().isoformat()

    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"🔍 Consultando: {nombre_completo}")

        try:
            name_enc = quote(jugador["name"])
            tag_enc  = quote(jugador["tag"])
            
            # 1. PUUID
            url_account = f"https://{REGION_API}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_enc}"
            r = requests.get(url_account, headers=headers, timeout=15)
            r.raise_for_status()
            puuid = r.json()["puuid"]

            # 2. Icono de perfil (Summoner v4)
            url_summoner = f"https://{REGION_GAME}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            rs = requests.get(url_summoner, headers=headers, timeout=15)
            rs.raise_for_status()
            icono_id = rs.json().get("profileIconId", 1)

            # 3. Rangos y LP
            url_league = f"https://{REGION_GAME}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
            rl = requests.get(url_league, headers=headers, timeout=15)
            rl.raise_for_status()
            league_data = rl.json()

            rango = "Unranked"
            division = ""
            lp = 0
            winrate = "0%"

            for mode in league_data:
                if mode.get("queueType") == "RANKED_SOLO_5x5":
                    rango    = mode["tier"].capitalize()
                    division = mode["rank"]
                    lp       = mode["leaguePoints"]
                    total    = mode["wins"] + mode["losses"]
                    winrate  = f"{round(mode['wins'] / total * 100)}%" if total > 0 else "0%"
                    break

            historial_lp_jugador = datos_antiguos.get(nombre_completo, [])
            historial_lp_jugador.append({"fecha": fecha_actual, "lp": lp})

            # 4. Historial SoloQ
            url_ids = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=10"
            ids = requests.get(url_ids, headers=headers, timeout=15).json()

            historial = []
            roles_count = {"TOP": 0, "JUNGLE": 0, "MIDDLE": 0, "BOTTOM": 0, "UTILITY": 0}
            
            for match_id in ids:
                url_match = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/{match_id}"
                md = requests.get(url_match, headers=headers, timeout=15).json()
                pp = next((p for p in md["info"]["participants"] if p["puuid"] == puuid), None)
                
                if pp:
                    k, d, a = pp["kills"], pp["deaths"], pp["assists"]
                    kda = "Perfect" if d == 0 else f"{round((k+a)/d, 2)}"
                    rol_api = pp.get("teamPosition", "")
                    if rol_api in roles_count:
                        roles_count[rol_api] += 1

                    historial.append({
                        "campeon":   pp["championName"],
                        "kda":       f"{k}/{d}/{a} ({kda})",
                        "resultado": "Victoria" if pp["win"] else "Derrota",
                        "duracion":  f"{md['info']['gameDuration'] // 60}min",
                    })

            rol_mas_jugado = max(roles_count, key=roles_count.get) if any(roles_count.values()) else "N/A"
            mapa_roles = {"TOP": "Top", "JUNGLE": "Jungla", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support", "N/A": "Unranked"}

            lista_final.append({
                "nombre":        nombre_completo,
                "icono":         icono_id,
                "rango":         f"{rango} {division}".strip(),
                "lp":            lp,
                "winrate":       winrate,
                "rol_principal": mapa_roles.get(rol_mas_jugado, "Desconocido"),
                "progreso_lp":   historial_lp_jugador,
                "historial":     historial,
            })
            print(f"  ✓ {nombre_completo} → {rango} {division} {lp} LP")

        except Exception as e:
            print(f"🚨 Error con {nombre_completo}: {e}")

    datos_exportar = {
        "ultimaActualizacion": datetime.now().isoformat(),
        "jugadores": lista_final
    }

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_exportar, f, indent=2, ensure_ascii=False)
    print("\n✅ datos.json actualizado correctamente.")

if __name__ == "__main__":
    obtener_datos()

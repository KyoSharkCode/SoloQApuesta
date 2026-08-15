import os
import json
import requests
from urllib.parse import quote
from datetime import datetime

REGION_API  = "americas"   
REGION_GAME = "la1"        

# Añade a todos tus amigos aquí (pueden ser los 8 o más)
JUGADORES = [
    {"name": "Pinea",          "tag": "Pinea"},
    {"name": "Galactic Shark", "tag": "AYK"},
    {"name": "El Buñuelito",   "tag": "KyA"},
    {"name": "ゆうき まこと",     "tag": "1411"},
    {"name": "adrianNOOBYT",     "tag": "LAN"},
]

def obtener_datos():
    # 1. Obtener la API Key
    API_KEY = os.getenv("RIOT_API_KEY", "").strip()
    if not API_KEY:
        raise ValueError("🚨 No se encontró RIOT_API_KEY en los Secrets de GitHub.")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Riot-Token": API_KEY
    }

    # 2. Descargar diccionarios de Data Dragon (Campeones y Hechizos)
    print("📚 Descargando diccionarios de Data Dragon...")
    url_champ = "https://ddragon.leagueoflegends.com/cdn/14.20.1/data/es_ES/champion.json"
    champ_data = requests.get(url_champ).json()["data"]
    diccionario_campeones = {int(info["key"]): info["id"] for _, info in champ_data.items()}
    diccionario_nombres = {int(info["key"]): info["name"] for _, info in champ_data.items()}

    url_spells = "https://ddragon.leagueoflegends.com/cdn/14.20.1/data/es_ES/summoner.json"
    spell_data = requests.get(url_spells).json()["data"]
    diccionario_hechizos = {int(info["key"]): info["id"] for _, info in spell_data.items()}

    # 3. Cargar historial antiguo de LP si existe
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
    fecha_actual = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # 4. Consultar a cada jugador
    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"🔍 Consultando: {nombre_completo}")

        try:
            # Obtener PUUID
            name_enc = quote(jugador["name"])
            tag_enc  = quote(jugador["tag"])
            url_account = f"https://{REGION_API}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_enc}"
            puuid = requests.get(url_account, headers=headers, timeout=15).json()["puuid"]

            # Obtener Icono
            url_summoner = f"https://{REGION_GAME}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            summoner_data = requests.get(url_summoner, headers=headers, timeout=15).json()
            icono_id = summoner_data.get("profileIconId", 1)

            # Obtener Rango y LP
            url_league = f"https://{REGION_GAME}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
            league_data = requests.get(url_league, headers=headers, timeout=15).json()

            rango, division, lp, winrate = "Unranked", "", 0, "0%"
            for mode in league_data:
                if mode.get("queueType") == "RANKED_SOLO_5x5":
                    rango    = mode["tier"].capitalize()
                    division = mode["rank"]
                    lp       = mode["leaguePoints"]
                    total    = mode["wins"] + mode["losses"]
                    winrate  = f"{round(mode['wins'] / total * 100)}%" if total > 0 else "0%"
                    break

            # Guardar historial de LP
            historial_lp_jugador = datos_antiguos.get(nombre_completo, [])
            historial_lp_jugador.append({"fecha": fecha_actual, "lp": lp})

            # Comprobar PARTIDA EN VIVO (Spectator)
            url_spectator = f"https://{REGION_GAME}.api.riotgames.com/lol/spectator/v5/active-games/by-puuid/{puuid}"
            resp_spectator = requests.get(url_spectator, headers=headers, timeout=15)
            
            en_partida = False
            datos_partida = None

            if resp_spectator.status_code == 200:
                en_partida = True
                s_data = resp_spectator.json()
                participantes = []
                
                for part in s_data.get("participants", []):
                    c_id = part.get("championId")
                    s1_id = part.get("spell1Id")
                    s2_id = part.get("spell2Id")
                    
                    participantes.append({
                        "nombre": part.get("riotId", part.get("summonerName", "Desconocido")),
                        "campeon_img": diccionario_campeones.get(c_id, "Desconocido"),
                        "campeon_nombre": diccionario_nombres.get(c_id, "Desconocido"),
                        "equipo": "Azul" if part.get("teamId") == 100 else "Rojo",
                        "hechizo1": diccionario_hechizos.get(s1_id, "SummonerFlash"),
                        "hechizo2": diccionario_hechizos.get(s2_id, "SummonerFlash")
                    })

                datos_partida = {
                    "modo": s_data.get("gameMode", "Desconocido"),
                    "duracion_inicio": s_data.get("gameLength", 0),
                    "participantes": participantes
                }

            # Añadir a la lista final
            lista_final.append({
                "id":               nombre_completo,
                "nombre":           nombre_completo,
                "icono":            icono_id,
                "rango":            f"{rango} {division}".strip(),
                "lp":               lp,
                "winrate":          winrate,
                "rol":              "N/A", 
                "en_partida":       en_partida,
                "datos_partida":    datos_partida,
                "progreso_lp":      historial_lp_jugador,
            })
            print(f"  ✓ {nombre_completo} actualizado. En partida: {en_partida}")

        except Exception as e:
            print(f"🚨 Error con {nombre_completo}: {e}")

    # 5. Exportar a JSON
    datos_exportar = {
        "ultimaActualizacion": datetime.now().isoformat(),
        "jugadores": lista_final
    }

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_exportar, f, indent=2, ensure_ascii=False)
    print("\n✅ datos.json actualizado correctamente.")

if __name__ == "__main__":
    obtener_datos()

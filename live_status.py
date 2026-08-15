import os
import json
import time
import requests
from urllib.parse import quote

# ================= CONFIGURACIÓN =================
# Usamos os.getenv para que GitHub Actions pueda leer la clave secreta
API_KEY = os.getenv("RIOT_API_KEY", "").strip()
REGION_ACC = "americas"   # Coincide con tu REGION_API
REGION_LOL = "la1"        # Coincide con tu REGION_GAME
VERSION_DDRAGON = "14.20.1" 
# =================================================

# IMPORTAR JUGADORES AUTOMÁTICAMENTE DESDE actualizar_datos.py
try:
    from actualizar_datos import JUGADORES as JUGADORES_RAW
    # Convertimos el formato de diccionarios [{'name': '...', 'tag': '...'}] al formato "Nombre#Tag"
    LISTA_JUGADORES = [f"{j['name']}#{j['tag']}" for j in JUGADORES_RAW]
    print(f"✅ Se han importado {len(LISTA_JUGADORES)} jugadores correctamente desde actualizar_datos.py")
except ImportError:
    print("⚠️ No se pudo importar actualizar_datos.py. Usando lista de respaldo local.")
    LISTA_JUGADORES = [
        "Pinea#Pinea",
        "Galactic Shark#AYK"
    ]

print("Cargando diccionarios de DDragon (para traducir IDs a imágenes)...")

# 1. Mapeo de Campeones
res_champs = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{VERSION_DDRAGON}/data/es_ES/champion.json").json()
CHAMP_MAP = {int(v["key"]): v["id"] for k, v in res_champs["data"].items()}

# 2. Mapeo de Hechizos
res_spells = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{VERSION_DDRAGON}/data/es_ES/summoner.json").json()
SPELL_MAP = {int(v["key"]): v["id"] for k, v in res_spells["data"].items()}

# 3. Mapeo de Runas
res_runes = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{VERSION_DDRAGON}/data/es_ES/runesReforged.json").json()
RUNE_MAP = {r["id"]: r["icon"] for r in res_runes}

def obtener_rango(summoner_id):
    url = f"https://{REGION_LOL}.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}?api_key={API_KEY}"
    try:
        res = requests.get(url)
        if res.status_code == 200:
            ligas = res.json()
            for liga in ligas:
                if liga['queueType'] == 'RANKED_SOLO_5x5':
                    return f"{liga['tier']} {liga['rank']}".title()
            return "Unranked"
    except Exception:
        pass
    return "Desconocido"

def actualizar_estado_en_vivo(jugadores):
    datos_json = {}

    for jugador in jugadores:
        print(f"\nRevisando a: {jugador}")
        try:
            nombre, tag = jugador.split("#")
        except ValueError:
            print(f"  ❌ Error de formato en: {jugador}")
            continue

        # PASO 1: Obtener PUUID del jugador
        url_acc = f"https://{REGION_ACC}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(nombre)}/{quote(tag)}?api_key={API_KEY}"
        res_acc = requests.get(url_acc)
        
        if res_acc.status_code != 200:
            print(f"  💤 No se encontró la cuenta en Riot ID.")
            continue
            
        puuid = res_acc.json()["puuid"]

        # PASO 2: Consultar si está en partida (Spectator V5)
        url_spec = f"https://{REGION_LOL}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={API_KEY}"
        res_spec = requests.get(url_spec)

        if res_spec.status_code == 200:
            print(f"  ✅ ¡Está en partida! Extrayendo los 10 jugadores...")
            partida = res_spec.json()
            
            equipo_azul = []
            equipo_rojo = []

            for p in partida["participants"]:
                p_name = p.get("riotIdGameName", "")
                p_tag = p.get("riotIdTagLine", "")
                nombre_completo = f"{p_name}#{p_tag}" if p_name and p_tag else "Jugador Oculto"

                campeon_img = CHAMP_MAP.get(p["championId"], "Desconocido")
                spell1 = SPELL_MAP.get(p["spell1Id"], "SummonerFlash")
                spell2 = SPELL_MAP.get(p["spell2Id"], "SummonerDot")

                perks = p.get("perks", {})
                perk_style = perks.get("perkStyle")
                perk_substyle = perks.get("perkSubStyle")
                
                runa1_img = RUNE_MAP.get(perk_style, "perk-images/Styles/7200_Domination.png")
                runa2_img = RUNE_MAP.get(perk_substyle, "perk-images/Styles/7201_Precision.png")

                rango = obtener_rango(p["summonerId"])

                datos_jugador = {
                    "nombre": nombre_completo,
                    "campeon_img": campeon_img,
                    "spell1": spell1,
                    "spell2": spell2,
                    "runa1_img": runa1_img,
                    "runa2_img": runa2_img,
                    "rango": rango
                }

                if p["teamId"] == 100:
                    equipo_azul.append(datos_jugador)
                else:
                    equipo_rojo.append(datos_jugador)

            datos_json[jugador] = {
                "en_partida": True,
                "cola": partida.get("gameQueueConfigId", 0),
                "tiempo": partida.get("gameLength", 0),
                "equipo_azul": equipo_azul,
                "equipo_rojo": equipo_rojo
            }
        else:
            print(f"  💤 No está en partida en este momento.")
            datos_json[jugador] = {
                "en_partida": False
            }

        time.sleep(1)

    with open('live_data.json', 'w', encoding='utf-8') as f:
        json.dump(datos_json, f, ensure_ascii=False, indent=4)
    
    print("\n✅ ¡Archivo 'live_data.json' creado/actualizado con éxito!")

if __name__ == "__main__":
    actualizar_estado_en_vivo(LISTA_JUGADORES)

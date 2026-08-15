import os
import json
import requests  # Librería moderna para conexiones seguras

# 1. Configuración de Regiones para LAN
REGION_API = "americas"  
REGION_GAME = "la1"      

# 2. LISTA DE JUGADORES
JUGADORES = [
    {"name": "Pinea", "tag": "Pinea"},
    {"name": "Galactic Shark", "tag": "AYK"}
]

def obtener_datos():
    API_KEY = os.getenv("RIOT_API_KEY")
    
    if not API_KEY or API_KEY.strip() == "":
        print("🚨 ERROR CRÍTICO: No se encontró la API Key en los Secrets de GitHub.")
        raise ValueError("Falta la RIOT_API_KEY")

    lista_final = []
    
    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"🔍 Consultando a: {nombre_completo}...")
        
        try:
            # Los encabezados oficiales que pide Riot
            headers = {
                "User-Agent": "Mozilla/5.0",
                "X-Riot-Token": API_KEY
            }
            
            # PASO A: Obtener el PUUID (requests codifica automáticamente los espacios del nombre)
            url_account = f"https://{REGION_API}://{jugador['name']}/{jugador['tag']}"
            response = requests.get(url_account, headers=headers, timeout=15)
            response.raise_for_status()
            puuid = response.json()["puuid"]
            
            # PASO B: Obtener el ID de Invocador
            url_summoner = f"https://{REGION_GAME}://{puuid}"
            response_sum = requests.get(url_summoner, headers=headers, timeout=15)
            response_sum.raise_for_status()
            summoner_id = response_sum.json()["id"]

            # PASO C: Obtener el Rango y LP
            url_league = f"https://{REGION_GAME}://{summoner_id}"
            response_league = requests.get(url_league, headers=headers, timeout=15)
            response_league.raise_for_status()
            league_data = response_league.json()
            
            rango = "Unranked"
            division = ""
            lp = 0
            winrate = "0%"
            
            for mode in league_data:
                if mode["queueType"] == "RANKED_SOLO_5x5":
                    rango = mode["tier"].capitalize()
                    division = mode["rank"]
                    lp = mode["leaguePoints"]
                    total_games = mode["wins"] + mode["losses"]
                    winrate = f"{round((mode['wins'] / total_games) * 100)}%" if total_games > 0 else "0%"

            lista_final.append({
                "nombre": nombre_completo,
                "rango": f"{rango} {division}".strip(),
                "lp": lp,
                "winrate": winrate,
                "progreso_lp": [lp],
                "historial": [
                    {"campeon": "Ver en juego", "kda": "N/A", "resultado": "Próximamente"}
                ]
            })
            
        except requests.exceptions.HTTPError as e:
            print(f"\n🚨 ERROR DE RIOT CON EL JUGADOR: {nombre_completo}")
            status_code = e.response.status_code
            if status_code == 403:
                print("❌ MOTIVO: Tu Riot API Key está VENCIDA o es INCORRECTA. Renuévala en ://riotgames.com")
            elif status_code == 404:
                print("❌ MOTIVO: Jugador no encontrado. Revisa mayúsculas/minúsculas o el #Tag.")
            else:
                print(f"❌ MOTIVO: Código de error HTTP {status_code}")
            raise e
            
        except Exception as e:
            print(f"\n🚨 ERROR GENERAL DE CONEXIÓN CON: {nombre_completo}")
            print(f"❌ DETALLE DEL FALLO: {e}")
            raise e

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(lista_final, f, indent=2, ensure_ascii=False)
    print("\n✅ ¡ÉXITO! El archivo datos.json se actualizó correctamente.")

if __name__ == "__main__":
    obtener_datos()

import os
import json
import urllib.request
import urllib.error

# 1. Configuración de Regiones para LAN
REGION_API = "americas"  
REGION_GAME = "la1"      

# 2. LISTA DE JUGADORES (Edita solo lo que está entre comillas)
JUGADORES = [
    {"name": "Galactic Shark", "tag": "AYK"},
    {"name": "El Buñuelito", "tag": "KyA"},
    {"name": "ゆうき まこと", "tag": "1411"}
]

def obtener_datos():
    API_KEY = os.getenv("RIOT_API_KEY")
    
    # Validación inicial de la clave secreta
    if not API_KEY or API_KEY.strip() == "":
        print("🚨 ERROR CRÍTICO: No se encontró la API Key en los Secrets de GitHub. Verifica el Método 2.")
        raise ValueError("Falta la RIOT_API_KEY")

    lista_final = []
    
    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"🔍 Consultando a: {nombre_completo}...")
        
        try:
            # PASO A: Obtener el PUUID
            url_account = f"https://{REGION_API}://{jugador['name']}/{jugador['tag']}?api_key={API_KEY}"
            req = urllib.request.Request(url_account, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                account_data = json.loads(response.read().decode())
                puuid = account_data["puuid"]
            
            # PASO B: Obtener el ID de Invocador
            url_summoner = f"https://{REGION_GAME}://{puuid}?api_key={API_KEY}"
            req_sum = urllib.request.Request(url_summoner, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_sum) as response_sum:
                summoner_data = json.loads(response_sum.read().decode())
                summoner_id = summoner_data["id"]

            # PASO C: Obtener el Rango y LP
            url_league = f"https://{REGION_GAME}://{summoner_id}?api_key={API_KEY}"
            req_league = urllib.request.Request(url_league, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_league) as response_league:
                league_data = json.loads(response_league.read().decode())
                
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

            # Guardar estructura limpia
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
            
        except urllib.error.HTTPError as e:
            print(f"\n🚨 ERROR CRÍTICO CON EL JUGADOR: {nombre_completo}")
            if e.code == 403:
                print("❌ MOTIVO: Tu Riot API Key está VENCIDA o es INCORRECTA. Entra a ://riotgames.com y renuévala.")
            elif e.code == 404:
                print("❌ MOTIVO: El jugador no fue encontrado. Revisa si escribiste mal las mayúsculas, minúsculas o el #Tag.")
            elif e.code == 429:
                print("❌ MOTIVO: Riot está saturado (Rate Limit excedido). Espera un par de minutos.")
            else:
                print(f"❌ MOTIVO: Error de Riot de tipo HTTP {e.code}")
            raise e
            
        except Exception as e:
            print(f"\n🚨 ERROR DE PROGRAMACIÓN CON: {nombre_completo}")
            print(f"❌ DETALLE: {e}")
            raise e

    # Guardar en el archivo JSON si todo sale bien
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(lista_final, f, indent=2, ensure_ascii=False)
    print("\n✅ ¡ÉXITO! El archivo datos.json se actualizó correctamente con tus amigos.")

if __name__ == "__main__":
    obtener_datos()

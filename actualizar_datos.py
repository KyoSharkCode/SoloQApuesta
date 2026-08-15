import os
import json
import urllib.request

# 1. Configuración inicial
API_KEY = os.getenv("RIOT_API_KEY") # Aquí GitHub inyecta tu clave secreta de forma segura
REGION_API = "americas"  # Para cuentas de LAN, LAS, NA, etc.
REGION_GAME = "la1"      # Usa 'la1' para LAN, 'la2' para LAS, 'na1' para NA

# LISTA DE TUS AMIGOS: Reemplaza estos nombres por los de tu grupo (Máximo 5-10 por ahora)
JUGADORES = [
    {"name": "Galactic Shark", "tag": "AYK"},
    {"name": "El Buñuelito", "tag": "KyA"},
    {"name": "Pinea", "tag": "Pinea"},
    {"name": "ゆうき まこと", "tag": "1411"},
]

def obtener_datos():
    lista_final = []
    
    for jugador in JUGADORES:
        try:
            # PASO A: Obtener el PUUID del jugador usando su Riot ID y Tag
            url_account = f"https://{REGION_API}://{jugador['name']}/{jugador['tag']}?api_key={API_KEY}"
            req = urllib.request.Request(url_account, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                account_data = json.loads(response.read().decode())
                puuid = account_data["puuid"]
            
            # PASO B: Obtener el ID de Invocador (necesario para el rango)
            url_summoner = f"https://{REGION_GAME}://{puuid}?api_key={API_KEY}"
            req_sum = urllib.request.Request(url_summoner, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_sum) as response_sum:
                summoner_data = json.loads(response_sum.read().decode())
                summoner_id = summoner_data["id"]

            # PASO C: Obtener el Rango, LP y Winrate
            url_league = f"https://{REGION_GAME}://{summoner_id}?api_key={API_KEY}"
            req_league = urllib.request.Request(url_league, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_league) as response_league:
                league_data = json.loads(response_league.read().decode())
                
                # Datos por defecto si el jugador no tiene rango en SoloQ
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

            # Construimos la estructura limpia para tu HTML
            lista_final.append({
                "nombre": f"{jugador['name']}#{jugador['tag']}",
                "rango": f"{rango} {division}".strip(),
                "lp": lp,
                "winrate": winrate,
                "progreso_lp": [lp], # Nota: En el futuro esto guardará el histórico
                "historial": [
                    {"campeon": "Ver en juego", "kda": "N/A", "resultado": "Próximamente"}
                ]
            })
            
        except Exception as e:
            print(f"Error cargando a {jugador['name']}: {e}")

    # Guardar los datos en el archivo datos.json
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(lista_final, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    obtener_datos()

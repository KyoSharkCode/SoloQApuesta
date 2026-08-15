import os
import json
import time
import requests
from urllib.parse import quote

# ================= CONFIGURACIÓN =================
API_KEY = os.getenv("RIOT_API_KEY", "").strip()
REGION_ACC = "americas"   
REGION_LOL = "la1"        
# =================================================

# IMPORTAR JUGADORES AUTOMÁTICAMENTE DESDE actualizar_datos.py
try:
    from actualizar_datos import JUGADORES as JUGADORES_RAW
    LISTA_JUGADORES = [f"{j['name']}#{j['tag']}" for j in JUGADORES_RAW]
    print(f"✅ Se han importado {len(LISTA_JUGADORES)} jugadores correctamente desde actualizar_datos.py")
except ImportError:
    print("⚠️ No se pudo importar actualizar_datos.py. Usando lista de respaldo local.")
    LISTA_JUGADORES = [
        "Pinea#Pinea",
        "Galactic Shark#AYK"
    ]

def actualizar_estado_en_vivo(jugadores):
    datos_json = {}

    for jugador in jugadores:
        print(f"Revisando estado de: {jugador}")
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
            datos_json[jugador] = {"en_partida": False}
            continue
            
        puuid = res_acc.json()["puuid"]

        # PASO 2: Consultar si está en partida (Spectator V5)
        url_spec = f"https://{REGION_LOL}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={API_KEY}"
        res_spec = requests.get(url_spec)

        if res_spec.status_code == 200:
            print(f"  ✅ ¡Está en partida!")
            datos_json[jugador] = {"en_partida": True}
        else:
            print(f"  💤 Offline / No en partida.")
            datos_json[jugador] = {"en_partida": False}

        time.sleep(1)

    with open('live_data.json', 'w', encoding='utf-8') as f:
        json.dump(datos_json, f, ensure_ascii=False, indent=4)
    
    print("\n✅ ¡Archivo 'live_data.json' actualizado con éxito!")

if __name__ == "__main__":
    actualizar_estado_en_vivo(LISTA_JUGADORES)

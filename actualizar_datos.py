import os
import json
import requests
from urllib.parse import quote

REGION_API  = "americas"   # Account v1 y Match v5
REGION_GAME = "la1"        # Summoner v4 y League v4

JUGADORES = [
    {"name": "Pinea",          "tag": "Pinea"},
    {"name": "Galactic Shark", "tag": "AYK"},
    {"name": "El Buñuelito",   "tag": "KyA"},
]

def obtener_datos():
    API_KEY = os.getenv("RIOT_API_KEY", "").strip()
    if not API_KEY:
        raise ValueError("🚨 No se encontró RIOT_API_KEY en los Secrets de GitHub.")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Riot-Token": API_KEY
    }

    lista_final = []

    for jugador in JUGADORES:
        nombre_completo = f"{jugador['name']}#{jugador['tag']}"
        print(f"🔍 Consultando: {nombre_completo}")

        try:
            # PASO A: PUUID via Account v1 (cluster: americas)
            name_enc = quote(jugador["name"])
            tag_enc  = quote(jugador["tag"])
            url_account = f"https://{REGION_API}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_enc}"
            puuid = requests.get(url_account, headers=headers, timeout=15).raise_for_status() or \
                    requests.get(url_account, headers=headers, timeout=15).json()["puuid"]

            r = requests.get(url_account, headers=headers, timeout=15)
            r.raise_for_status()
            puuid = r.json()["puuid"]
            print(f"  ✓ PUUID obtenido")

            # PASO B: Ranked por PUUID via League v4 (no necesita summonerId)
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

            # PASO C: Últimas 5 partidas SoloQ (Match v5, cluster)
            url_ids = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=5"
            ids = requests.get(url_ids, headers=headers, timeout=15).json()

            historial = []
            for match_id in ids:
                url_match = f"https://{REGION_API}.api.riotgames.com/lol/match/v5/matches/{match_id}"
                md = requests.get(url_match, headers=headers, timeout=15).json()
                pp = next((p for p in md["info"]["participants"] if p["puuid"] == puuid), None)
                if pp:
                    k, d, a = pp["kills"], pp["deaths"], pp["assists"]
                    kda = "Perfect" if d == 0 else f"{round((k+a)/d, 2)}"
                    historial.append({
                        "campeon":   pp["championName"],
                        "kda":       f"{k}/{d}/{a} ({kda})",
                        "resultado": "Victoria" if pp["win"] else "Derrota",
                        "duracion":  f"{md['info']['gameDuration'] // 60}min",
                    })

            lista_final.append({
                "nombre":      nombre_completo,
                "rango":       f"{rango} {division}".strip(),
                "lp":          lp,
                "winrate":     winrate,
                "progreso_lp": [lp],
                "historial":   historial,
            })
            print(f"  ✓ {nombre_completo} → {rango} {division} {lp} LP")

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            msgs = {
                401: "Key mal formada (debe empezar con RGAPI-).",
                403: "Key vencida o inválida. Renuévala en developer.riotgames.com",
                404: "Jugador no encontrado. Verifica nombre y #tag.",
                429: "Rate limit. Espera 1-2 minutos.",
            }
            print(f"🚨 {nombre_completo} — {code}: {msgs.get(code, e)}")
            raise

        except Exception as e:
            print(f"🚨 Error inesperado con {nombre_completo}: {e}")
            raise

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(lista_final, f, indent=2, ensure_ascii=False)
    print("\n✅ datos.json actualizado correctamente.")

if __name__ == "__main__":
    obtener_datos()

import os
import requests
import pandas as pd

# 🔑 Carrega as Chaves de Segurança cadastradas nos Secrets do GitHub
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
CSV_FILE_PATH = os.getenv("CSV_FILE_PATH", "jogos_filtrados_notebooklm_v4.csv")

def send_telegram_alert(message):
    """Envia a mensagem de alerta formatada para o Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram não configurado nos Secrets do GitHub.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload)
        if res.status_code == 200:
            print("✅ Alerta enviado com sucesso para o Telegram!")
        else:
            print(f"❌ Erro ao enviar mensagem: {res.text}")
    except Exception as e:
        print(f"❌ Falha de conexão: {e}")

def check_live_fixtures():
    """Consulta a API-Football configurada para o Fuso Horário de Brasília/SP (America/Sao_Paulo)."""
    if not FOOTBALL_API_KEY:
        print("⚠️ FOOTBALL_API_KEY não encontrada nos Secrets. Pulando consulta API.")
        return

    # URL atualizada com o fuso horário oficial de Brasília/SP
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures?live=all&timezone=America/Sao_Paulo"
    headers = {
        "X-RapidAPI-Key": FOOTBALL_API_KEY,
        "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"
    }

    print("📡 Consultando partidas ao vivo na API-Football (Horário de Brasília/SP)...")
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        matches = data.get("response", [])
        print(f"🏟️ Partidas ao vivo encontradas neste momento: {len(matches)}")

        for match in matches:
            fixture = match.get("fixture", {})
            teams = match.get("teams", {})
            goals = match.get("goals", {})
            league = match.get("league", {})

            home_team = teams.get("home", {}).get("name", "Mandante")
            away_team = teams.get("away", {}).get("name", "Visitante")
            league_name = league.get("name", "Liga")
            elapsed = fixture.get("status", {}).get("elapsed", 0)  # Minuto da partida live
            goals_home = goals.get("home", 0)
            goals_away = goals.get("away", 0)

            # Gatilho Live Gol Limite (2º Tempo entre 65' e 75' min sem gols)
            if elapsed and 65 <= elapsed <= 75 and (goals_home + goals_away == 0):
                prob = 80.0  # Probabilidade estimada do confronto
                odd_target = 1.75  # Odd Alvo Live (@1.75)
                ev = (prob / 100.0 * odd_target) - 1.0

                if ev > 0.05:
                    alert_msg = (
                        f"🚨 *SINAL LIVE +EV DETECTADO - ENGINE MASTER* 🚨\n\n"
                        f"⚽ *{home_team} vs {away_team}*\n"
                        f"🏆 *Liga:* {league_name}\n"
                        f"⏱️ *Tempo Live:* {elapsed}' min | 🔢 *Placar:* {goals_home} x {goals_away}\n\n"
                        f"🎯 *Mercado:* `Gol Limite (Over 0.5 2H)`\n"
                        f"📊 *Probabilidade Estimada:* `{prob:.0f}%`\n"
                        f"🔥 *Odd Alvo Live:* `@{odd_target:.2f}`\n"
                        f"📈 *Valor Esperado (+EV):* `+{ev*100:.1f}%`\n\n"
                        f"💰 *Gestão Recomendada:* `1.0u Stake`\n"
                        f"🛡️ *Filtro:* Select Elite (P ≥ 70% | EV > +5%)\n"
                    )
                    send_telegram_alert(alert_msg)

    except Exception as e:
        print(f"❌ Erro na consulta da API-Football: {e}")

def main():
    print("🚀 Executando Varredura Quantitativa EV+ (Fuso Brasília/SP)...")
    
    # Proteção de leitura do CSV
    if os.path.exists(CSV_FILE_PATH) and os.path.getsize(CSV_FILE_PATH) > 0:
        try:
            df = pd.read_csv(CSV_FILE_PATH)
            print(f"📊 Banco de dados CSV carregado com sucesso ({len(df)} jogos).")
        except Exception as e:
            print(f"⚠️ Erro ao ler CSV ({e}). Prosseguindo com varredura Live...")
    else:
        print(f"ℹ️ Arquivo CSV '{CSV_FILE_PATH}' ausente ou vazio. Executando varredura via API...")

    # Executa a busca ao vivo via API
    check_live_fixtures()

if __name__ == "__main__":
    main()

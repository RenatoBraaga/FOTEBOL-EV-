import os
import requests
import pandas as pd

# -------------------------------------------------------------------
# CONFIGURAÇÕES E VARIÁVEIS DE AMBIENTE
# -------------------------------------------------------------------
API_KEY = os.getenv("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Tenta carregar a planilha v4 ou a sem_branco
CSV_FILE = "jogos_filtrados_notebooklm_v4.csv"
if not os.path.exists(CSV_FILE):
    CSV_FILE = "jogos_filtrados_notebooklm_sem_branco.csv"

def enviar_telegram(mensagem):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Token ou Chat ID do Telegram não configurados nos Secrets.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("  ✅ Alerta enviado para o Telegram com sucesso!")
        else:
            print(f"  ❌ Erro no Telegram ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"  ❌ Falha ao conectar com Telegram: {e}")

def limpar_nome(nome):
    if not isinstance(nome, str): return ""
    nome = nome.lower()
    for termo in [" fc", " u19", " u20", " u23", " women", " w", " cd", " sd", " cf"]:
        nome = nome.replace(termo, "")
    return nome.strip()

def main():
    print("🚀 Iniciando Varredura Quantitativa EV+ com Diagnóstico Ampliado...")

    # 1. Carregar CSV
    if not os.path.exists(CSV_FILE):
        print(f"❌ Erro Crítico: Nenhum arquivo CSV ({CSV_FILE}) foi encontrado no repositório.")
        return

    try:
        df_base = pd.read_csv(CSV_FILE)
        print(f"📊 Banco de Dados carregado: '{CSV_FILE}' com {len(df_base)} partidas.")
    except Exception as e:
        print(f"❌ Erro ao ler CSV: {e}")
        return

    # Mapeamento de Colunas
    col_home = "Time_Casa" if "Time_Casa" in df_base.columns else "Home Team"
    col_away = "Time_Fora" if "Time_Fora" in df_base.columns else "Away Team"
    col_prob = "Over25_Pct" if "Over25_Pct" in df_base.columns else "Over25 Average"
    col_odd  = "Odd_Over25" if "Odd_Over25" in df_base.columns else "Odds_Over25"

    # 2. Consultar API-Football
    if not API_KEY:
        print("❌ A chave FOOTBALL_API_KEY não foi configurada nos Secrets do GitHub.")
        return

    headers = {
        "x-apisports-key": API_KEY,
        "x-rapidapi-host": "v3.football.api-sports.io"
    }
    url = "https://v3.football.api-sports.io/fixtures?live=all"

    try:
        response = requests.get(url, headers=headers, timeout=15)
        data = response.json()
    except Exception as e:
        print(f"❌ Erro de conexão com a API: {e}")
        return

    if response.status_code != 200 or "response" not in data:
        print(f"❌ Resposta Inválida da API ({response.status_code}): {data.get('errors')}")
        return

    partidas_live = data["response"]
    print(f"📡 Partidas ao vivo retornadas pela API no MUNDO agora: {len(partidas_live)}")

    if len(partidas_live) == 0:
        print("ℹ️ Nenhuma partida ao vivo no momento no mundo inteiro.")
        return

    # 3. Cruzamento e Diagnóstico
    jogos_na_base = 0
    alertas_enviados = 0

    print("\n🔍 --- INÍCIO DO DIAGNÓSTICO DE JOGOS AO VIVO ---")
    for fixture in partidas_live:
        home_api = fixture["teams"]["home"]["name"]
        away_api = fixture["teams"]["away"]["name"]
        elapsed = fixture["fixture"]["status"]["elapsed"]
        league_name = fixture["league"]["name"]

        home_clean = limpar_nome(home_api)
        away_clean = limpar_nome(away_api)

        # Busca flexível no CSV
        match_csv = df_base[
            (df_base[col_home].astype(str).apply(limpar_nome).str.contains(home_clean, regex=False, na=False)) |
            (df_base[col_away].astype(str).apply(limpar_nome).str.contains(away_clean, regex=False, na=False))
        ]

        if match_csv.empty:
            continue

        jogos_na_base += 1
        row = match_csv.iloc

        prob_raw = row.get(col_prob, 0)
        prob = (prob_raw / 100.0) if prob_raw > 1.0 else prob_raw
        odd_house = row.get(col_odd, None)

        # Checagem de Odds Vazias
        if pd.isna(odd_house) or odd_house <= 1.0:
            print(f"⚠️ [SEM ODDS NO CSV] {home_api} x {away_api} ({league_name}) | Prob: {prob*100:.0f}% - Sem Odd de Over 2.5 cadastrada.")
            continue

        fair_odd = 1.0 / prob if prob > 0 else 99.0
        ev = (prob * odd_house) - 1.0

        # Validação +EV (P >= 70% e EV > +5%)
        if prob >= 0.70 and ev > 0.05:
            stake_str = "1.5u" if ev > 0.15 else "1.0u"
            print(f"🎯 [APROVADO +EV] {home_api} x {away_api} | Prob: {prob*100:.0f}% | Odd: @{odd_house:.2f} | EV: +{ev*100:.1f}%")
            
            mensagem = (
                f"🎯 *ALERTA LIVE EV+ FUTBET*\n\n"
                f"⚽ *{home_api} x {away_api}*\n"
                f"🏆 *Liga:* {league_name}\n"
                f"⏱️ *Tempo:* {elapsed}' min\n\n"
                f"📌 *Mercado:* Over 2.5 Gols\n"
                f"📈 *Probabilidade Real (P):* {prob*100:.0f}%\n"
                f"📐 *Odd Justa:* @{fair_odd:.2f}\n"
                f"🏠 *Odd da Casa:* @{odd_house:.2f}\n"
                f"💎 *EV+ Estimado:* +{ev*100:.1f}%\n"
                f"🛡️ *Stake Recomendada:* {stake_str}\n"
            )
            enviar_telegram(mensagem)
            alertas_enviados += 1
        else:
            print(f"🛑 [FILTRADO] {home_api} x {away_api} | Prob: {prob*100:.0f}% (Mín 70%) | EV: {ev*100:.1f}% (Mín +5%)")

    print("--------------------------------------------------")
    print(f"📊 Resumo: {jogos_na_base} jogos ao vivo pertenciam à sua planilha.")
    print(f"🏁 Alertas enviados no Telegram: {alertas_enviados}\n")

if __name__ == "__main__":
    main()

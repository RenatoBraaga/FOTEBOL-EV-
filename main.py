```python
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

def safe_float(val):
    try:
        if pd.isna(val) or val == "" or val is None:
            return None
        return float(val)
    except (ValueError, TypeError):
        return None

def e_liga_elite(league_name):
    if not isinstance(league_name, str): return True
    name_lower = league_name.lower()
    exclusoes = [
        "serie c", "série c", "serie d", "série d",
        "u19", "u20", "u23", "sub-19", "sub-20", "sub-23", "junior", "juniors",
        "women", "femenina", "feminin", "frauen", " 3. liga", " 4. liga", "tercera"
    ]
    for exc in exclusoes:
        if exc in name_lower:
            return False
    return True

def main():
    print("🚀 Iniciando Varredura Quantitativa EV+ com Novas Estratégias Live...")

    if not os.path.exists(CSV_FILE):
        print(f"❌ Erro Crítico: Arquivo CSV ({CSV_FILE}) não encontrado no repositório.")
        return

    try:
        df_base = pd.read_csv(CSV_FILE)
        print(f"📊 Banco de Dados carregado: '{CSV_FILE}' com {len(df_base)} partidas.")
    except Exception as e:
        print(f"❌ Erro ao ler CSV: {e}")
        return

    # Mapeamento de Colunas
    col_home     = "Time_Casa" if "Time_Casa" in df_base.columns else "Home Team"
    col_away     = "Time_Fora" if "Time_Fora" in df_base.columns else "Away Team"
    col_over25   = "Over25_Pct" if "Over25_Pct" in df_base.columns else "Over25 Average"
    col_over15ht = "Over15_HT_Pct" if "Over15_HT_Pct" in df_base.columns else "Over15 FHG HT Average"
    col_over15ft = "Over15_FT_Pct" if "Over15_FT_Pct" in df_base.columns else "Over15 Average"
    col_btts     = "BTTS_Pct" if "BTTS_Pct" in df_base.columns else "BTTS Average"

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

    jogos_na_base = 0
    alertas_enviados = 0

    print("\n🔍 --- INÍCIO DO DIAGNÓSTICO DE JOGOS AO VIVO ---")
    for fixture in partidas_live:
        home_api = fixture["teams"]["home"]["name"]
        away_api = fixture["teams"]["away"]["name"]
        elapsed = fixture["fixture"]["status"]["elapsed"] or 0
        league_name = fixture["league"]["name"]
        
        # Placar da API ao vivo
        goals_home = fixture["goals"]["home"] if fixture["goals"]["home"] is not None else 0
        goals_away = fixture["goals"]["away"] if fixture["goals"]["away"] is not None else 0
        total_gols = goals_home + goals_away

        # Filtro de Elite (Exclusão de Séries C/D, Base, Feminino)
        if not e_liga_elite(league_name):
            continue

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
        row_dict = match_csv.head(1).to_dict(orient="records").pop()

        # Extração de Métricas Pré-Jogo (0-100%)
        p_over25   = safe_float(row_dict.get(col_over25, 0)) or 0.0
        p_over15ht = safe_float(row_dict.get(col_over15ht, 0)) or 0.0
        p_over15ft = safe_float(row_dict.get(col_over15ft, 0)) or 0.0
        p_btts     = safe_float(row_dict.get(col_btts, 0)) or 0.0

        if 0 < p_over25 <= 1.0: p_over25 *= 100
        if 0 < p_over15ht <= 1.0: p_over15ht *= 100
        if 0 < p_over15ft <= 1.0: p_over15ft *= 100
        if 0 < p_btts <= 1.0: p_btts *= 100

        # --- AVALIAÇÃO DOS MÉTODOS E GATILHOS LIVE ---
        alerta_gatilho = None
        mercado_alerta = ""
        prob_alerta = 0.0
        stake_rec = "1.0u"

        # MÉTODO 1: Over 0.5 HT (Gatilho: Over 1.5 HT >= 80%, 20'-30' min, Placar 0x0)
        if p_over15ht >= 80.0 and 20 <= elapsed <= 30 and total_gols == 0:
            alerta_gatilho = "📌 METODO 1: GOL LIMITE HT (Over 0.5 HT)"
            mercado_alerta = "Over 0.5 HT"
            prob_alerta = p_over15ht
            stake_rec = "1.5u" if p_over15ht >= 90 else "1.0u"

        # MÉTODO 2: Over 1.5 FT (Gatilho: Over 2.5 FT >= 80%, 15'-40' min, Placar <= 1 gol)
        elif p_over25 >= 80.0 and 15 <= elapsed <= 40 and total_gols <= 1:
            alerta_gatilho = "📌 METODO 2: OVER 1.5 FT LIVE"
            mercado_alerta = "Over 1.5 FT"
            prob_alerta = p_over25
            stake_rec = "1.5u" if p_over25 >= 90 else "1.0u"

        # MÉTODO 3: Over Limite 70+ (Gatilho: Over 2.5 FT ou Over 1.5 FT >= 80%, Minuto >= 70, Placar Independente)
        elif (p_over25 >= 80.0 or p_over15ft >= 80.0) and elapsed >= 70:
            alerta_gatilho = "📌 METODO 3: OVER LIMITE 70+ (LATE GOAL)"
            mercado_alerta = f"Over Limite FT (Placar Atual: {goals_home}x{goals_away})"
            prob_alerta = max(p_over25, p_over15ft)
            stake_rec = "1.5u" if prob_alerta >= 90 else "1.0u"

        # MÉTODO 4: Ambas Marcam Live (Gatilho: BTTS >= 80%, 20'-30' min, Placar 0x0)
        elif p_btts >= 80.0 and 20 <= elapsed <= 30 and total_gols == 0:
            alerta_gatilho = "📌 METODO 4: AMBAS MARCAM LIVE (BTTS YES)"
            mercado_alerta = "Ambas Marcam (Sim)"
            prob_alerta = p_btts
            stake_rec = "1.5u" if p_btts >= 90 else "1.0u"

        if alerta_gatilho:
            fair_odd = 100.0 / prob_alerta if prob_alerta > 0 else 1.25
            print(f"🎯 [APROVADO LIVE] {home_api} {goals_home}x{goals_away} {away_api} ({elapsed}') | {mercado_alerta} | Prob: {prob_alerta:.0f}%")
            
            mensagem = (
                f"🎯 *ALERTA LIVE EV+ FUTBET*\n"
                f"{alerta_gatilho}\n\n"
                f"⚽ *{home_api} {goals_home} x {goals_away} {away_api}*\n"
                f"🏆 *Liga:* {league_name}\n"
                f"⏱️ *Tempo:* {elapsed}' min\n\n"
                f"📌 *Mercado:* {mercado_alerta}\n"
                f"📈 *Probabilidade Base:* {prob_alerta:.0f}%\n"
                f"📐 *Odd Justa Estimada:* @{fair_odd:.2f}\n"
                f"🛡️ *Stake Recomendada:* {stake_rec}\n"
            )
            enviar_telegram(mensagem)
            alertas_enviados += 1

    print("--------------------------------------------------")
    print(f"📊 Resumo: {jogos_na_base} jogos ao vivo pertenciam à sua planilha.")
    print(f"🏁 Alertas enviados no Telegram: {alertas_enviados}\n")

if __name__ == "__main__":
    main()
```

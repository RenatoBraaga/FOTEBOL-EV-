import os
import json
import requests
import pandas as pd
from difflib import SequenceMatcher

# -------------------------------------------------------------------
# CONFIGURAÇÕES E VARIÁVEIS DE AMBIENTE
# -------------------------------------------------------------------
API_KEY = os.getenv("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LOG_ALERTAS_FILE = "alertas_enviados.json"

# Tenta carregar a planilha v4 ou a sem_branco
CSV_FILE = "agenda_jogos_ev_positiva"
if not os.path.exists(CSV_FILE):
    CSV_FILE = "jogos_filtrados_notebooklm_sem_branco.csv"

def carregar_historico_alertas():
    if os.path.exists(LOG_ALERTAS_FILE):
        try:
            with open(LOG_ALERTAS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def salvar_historico_alertas(historico):
    try:
        with open(LOG_ALERTAS_FILE, "w") as f:
            json.dump(historico, f)
    except Exception as e:
        print(f"⚠️ Erro ao salvar histórico de alertas: {e}")

def enviar_telegram(mensagem):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Token ou Chat ID do Telegram não configurados nos Secrets.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("   ✅ Alerta enviado para o Telegram com sucesso!")
        else:
            print(f"   ❌ Erro no Telegram ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"   ❌ Falha ao conectar com Telegram: {e}")

def limpar_nome(nome):
    if not isinstance(nome, str): return ""
    nome = nome.lower()
    for termo in [" fc", " u19", " u20", " u23", " women", " w", " cd", " sd", " cf", " club", " atletico"]:
        nome = nome.replace(termo, "")
    return nome.strip()

def similaridade(a, b):
    return SequenceMatcher(None, a, b).ratio()

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

def obter_estatisticas_live(fixture_id, headers):
    """
    Consulta estatísticas ao vivo (chutes no gol, escanteios, cartões, etc.) 
    Apenas para jogos pré-qualificados nos gatilhos, economizando requisições.
    """
    url_stats = f"https://v3.football.api-sports.io/fixtures/statistics?fixture={fixture_id}"
    stats_summary = {
        "home_shots_on_target": 0,
        "away_shots_on_target": 0,
        "home_red_cards": 0,
        "away_red_cards": 0,
        "home_possession": "0%",
        "away_possession": "0%",
        "home_corners": 0,
        "away_corners": 0
    }
    try:
        res = requests.get(url_stats, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json().get("response", [])
            if len(data) >= 2:
                # Time Casa
                for item in data[0].get("statistics", []):
                    st_type = item.get("type")
                    val = item.get("value") or 0
                    if st_type == "Shots on Goal": stats_summary["home_shots_on_target"] = val
                    elif st_type == "Red Cards": stats_summary["home_red_cards"] = val
                    elif st_type == "Ball Possession": stats_summary["home_possession"] = str(val)
                    elif st_type == "Corner Kicks": stats_summary["home_corners"] = val

                # Time Fora
                for item in data[1].get("statistics", []):
                    st_type = item.get("type")
                    val = item.get("value") or 0
                    if st_type == "Shots on Goal": stats_summary["away_shots_on_target"] = val
                    elif st_type == "Red Cards": stats_summary["away_red_cards"] = val
                    elif st_type == "Ball Possession": stats_summary["away_possession"] = str(val)
                    elif st_type == "Corner Kicks": stats_summary["away_corners"] = val
    except Exception as e:
        print(f"⚠️ Falha ao buscar estatísticas do jogo {fixture_id}: {e}")
    
    return stats_summary


def main():
    print("🚀 Iniciando Varredura Quantitativa EV+ com Estratégias Live FutBET...")

    if not os.path.exists(CSV_FILE):
        print(f"❌ Erro Crítico: Arquivo CSV ({CSV_FILE}) não encontrado no repositório.")
        return

    try:
        df_base = pd.read_csv(CSV_FILE)
        print(f"📊 Banco de Dados carregado: '{CSV_FILE}' com {len(df_base)} partidas.")
    except Exception as e:
        print(f"❌ Erro ao ler CSV: {e}")
        return

    historico_alertas = carregar_historico_alertas()

    # Mapeamento de Colunas da Planilha
    col_home      = "Time_Casa" if "Time_Casa" in df_base.columns else "Home Team"
    col_away      = "Time_Fora" if "Time_Fora" in df_base.columns else "Away Team"
    col_over25    = "Over25_Pct" if "Over25_Pct" in df_base.columns else "Over25 Average"
    col_over15ht  = "Over15_HT_Pct" if "Over15_HT_Pct" in df_base.columns else "Over15 FHG HT Average"
    col_over15ft  = "Over15_FT_Pct" if "Over15_FT_Pct" in df_base.columns else "Over15 Average"
    col_btts      = "BTTS_Pct" if "BTTS_Pct" in df_base.columns else "BTTS Average"
    col_corners85 = "Average Over 8.5 Corners" if "Average Over 8.5 Corners" in df_base.columns else "Over85_Corners_Pct"
    col_corners95 = "Average Over 9.5 Corners" if "Average Over 9.5 Corners" in df_base.columns else "Over95_Corners_Pct"

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
        fixture_id = fixture["fixture"]["id"]
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

        # Busca flexível no CSV com Similaridade de Nomes
        row_dict = None
        for idx, row in df_base.iterrows():
            h_csv = limpar_nome(str(row.get(col_home, "")))
            a_csv = limpar_nome(str(row.get(col_away, "")))
            
            if (similaridade(home_clean, h_csv) > 0.65 or home_clean in h_csv) and \
               (similaridade(away_clean, a_csv) > 0.65 or away_clean in a_csv):
                row_dict = row.to_dict()
                break

        if not row_dict:
            continue

        jogos_na_base += 1

        # Extração de Métricas Pré-Jogo (Normalizadas de 0 a 100%)
        p_over25    = safe_float(row_dict.get(col_over25, 0)) or 0.0
        p_over15ht  = safe_float(row_dict.get(col_over15ht, 0)) or 0.0
        p_over15ft  = safe_float(row_dict.get(col_over15ft, 0)) or 0.0
        p_btts      = safe_float(row_dict.get(col_btts, 0)) or 0.0
        p_corners85 = safe_float(row_dict.get(col_corners85, 0)) or 0.0
        p_corners95 = safe_float(row_dict.get(col_corners95, 0)) or 0.0

        if 0 < p_over25 <= 1.0: p_over25 *= 100
        if 0 < p_over15ht <= 1.0: p_over15ht *= 100
        if 0 < p_over15ft <= 1.0: p_over15ft *= 100
        if 0 < p_btts <= 1.0: p_btts *= 100
        if 0 < p_corners85 <= 1.0: p_corners85 *= 100
        if 0 < p_corners95 <= 1.0: p_corners95 *= 100

        p_corners = max(p_corners85, p_corners95)

        # --- AVALIAÇÃO DOS MÉTODOS E GATILHOS LIVE ---
        alerta_gatilho = None
        mercado_alerta = ""
        prob_alerta = 0.0
        stake_rec = "1.0u"
        metodo_id = ""
        exige_stats_corners = False

        # MÉTODO 1: Over 0.5 HT (Gatilho: Over 1.5 HT >= 80%, 20'-30' min, Placar 0x0)
        if p_over15ht >= 80.0 and 20 <= elapsed <= 30 and total_gols == 0:
            alerta_gatilho = "📌 MÉTODO 1: GOL LIMITE HT (Over 0.5 HT)"
            mercado_alerta = "Over 0.5 HT"
            prob_alerta = p_over15ht
            stake_rec = "1.5u" if p_over15ht >= 90 else "1.0u"
            metodo_id = "M1_OVER05_HT"

        # MÉTODO 2: Over 1.5 FT (Gatilho: Over 2.5 FT >= 80%, 15'-40' min, Placar 0x0)
        elif p_over25 >= 80.0 and 15 <= elapsed <= 40 and total_gols == 0:
            alerta_gatilho = "📌 MÉTODO 2: OVER 1.5 FT LIVE"
            mercado_alerta = "Over 1.5 FT"
            prob_alerta = p_over25
            stake_rec = "1.5u" if p_over25 >= 90 else "1.0u"
            metodo_id = "M2_OVER15_FT"

        # MÉTODO 3: Over Limite 70+ (Gatilho: Over 2.5 FT ou Over 1.5 FT >= 80%, Minuto >= 70)
        elif (p_over25 >= 80.0 or p_over15ft >= 80.0) and elapsed >= 70:
            alerta_gatilho = "📌 MÉTODO 3: OVER LIMITE 70+ (LATE GOAL)"
            mercado_alerta = f"Over Limite FT (Placar Atual: {goals_home}x{goals_away})"
            prob_alerta = max(p_over25, p_over15ft)
            stake_rec = "1.5u" if prob_alerta >= 90 else "1.0u"
            metodo_id = "M3_OVER_LIMITE_70"

        # MÉTODO 4: Ambas Marcam Live (Gatilho: BTTS >= 80%, 20'-30' min, Placar 0x0)
        elif p_btts >= 80.0 and 20 <= elapsed <= 30 and total_gols == 0:
            alerta_gatilho = "📌 MÉTODO 4: AMBAS MARCAM LIVE (BTTS YES)"
            mercado_alerta = "Ambas Marcam (Sim)"
            prob_alerta = p_btts
            stake_rec = "1.5u" if p_btts >= 90 else "1.0u"
            metodo_id = "M4_BTTS_YES"

        # MÉTODO 5: Cantos / Escanteios Live (Gatilho: Cantos >= 70%, 28'-33' min, Total Cantos <= 2)
        elif p_corners >= 70.0 and 28 <= elapsed <= 33:
            alerta_gatilho = "📌 MÉTODO 5: CANTS / ESCANTEIOS LIVE"
            mercado_alerta = "Over Escanteios Limite HT/FT"
            prob_alerta = p_corners
            stake_rec = "1.0u"
            metodo_id = "M5_CORNERS_LIVE"
            exige_stats_corners = True

        # Trava Antiduplicação (Chave Única: fixture_id + metodo_id)
        chave_alerta = f"{fixture_id}_{metodo_id}"

        if alerta_gatilho and chave_alerta not in historico_alertas:
            # BUSCA DE ESTATÍSTICAS EM TEMPO REAL DA API (Gasta 1 requisição secundária apenas se o jogo for pré-aprovado)
            stats = obter_estatisticas_live(fixture_id, headers)
            
            total_corners = stats["home_corners"] + stats["away_corners"]

            # Validação Específica do Método 5 (Filtro do total de cantos no live)
            if exige_stats_corners and total_corners > 2:
                continue

            chutes_home = stats["home_shots_on_target"]
            chutes_away = stats["away_shots_on_target"]
            chutes_totais = chutes_home + chutes_away
            reds_home = stats["home_red_cards"]
            reds_away = stats["away_red_cards"]

            fair_odd = 100.0 / prob_alerta if prob_alerta > 0 else 1.25
            print(f"🎯 [APROVADO LIVE] {home_api} {goals_home}x{goals_away} {away_api} ({elapsed}') | {mercado_alerta} | Prob: {prob_alerta:.0f}%")
            
            # Montagem do Alerta com Estatísticas ao Vivo inclusas
            mensagem = (
                f"🎯 *ALERTA LIVE EV+ FUTBET*\n"
                f"{alerta_gatilho}\n\n"
                f"⚽ *{home_api} {goals_home} x {goals_away} {away_api}*\n"
                f"🏆 *Liga:* {league_name}\n"
                f"⏱️ *Tempo:* {elapsed}' min\n\n"
                f"📊 *Estatísticas em Tempo Real:*\n"
                f"🎯 *Chutes no Gol:* {chutes_home} - {chutes_away} (Total: {chutes_totais})\n"
                f"🚩 *Escanteios Totais:* {total_corners} ({stats['home_corners']} - {stats['away_corners']})\n"
                f"🛑 *Cartões Vermelhos:* {reds_home} (Casa) | {reds_away} (Fora)\n"
                f"📈 *Posse de Bola:* {stats['home_possession']} - {stats['away_possession']}\n\n"
                f"📌 *Mercado:* {mercado_alerta}\n"
                f"📈 *Probabilidade Base:* {prob_alerta:.0f}%\n"
                f"📐 *Odd Justa Estimada:* @{fair_odd:.2f}\n"
                f"🛡️ *Stake Recomendada:* {stake_rec}\n\n"
                f"🔗 https://www.bet365.bet.br/#/AX/\n"
            )
            enviar_telegram(mensagem)
            historico_alertas.append(chave_alerta)
            salvar_historico_alertas(historico_alertas)
            alertas_enviados += 1

    print("--------------------------------------------------")
    print(f"📊 Resumo: {jogos_na_base} jogos ao vivo pertenciam à sua planilha.")
    print(f"🏁 Alertas enviados no Telegram: {alertas_enviados}\n")

if __name__ == "__main__":
    main()

import os
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

# -------------------------------------------------------------------
# CONFIGURAÇÕES E VARIÁVEIS DE AMBIENTE
# -------------------------------------------------------------------
API_KEY = os.getenv("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LOG_ALERTAS_FILE = "alertas_enviados.json"

# Suporte automático às variações de nomes do arquivo no repositório
CSV_FILE = "agenda_jogos_ev_positiva.csv.csv"
if not os.path.exists(CSV_FILE):
    CSV_FILE = "jogos_filtrados_notebooklm_v4.csv"
if not os.path.exists(CSV_FILE):
    CSV_FILE = "jogos_filtrados_notebooklm_sem_branco.csv"

# Odd Padrão Auditada
ODD_AUDITADA = 1.75

def obter_horario_brt():
    """Retorna o datetime atual no fuso horário de Brasília (UTC-3)"""
    return datetime.now(timezone.utc) - timedelta(hours=3)

def carregar_historico_alertas():
    """Carrega o histórico de apostas em formato de dicionário estruturado"""
    if os.path.exists(LOG_ALERTAS_FILE):
        try:
            with open(LOG_ALERTAS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {k: {"status": "PENDENTE"} for k in data}
                return data
        except Exception:
            return {}
    return {}

def salvar_historico_alertas(historico):
    """Salva o dicionário de histórico de apostas no JSON"""
    try:
        with open(LOG_ALERTAS_FILE, "w") as f:
            json.dump(historico, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Erro ao salvar histórico de alertas: {e}")

def enviar_telegram(mensagem):
    """Envia mensagens formatadas via Bot do Telegram"""
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
            print("   ✅ Mensagem enviada para o Telegram com sucesso!")
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
    """Consulta estatísticas ao vivo (chutes no gol, escanteios, cartões, etc.)"""
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
                if isinstance(data, dict):
                    for item in data.get("statistics", []):
                        st_type = item.get("type")
                        val = item.get("value") or 0
                        if st_type == "Shots on Goal": stats_summary["home_shots_on_target"] = val
                        elif st_type == "Red Cards": stats_summary["home_red_cards"] = val
                        elif st_type == "Ball Possession": stats_summary["home_possession"] = str(val)
                        elif st_type == "Corner Kicks": stats_summary["home_corners"] = val

                if isinstance(data[1], dict):
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


# =================================----------------==================
# MÓDULO DE AUDITORIA LIVE (GREEN / RED EM TEMPO REAL E PÓS-JOGO)
# =================================--------------------------------==
def auditar_apostas_pendentes(partidas_live, historico_alertas, headers):
    """
    Varre as apostas pendentes e verifica se bateram GREEN ou RED
    tanto nos jogos ao vivo quanto nos encerrados.
    """
    if not historico_alertas:
        return

    mapa_live = {}
    for fix in partidas_live:
        f_id = fix["fixture"]["id"]
        status_short = fix["fixture"]["status"]["short"]
        elapsed = fix["fixture"]["status"]["elapsed"] or 0
        g_home = fix["goals"]["home"] if fix["goals"]["home"] is not None else 0
        g_away = fix["goals"]["away"] if fix["goals"]["away"] is not None else 0
        mapa_live[f_id] = {
            "status": status_short,
            "elapsed": elapsed,
            "goals_home": g_home,
            "goals_away": g_away,
            "total_goals": g_home + g_away
        }

    alteracao = False

    for chave, item in list(historico_alertas.items()):
        if not isinstance(item, dict) or item.get("status") != "PENDENTE":
            continue

        f_id = item.get("fixture_id")
        metodo_id = item.get("metodo_id")
        gols_alerta = item.get("gols_no_alerta", 0)
        home = item.get("home", "Mandante")
        away = item.get("away", "Visitante")
        mercado = item.get("mercado", "")

        live_data = None

        if f_id in mapa_live:
            live_data = mapa_live[f_id]
        else:
            # O jogo NÃO está mais na lista de live -> busca resultado encerrado na API
            try:
                url_fix = f"https://v3.football.api-sports.io/fixtures?id={f_id}"
                res = requests.get(url_fix, headers=headers, timeout=10)
                if res.status_code == 200:
                    resp = res.json().get("response", [])
                    if resp:
                        fix_info = resp
                        st_short = fix_info["fixture"]["status"]["short"]
                        g_h = fix_info["goals"]["home"] if fix_info["goals"]["home"] is not None else 0
                        g_a = fix_info["goals"]["away"] if fix_info["goals"]["away"] is not None else 0
                        el = fix_info["fixture"]["status"]["elapsed"] or 90
                        live_data = {
                            "status": st_short,
                            "elapsed": el,
                            "goals_home": g_h,
                            "goals_away": g_a,
                            "total_goals": g_h + g_a
                        }
            except Exception as e:
                print(f"⚠️ Erro ao consultar resultado final do jogo {f_id}: {e}")

        if live_data:
            tot_gols = live_data["total_goals"]
            elapsed = live_data["elapsed"]
            status_game = live_data["status"]

            NOVO_STATUS = None

            # --- M1: Over 0.5 HT ---
            if metodo_id == "M1_OVER05_HT":
                if tot_gols > 0:
                    NOVO_STATUS = "GREEN"
                elif status_game in ["HT", "2H", "FT", "AET", "PEN"] or elapsed > 45:
                    NOVO_STATUS = "RED"

            # --- M2: Over 1.5 FT (1H e 2H) ---
            elif metodo_id in ["M2_OVER15_FT_1H", "M2_OVER15_FT_2H"]:
                if tot_gols >= 2:
                    NOVO_STATUS = "GREEN"
                elif status_game in ["FT", "AET", "PEN"]:
                    NOVO_STATUS = "RED"

            # --- M4: Ambas Marcam (BTTS) ---
            elif metodo_id == "M4_BTTS_YES":
                if live_data["goals_home"] > 0 and live_data["goals_away"] > 0:
                    NOVO_STATUS = "GREEN"
                elif status_game in ["FT", "AET", "PEN"]:
                    NOVO_STATUS = "RED"

            # --- M5: Cantos HT ---
            elif metodo_id == "M5_CORNERS_HT":
                if status_game in ["HT", "2H", "FT", "AET", "PEN"]:
                    NOVO_STATUS = "GREEN"

            # --- M3: Late Goal (Gol no Final 70+) ---
            elif metodo_id == "M3_OVER_LIMITE_70":
                if tot_gols > gols_alerta:
                    NOVO_STATUS = "GREEN"
                elif status_game in ["FT", "AET", "PEN"]:
                    NOVO_STATUS = "RED"

            if NOVO_STATUS:
                historico_alertas[chave]["status"] = NOVO_STATUS
                alteracao = True
                
                emoji = "🟢 GREEN CONFIRMADO!" if NOVO_STATUS == "GREEN" else "🔴 RED CONFIRMADO"
                lucro_str = "+0.75u" if NOVO_STATUS == "GREEN" else "-1.00u"
                
                msg_auditoria = (
                    f"{emoji}\n"
                    f"⚽ *{home} {live_data['goals_home']} x {live_data['goals_away']} {away}*\n"
                    f"📌 *Mercado:* {mercado}\n"
                    f"💵 *Resultado:* {NOVO_STATUS} ({lucro_str})\n"
                )
                enviar_telegram(msg_auditoria)

    if alteracao:
        salvar_historico_alertas(historico_alertas)


# =================================----------------==================
# MÓDULO DE FECHAMENTO DIÁRIO (ENVIADO ÀS 23h55 BRT)
# =================================--------------------------------==
def enviar_relatorio_fechamento_diario(historico_alertas, forcar=False):
    """Gera e envia o resumo diário de ROI, Winrate e P&L no Telegram às 23h55 BRT."""
    agora_brt = obter_horario_brt()
    hoje_str = agora_brt.strftime("%Y-%m-%d")

    if not forcar and not (agora_brt.hour == 23 and agora_brt.minute >= 50):
        return

    chave_fechamento_hoje = f"FECHAMENTO_ENVIADO_{hoje_str}"
    if historico_alertas.get(chave_fechamento_hoje) and not forcar:
        return

    apostas_hoje = []
    for chave, item in historico_alertas.items():
        if isinstance(item, dict) and item.get("data_alerta") == hoje_str:
            apostas_hoje.append(item)

    if not apostas_hoje:
        print(f"ℹ️ Nenhuma aposta enviada hoje ({hoje_str}) para o fechamento.")
        return

    greens = 0
    reds = 0
    pendentes = 0
    lista_detalhada = []

    for idx, bet in enumerate(apostas_hoje, 1):
        st = bet.get("status", "PENDENTE")
        h = bet.get("home", "")
        a = bet.get("away", "")
        m = bet.get("mercado", "")
        hr = bet.get("horario_alerta", "")

        if st == "GREEN":
            greens += 1
            icon = "🟢"
        elif st == "RED":
            reds += 1
            icon = "🔴"
        else:
            pendentes += 1
            icon = "⏳"

        lista_detalhada.append(f"{idx}. {icon} `[{hr}]` *{h} x {a}* - _{m}_")

    total_resolvidos = greens + reds
    total_entradas = len(apostas_hoje)

    winrate = (greens / total_resolvidos * 100.0) if total_resolvidos > 0 else 0.0
    profit_loss = (greens * 0.75) - (reds * 1.0)
    roi = (profit_loss / total_resolvidos * 100.0) if total_resolvidos > 0 else 0.0

    pnl_sign = "+" if profit_loss >= 0 else ""
    roi_sign = "+" if roi >= 0 else ""

    texto_lista = "\n".join(lista_detalhada)

    msg_fechamento = (
        f"📊 *FECHAMENTO DIÁRIO DE AUDITORIA EV+*\n"
        f"📅 *Data:* {agora_brt.strftime('%d/%m/%Y')}\n\n"
        f"🟢 *Greens:* {greens}\n"
        f"🔴 *Reds:* {reds}\n"
        f"⏳ *Pendentes:* {pendentes}\n"
        f"🎯 *Total de Entradas no Dia:* {total_entradas}\n\n"
        f"📈 *Winrate:* {winrate:.1f}%\n"
        f"💵 *Profit / Loss (Odd @1.75):* {pnl_sign}{profit_loss:.2f}u\n"
        f"📊 *ROI Estimado:* {roi_sign}{roi:.1f}%\n\n"
        f"📋 *Todas as Apostas Enviadas Hoje:*\n"
        f"{texto_lista}\n"
    )

    enviar_telegram(msg_fechamento)
    historico_alertas[chave_fechamento_hoje] = True
    salvar_historico_alertas(historico_alertas)
    print("✅ Relatório de Fechamento Diário enviado no Telegram!")


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

    col_home       = "Time_Casa" if "Time_Casa" in df_base.columns else "Home Team"
    col_away       = "Time_Fora" if "Time_Fora" in df_base.columns else "Away Team"
    col_over25     = "Over25_Pct" if "Over25_Pct" in df_base.columns else "Over25 Average"
    col_over15ht   = "Over15_HT_Pct" if "Over15_HT_Pct" in df_base.columns else "Over15 FHG HT Average"
    col_over15ft   = "Over15_FT_Pct" if "Over15_FT_Pct" in df_base.columns else "Over15 Average"
    col_btts       = "BTTS_Pct" if "BTTS_Pct" in df_base.columns else "BTTS Average"
    col_over05_2hg = "Over05_2HG_Pct" if "Over05_2HG_Pct" in df_base.columns else "Over05 2HG Average"
    col_corners85  = "Average Over 8.5 Corners" if "Average Over 8.5 Corners" in df_base.columns else "Over85_Corners_Pct"
    col_corners95  = "Average Over 9.5 Corners" if "Average Over 9.5 Corners" in df_base.columns else "Over95_Corners_Pct"

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

    errors = data.get("errors")
    if errors and (isinstance(errors, dict) and len(errors) > 0 or isinstance(errors, list) and len(errors) > 0):
        print(f"❌ A API-Football retornou o seguinte ERRO: {errors}")
        print("💡 Verifique a Secret 'FOOTBALL_API_KEY' no GitHub ou a cota diária de requisições.")
        return

    if response.status_code != 200 or "response" not in data:
        print(f"❌ Resposta Inválida da API ({response.status_code}): {data}")
        return

    partidas_live = data["response"]
    print(f"📡 Partidas ao vivo retornadas pela API no MUNDO agora: {len(partidas_live)}")

    # Executa Auditoria das Apostas Pendentes (Tanto Live quanto Encerradas)
    auditar_apostas_pendentes(partidas_live, historico_alertas, headers)

    if len(partidas_live) == 0:
        print("ℹ️ Nenhuma partida ao vivo no momento no mundo inteiro.")
        enviar_relatorio_fechamento_diario(historico_alertas)
        return

    jogos_na_base = 0
    alertas_enviados = 0

    agora_brt = obter_horario_brt()
    hoje_str = agora_brt.strftime("%Y-%m-%d")
    horario_str = agora_brt.strftime("%H:%M:%S")

    print("\n🔍 --- INÍCIO DO DIAGNÓSTICO DE JOGOS AO VIVO ---")
    for fixture in partidas_live:
        fixture_id = fixture["fixture"]["id"]
        home_api = fixture["teams"]["home"]["name"]
        away_api = fixture["teams"]["away"]["name"]
        elapsed = fixture["fixture"]["status"]["elapsed"] or 0
        league_name = fixture["league"]["name"]
        
        goals_home = fixture["goals"]["home"] if fixture["goals"]["home"] is not None else 0
        goals_away = fixture["goals"]["away"] if fixture["goals"]["away"] is not None else 0
        total_gols = goals_home + goals_away
        
        if not e_liga_elite(league_name):
            continue

        home_clean = limpar_nome(home_api)
        away_clean = limpar_nome(away_api)

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

        p_over25     = safe_float(row_dict.get(col_over25, 0)) or 0.0
        p_over15ht   = safe_float(row_dict.get(col_over15ht, 0)) or 0.0
        p_over15ft   = safe_float(row_dict.get(col_over15ft, 0)) or 0.0
        p_btts       = safe_float(row_dict.get(col_btts, 0)) or 0.0
        p_over05_2hg = safe_float(row_dict.get(col_over05_2hg, 0)) or 0.0
        p_corners85  = safe_float(row_dict.get(col_corners85, 0)) or 0.0
        p_corners95  = safe_float(row_dict.get(col_corners95, 0)) or 0.0

        if 0 < p_over25 <= 1.0: p_over25 *= 100
        if 0 < p_over15ht <= 1.0: p_over15ht *= 100
        if 0 < p_over15ft <= 1.0: p_over15ft *= 100
        if 0 < p_btts <= 1.0: p_btts *= 100
        if 0 < p_over05_2hg <= 1.0: p_over05_2hg *= 100
        if 0 < p_corners85 <= 1.0: p_corners85 *= 100
        if 0 < p_corners95 <= 1.0: p_corners95 *= 100

        p_corners = max(p_corners85, p_corners95)

        alerta_gatilho = None
        mercado_alerta = ""
        prob_alerta = 0.0
        stake_rec = "1.0u"
        metodo_id = ""
        exige_stats_corners = False

        ODD_ALVO = 1.80

        # MÉTODO 1: Gol Limite HT (Over 0.5 HT) -> 15' a 35' min (Placar 0x0)
        if p_over15ht >= 80.0 and 15 <= elapsed <= 35 and total_gols == 0:
            alerta_gatilho = "📌 MÉTODO 1: GOL LIMITE HT (Over 0.5 HT)"
            mercado_alerta = "Over 0.5 HT"
            prob_alerta = p_over15ht
            stake_rec = "1.5u" if p_over15ht >= 90 else "1.0u"
            metodo_id = "M1_OVER05_HT"

        # MÉTODO 2: Over 1.5 FT Live -> 15' a 60' min (Placar 0x0)
        elif p_over25 >= 80.0 and 15 <= elapsed <= 60 and total_gols == 0:
            if elapsed <= 45:
                alerta_gatilho = "📌 MÉTODO 2: OVER 1.5 FT LIVE (1º Tempo)"
                metodo_id = "M2_OVER15_FT_1H"
            else:
                alerta_gatilho = "📌 MÉTODO 2: OVER 1.5 FT LIVE (2º Tempo - Reavaliação 0x0)"
                metodo_id = "M2_OVER15_FT_2H"
            mercado_alerta = "Over 1.5 FT"
            prob_alerta = p_over25
            stake_rec = "1.5u" if p_over25 >= 90 else "1.0u"

        # MÉTODO AMBAS MARCAM LIVE (BTTS Yes) -> 0' a 35' min (Placar 0x0)
        elif p_btts >= 80.0 and 0 <= elapsed <= 35 and total_gols == 0:
            alerta_gatilho = "📌 AMBAS MARCAM LIVE (BTTS YES)"
            mercado_alerta = "Ambas Marcam (Sim)"
            prob_alerta = p_btts
            stake_rec = "1.5u" if p_btts >= 90 else "1.0u"
            metodo_id = "M4_BTTS_YES"

        # MÉTODO CANTOS: Escanteios HT -> 19' a 35' min (1º Tempo, Cantos <= 2)
        elif p_corners >= 70.0 and 19 <= elapsed <= 35:
            alerta_gatilho = "📌 CANTS / ESCANTEIOS LIVE HT"
            mercado_alerta = "Over Escanteios Limite HT"
            prob_alerta = p_corners
            stake_rec = "1.0u"
            metodo_id = "M5_CORNERS_HT"
            exige_stats_corners = True

        # MOMENTO 3: Gol no Final / Late Goal -> 68' a 80' min
        elif (p_over05_2hg >= 80.0 or p_over25 >= 80.0 or p_over15ft >= 80.0) and 68 <= elapsed <= 80:
            alerta_gatilho = "📌 MOMENTO 3: OVER LIMITE 70+ (LATE GOAL)"
            mercado_alerta = f"Over Limite FT (Placar Atual: {goals_home}x{goals_away})"
            prob_alerta = max(p_over05_2hg, p_over25, p_over15ft)
            stake_rec = "1.5u" if prob_alerta >= 90 else "1.0u"
            metodo_id = "M3_OVER_LIMITE_70"

        chave_alerta = f"{fixture_id}_{metodo_id}"

        if alerta_gatilho and chave_alerta not in historico_alertas:
            stats = obter_estatisticas_live(fixture_id, headers)
            total_corners = stats["home_corners"] + stats["away_corners"]

            if exige_stats_corners and total_corners > 2:
                continue

            chutes_home = stats["home_shots_on_target"]
            chutes_away = stats["away_shots_on_target"]
            chutes_totais = chutes_home + chutes_away
            reds_home = stats["home_red_cards"]
            reds_away = stats["away_red_cards"]

            fair_odd = 100.0 / prob_alerta if prob_alerta > 0 else 1.25
            ev_estimado = ((prob_alerta / 100.0) * ODD_ALVO) - 1.0
            ev_pct = ev_estimado * 100.0

            print(f"🎯 [APROVADO LIVE] {home_api} {goals_home}x{goals_away} {away_api} ({elapsed}') | {mercado_alerta} | Prob: {prob_alerta:.0f}% | EV: +{ev_pct:.1f}%")
            
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
                f"🎯 *Odd Alvo Recomendada:* @{ODD_ALVO:.2f}\n"
                f"📐 *Odd Justa Estimada:* @{fair_odd:.2f}\n"
                f"💵 *EV+ Estimado (Odd @{ODD_ALVO:.2f}):* +{ev_pct:.1f}%\n"
                f"🛡️ *Stake Recomendada:* {stake_rec}\n\n"
                f"🔗 https://www.bet365.bet.br/#/AX/\n"
            )
            enviar_telegram(mensagem)
            
            historico_alertas[chave_alerta] = {
                "fixture_id": fixture_id,
                "metodo_id": metodo_id,
                "home": home_api,
                "away": away_api,
                "league": league_name,
                "mercado": mercado_alerta,
                "status": "PENDENTE",
                "gols_no_alerta": total_gols,
                "data_alerta": hoje_str,
                "horario_alerta": horario_str
            }
            salvar_historico_alertas(historico_alertas)
            alertas_enviados += 1

    enviar_relatorio_fechamento_diario(historico_alertas)

    print("--------------------------------------------------")
    print(f"📊 Resumo: {jogos_na_base} jogos ao vivo pertenciam à sua planilha.")
    print(f"🏁 Alertas enviados no Telegram: {alertas_enviados}\n")

if __name__ == "__main__":
    main()

import os
import json
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher

# -------------------------------------------------------------------
# CONFIGURAÇÕES E VARIÁVEIS DE AMBIENTE
# -------------------------------------------------------------------
API_KEY = os.getenv("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CACHE_FILE = "cache_data.json"
LOG_ALERTAS_FILE = "alertas_enviados.json"

MAX_DAILY_REQUESTS = 80
MIN_API_REMAINING_SAFETY = 3

POSSIVEIS_CSVS = [
    "jogos_filtrados_notebooklm_v4.csv",
    "agenda_jogos_ev_positiva.csv.csv",
    "jogos_filtrados_notebooklm_sem_branco.csv",
    "agenda_jogos_ev_positiva.csv",
]

CSV_FILE = next(
    (caminho for caminho in POSSIVEIS_CSVS if os.path.exists(caminho)),
    "jogos_filtrados_notebooklm_v4.csv",
)

ODD_AUDITADA = 1.75
requests_made_this_run = 0


def obter_horario_brt():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


def safe_int(valor, default=0):
    if valor is None:
        return default
    try:
        texto = str(valor).replace("%", "").strip()
        if not texto:
            return default
        return int(float(texto))
    except (ValueError, TypeError):
        return default


def safe_float(valor):
    if valor is None:
        return None
    try:
        if pd.isna(valor) or str(valor).strip() == "":
            return None
        return float(str(valor).replace(",", ".").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def carregar_cache():
    hoje_str = obter_horario_brt().strftime("%Y-%m-%d")
    cache_padrao = {
        "quota": {
            "date": hoje_str,
            "requests_today": 0,
            "last_api_remaining": 100,
        },
        "finished_fixtures": {},
        "stats_cache": {},
        "live_fixtures_cache": {},
    }

    if not os.path.exists(CACHE_FILE):
        return cache_padrao

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)

        if not isinstance(dados, dict):
            return cache_padrao

        quota = dados.get("quota", {})
        if not isinstance(quota, dict):
            quota = {}

        if quota.get("date") != hoje_str:
            quota = {
                "date": hoje_str,
                "requests_today": 0,
                "last_api_remaining": 100,
            }
        else:
            quota.setdefault("requests_today", 0)
            quota.setdefault("last_api_remaining", 100)

        dados["quota"] = quota

        for chave in ["finished_fixtures", "stats_cache", "live_fixtures_cache"]:
            if not isinstance(dados.get(chave), dict):
                dados[chave] = {}

        return dados

    except (json.JSONDecodeError, OSError, TypeError) as erro:
        print(f"⚠️ Erro ao ler o cache: {erro}")
        return cache_padrao


def salvar_cache(cache):
    try:
        arquivo_temporario = f"{CACHE_FILE}.tmp"
        with open(arquivo_temporario, "w", encoding="utf-8") as arquivo:
            json.dump(cache, arquivo, indent=2, ensure_ascii=False)
        os.replace(arquivo_temporario, CACHE_FILE)
    except OSError as erro:
        print(f"⚠️ Erro ao salvar o cache: {erro}")


def atualizar_headers_ratelimit(resposta, cache):
    for nome, valor in resposta.headers.items():
        nome_lower = nome.lower()
        if "requests-remaining" in nome_lower or "requests_remaining" in nome_lower:
            try:
                cache["quota"]["last_api_remaining"] = int(valor)
                return
            except (ValueError, TypeError):
                pass


def fazer_requisicao_api(url, headers, cache, timeout=15):
    global requests_made_this_run

    quota = cache.setdefault("quota", {})
    requisicoes_hoje = safe_int(quota.get("requests_today", 0))
    saldo_api = safe_int(quota.get("last_api_remaining", 100), 100)

    if requisicoes_hoje >= MAX_DAILY_REQUESTS:
        print(
            f"🛑 Limite diário atingido: {requisicoes_hoje}/{MAX_DAILY_REQUESTS}. "
            f"Chamada bloqueada: {url}"
        )
        return None

    if saldo_api <= MIN_API_REMAINING_SAFETY:
        print(
            f"🛑 Saldo crítico da API: {saldo_api} restantes. Chamada bloqueada: {url}"
        )
        return None

    try:
        resposta = requests.get(url, headers=headers, timeout=timeout)

        requests_made_this_run += 1
        quota["requests_today"] = requisicoes_hoje + 1

        atualizar_headers_ratelimit(resposta, cache)

        if resposta.status_code == 429:
            print("🛑 API retornou erro 429: limite de requisições excedido.")
            quota["last_api_remaining"] = 0
            salvar_cache(cache)
            return None

        if resposta.status_code != 200:
            print(f"⚠️ Resposta HTTP {resposta.status_code} para a URL: {url}")
            salvar_cache(cache)
            return None

        try:
            dados = resposta.json()
        except ValueError:
            print("❌ A API retornou uma resposta que não é JSON válido.")
            salvar_cache(cache)
            return None

        if not isinstance(dados, dict):
            print("❌ A API retornou um formato inválido.")
            salvar_cache(cache)
            return None

        erros = dados.get("errors")

        if erros and (
            isinstance(erros, dict) and len(erros) > 0
            or isinstance(erros, list) and len(erros) > 0
        ):
            print(f"❌ Erro da API-Football: {erros}")

            if "suspended" in str(erros).lower():
                quota["last_api_remaining"] = 0

            salvar_cache(cache)
            return None

        salvar_cache(cache)
        return dados

    except requests.RequestException as erro:
        print(f"⚠️ Erro de rede na API: {erro}")
        salvar_cache(cache)
        return None

    except Exception as erro:
        print(f"⚠️ Erro inesperado na API: {erro}")
        salvar_cache(cache)
        return None


def carregar_historico_alertas():
    if not os.path.exists(LOG_ALERTAS_FILE):
        return {}

    try:
        with open(LOG_ALERTAS_FILE, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)

        if isinstance(dados, list):
            return {str(chave): {"status": "PENDENTE"} for chave in dados}

        return dados if isinstance(dados, dict) else {}

    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def salvar_historico_alertas(historico):
    try:
        with open(LOG_ALERTAS_FILE, "w", encoding="utf-8") as arquivo:
            json.dump(historico, arquivo, indent=2, ensure_ascii=False)
    except OSError as erro:
        print(f"⚠️ Erro ao salvar histórico: {erro}")


def enviar_telegram(mensagem):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Token ou Chat ID do Telegram não configurados nos Secrets.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("   ✅ Alerta enviado para o Telegram com sucesso!")
            return True
        else:
            print(f"   ❌ Erro no Telegram ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"   ❌ Falha ao conectar com Telegram: {e}")
        return False


def limpar_nome(nome):
    if not isinstance(nome, str):
        return ""
    nome = nome.lower()
    for termo in [
        " fc",
        " u19",
        " u20",
        " u23",
        " women",
        " w",
        " cd",
        " sd",
        " cf",
        " club",
        " atletico",
    ]:
        nome = nome.replace(termo, "")
    return " ".join(nome.split()).strip()


def similaridade(valor_a, valor_b):
    return SequenceMatcher(None, valor_a, valor_b).ratio()


def e_liga_elite(nome_liga):
    if not isinstance(nome_liga, str):
        return True
    nome = nome_liga.lower()
    exclusoes = [
        "serie c",
        "série c",
        "serie d",
        "série d",
        "u19",
        "u20",
        "u23",
        "sub-19",
        "sub-20",
        "sub-23",
        "junior",
        "juniors",
        "women",
        "femenina",
        "feminin",
        "frauen",
        " 3. liga",
        " 4. liga",
        "tercera",
    ]
    return not any(exclusao in nome for exclusao in exclusoes)


def obter_estatisticas_live(fixture_id, headers, cache):
    fixture_str = str(fixture_id)
    agora = obter_horario_brt()
    stats_cache = cache.setdefault("stats_cache", {})

    entrada_cache = stats_cache.get(fixture_str)

    if isinstance(entrada_cache, dict):
        timestamp = entrada_cache.get("timestamp")
        if timestamp:
            try:
                horario_cache = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                minutos = (
                    agora.replace(tzinfo=None) - horario_cache
                ).total_seconds() / 60
                if 0 <= minutos <= 15:
                    print(f"⚡ Cache de estatísticas usado para o jogo {fixture_id}.")
                    return entrada_cache.get("data", {})
            except (ValueError, TypeError):
                pass

    resumo = {
        "home_shots_on_target": 0,
        "away_shots_on_target": 0,
        "home_red_cards": 0,
        "away_red_cards": 0,
        "home_possession": "0%",
        "away_possession": "0%",
        "home_corners": 0,
        "away_corners": 0,
    }

    url = f"https://v3.football.api-sports.io/fixtures/statistics?fixture={fixture_id}"
    resposta = fazer_requisicao_api(url, headers, cache)

    if resposta and isinstance(resposta.get("response"), list):
        dados = resposta["response"]
        for indice, time_data in enumerate(dados[:2]):
            if not isinstance(time_data, dict):
                continue
            prefixo = "home" if indice == 0 else "away"
            for item in time_data.get("statistics", []):
                if not isinstance(item, dict):
                    continue
                tipo = item.get("type")
                valor = item.get("value")
                if tipo == "Shots on Goal":
                    resumo[f"{prefixo}_shots_on_target"] = safe_int(valor)
                elif tipo == "Red Cards":
                    resumo[f"{prefixo}_red_cards"] = safe_int(valor)
                elif tipo == "Ball Possession":
                    resumo[f"{prefixo}_possession"] = (
                        str(valor) if valor is not None else "0%"
                    )
                elif tipo == "Corner Kicks":
                    resumo[f"{prefixo}_corners"] = safe_int(valor)

    stats_cache[fixture_str] = {
        "timestamp": agora.strftime("%Y-%m-%d %H:%M:%S"),
        "data": resumo,
    }
    salvar_cache(cache)
    return resumo


def extrair_dados_fixture(fixture):
    if not isinstance(fixture, dict):
        return None
    try:
        fix_data = fixture.get("fixture", {})
        status_data = fix_data.get("status", {})
        teams_data = fixture.get("teams", {})
        goals_data = fixture.get("goals", {})
        league_data = fixture.get("league", {})

        id_fix = fix_data.get("id")
        status_short = status_data.get("short")
        elapsed = fix_data.get("elapsed") or 0

        home_name = teams_data.get("home", {}).get("name", "")
        away_name = teams_data.get("away", {}).get("name", "")
        league_name = league_data.get("name", "")

        g_home = goals_data.get("home") if goals_data.get("home") is not None else 0
        g_away = goals_data.get("away") if goals_data.get("away") is not None else 0

        return {
            "id": id_fix,
            "status": status_short,
            "elapsed": elapsed,
            "home": home_name,
            "away": away_name,
            "league": league_name,
            "goals_home": g_home,
            "goals_away": g_away,
            "total_goals": g_home + g_away,
        }
    except Exception:
        return None


def auditar_apostas_pendentes(headers, cache, historico_alertas, partidas_live):
    mapa_live = {}
    for partida in partidas_live:
        dados = extrair_dados_fixture(partida)
        if dados:
            mapa_live[dados["id"]] = {
                "status": dados["status"],
                "elapsed": dados["elapsed"],
                "goals_home": dados["goals_home"],
                "goals_away": dados["goals_away"],
                "total_goals": dados["total_goals"],
            }

    finished_fixtures = cache.setdefault("finished_fixtures", {})
    houve_alteracao = False

    for chave, item in list(historico_alertas.items()):
        if not isinstance(item, dict):
            continue
        if item.get("status") != "PENDENTE":
            continue

        try:
            fixture_id = int(item.get("fixture_id"))
        except (TypeError, ValueError):
            continue

        fixture_str = str(fixture_id)
        metodo_id = item.get("metodo_id", "")
        gols_alerta = safe_int(item.get("gols_no_alerta"))
        home = item.get("home", "Mandante")
        away = item.get("away", "Visitante")
        mercado = item.get("mercado", "")

        live_data = mapa_live.get(fixture_id)
        if not live_data and fixture_str in finished_fixtures:
            live_data = finished_fixtures[fixture_str]

        if not live_data:
            url = f"https://v3.football.api-sports.io/fixtures?id={fixture_id}"
            resposta = fazer_requisicao_api(url, headers, cache)
            if (
                resposta
                and isinstance(resposta.get("response"), list)
                and resposta["response"]
            ):
                dados = extrair_dados_fixture(resposta["response"][0])
                if dados:
                    live_data = {
                        "status": dados["status"],
                        "elapsed": dados["elapsed"],
                        "goals_home": dados["goals_home"],
                        "goals_away": dados["goals_away"],
                        "total_goals": dados["total_goals"],
                    }
                    if dados["status"] in ["FT", "AET", "PEN"]:
                        finished_fixtures[fixture_str] = live_data
                        salvar_cache(cache)

        if not live_data:
            continue

        status = live_data.get("status", "")
        elapsed = safe_int(live_data.get("elapsed"))
        total_goals = safe_int(live_data.get("total_goals"))
        gols_casa = safe_int(live_data.get("goals_home"))
        gols_fora = safe_int(live_data.get("goals_away"))

        novo_status = None

        if metodo_id == "M1_OVER05_HT":
            if total_goals > 0:
                novo_status = "GREEN"
            elif status in ["HT", "2H", "FT", "AET", "PEN"] or elapsed > 45:
                novo_status = "RED"

        elif metodo_id in ["M2_OVER15_FT", "M2_OVER15_FT_1H", "M2_OVER15_FT_2H"]:
            if total_goals >= 2:
                novo_status = "GREEN"
            elif status in ["FT", "AET", "PEN"]:
                novo_status = "RED"

        elif metodo_id == "M4_BTTS_YES":
            if gols_casa > 0 and gols_fora > 0:
                novo_status = "GREEN"
            elif status in ["FT", "AET", "PEN"]:
                novo_status = "RED"

        elif metodo_id == "M5_CORNERS_HT":
            stats = obter_estatisticas_live(fixture_id, headers, cache)
            total_corners = safe_int(stats.get("home_corners")) + safe_int(
                stats.get("away_corners")
            )
            if total_corners >= 3:
                novo_status = "GREEN"
            elif status in ["FT", "AET", "PEN"]:
                novo_status = "RED"

        elif metodo_id == "M3_OVER_LIMITE_70":
            if total_goals > gols_alerta:
                novo_status = "GREEN"
            elif status in ["FT", "AET", "PEN"]:
                novo_status = "RED"

        if not novo_status:
            continue

        historico_alertas[chave]["status"] = novo_status
        houve_alteracao = True

        if novo_status == "GREEN":
            emoji = "🟢 *RESULTADO PÓS-JOGO: GREEN!* 🎯"
            lucro_str = "+0.75u"
            roi_str = "+75.0%"
        else:
            emoji = "🔴 *RESULTADO PÓS-JOGO: RED* ❌"
            lucro_str = "-1.00u"
            roi_str = "-100.0%"

        mensagem = (
            f"{emoji}\n\n"
            f"⚽ *{home} {gols_casa} x {gols_fora} {away}*\n"
            f"📌 *Mercado:* {mercado}\n"
            f"📊 *Resultado Final:* {gols_casa} x {gols_fora}\n\n"
            f"💰 *Resultado da Aposta:*\n"
            f"- Odd de Entrada: @1.75\n"
            f"- Stake: 1.0u\n"
            f"- Lucro/Prejuízo: {lucro_str}\n"
            f"- ROI do Jogo: {roi_str}\n"
        )

        enviar_telegram(mensagem)

    if houve_alteracao:
        cache["finished_fixtures"] = finished_fixtures
        salvar_cache(cache)
        salvar_historico_alertas(historico_alertas)


def enviar_relatorio_fechamento_diario(historico_alertas, forcar=False):
    agora = obter_horario_brt()
    hoje = agora.strftime("%Y-%m-%d")

    dentro_do_horario = agora.hour == 23 and agora.minute >= 50

    if not forcar and not dentro_do_horario:
        return

    chave_fechamento = f"FECHAMENTO_ENVIADO_{hoje}"

    if historico_alertas.get(chave_fechamento) and not forcar:
        return

    apostas = [
        item
        for item in historico_alertas.values()
        if isinstance(item, dict) and item.get("data_alerta") == hoje
    ]

    if not apostas:
        print(f"ℹ️ Nenhuma aposta encontrada para {hoje}.")
        return

    greens = 0
    reds = 0
    pendentes = 0
    detalhes = []

    for indice, aposta in enumerate(apostas, 1):
        status = aposta.get("status", "PENDENTE")
        home = aposta.get("home", "")
        away = aposta.get("away", "")
        mercado = aposta.get("mercado", "")
        horario = aposta.get("horario_alerta", "")

        if status == "GREEN":
            greens += 1
            icone = "🟢"
        elif status == "RED":
            reds += 1
            icone = "🔴"
        else:
            pendentes += 1
            icone = "⏳"

        detalhes.append(
            f"{indice}. {icone} [{horario}] *{home} x {away}* - _{mercado}_"
        )

    resolvidos = greens + reds
    total_entradas = len(apostas)

    winrate = (greens / resolvidos * 100) if resolvidos > 0 else 0.0
    profit_loss = greens * 0.75 - reds
    stake_total = float(resolvidos)
    roi = (profit_loss / stake_total * 100) if stake_total > 0 else 0.0

    sinal_pnl = "+" if profit_loss >= 0 else ""
    sinal_roi = "+" if roi >= 0 else ""

    mensagem = (
        f"📊 *BALANÇO FINAL DO DIA — EV+ FUTBET*\n"
        f"📅 *Data:* {agora.strftime('%d/%m/%Y')}\n\n"
        f"🎯 *Total de Operações:* {total_entradas}\n"
        f"✅ *Greens:* {greens} | ❌ *Reds:* {reds}\n"
        f"📈 *Winrate:* {winrate:.1f}%\n\n"
        f"💵 *Stake Total Investida:* {stake_total:.2f}u\n"
        f"💰 *Profit / Loss Diário:* {sinal_pnl}{profit_loss:.2f}u\n"
        f"🚀 *ROI do Dia:* {sinal_roi}{roi:.1f}%\n\n"
        f"📋 *Apostas do Dia:*\n" + "\n".join(detalhes)
    )

    if enviar_telegram(mensagem):
        historico_alertas[chave_fechamento] = True
        salvar_historico_alertas(historico_alertas)
        print("✅ Fechamento diário enviado.")


def main():
    agora = obter_horario_brt()
    print("==============================================")
    print("🚀 Robô EV+ FUTBET iniciado")
    print(f"📅 Horário BRT: {agora.strftime('%d/%m/%Y %H:%M:%S')}")

    # Trava Noturna: Não consome API entre 00:00 e 07:59 BRT
    if 0 <= agora.hour < 8:
        print("🌙 Trava Noturna Ativa (00h às 08h BRT). Execução suspensa para economizar quota.")
        return

    cache = carregar_cache()
    historico_alertas = carregar_historico_alertas()

    if not os.path.exists(CSV_FILE):
        print(f"❌ Erro Crítico: Arquivo CSV '{CSV_FILE}' não encontrado no repositório.")
        return

    try:
        df_base = pd.read_csv(CSV_FILE)
        print(f"📊 CSV carregado: {CSV_FILE} ({len(df_base)} partidas)")
    except Exception as e:
        print(f"❌ Erro ao ler CSV '{CSV_FILE}': {e}")
        return

    col_home = "Time_Casa" if "Time_Casa" in df_base.columns else "Home Team"
    col_away = "Time_Fora" if "Time_Fora" in df_base.columns else "Away Team"
    col_over25 = "Over25_Pct" if "Over25_Pct" in df_base.columns else "Over25 Average"
    col_over15ht = "Over15_HT_Pct" if "Over15_HT_Pct" in df_base.columns else "Over15 FHG HT Average"
    col_over15ft = "Over15_FT_Pct" if "Over15_FT_Pct" in df_base.columns else "Over15 Average"
    col_btts = "BTTS_Pct" if "BTTS_Pct" in df_base.columns else "BTTS Average"
    col_corners85 = "Average Over 8.5 Corners" if "Average Over 8.5 Corners" in df_base.columns else "Over85_Corners_Pct"
    col_corners95 = "Average Over 9.5 Corners" if "Average Over 9.5 Corners" in df_base.columns else "Over95_Corners_Pct"

    if not API_KEY:
        print("❌ A chave FOOTBALL_API_KEY não foi configurada nos Secrets do GitHub.")
        return

    headers = {
        "x-apisports-key": API_KEY,
        "x-rapidapi-host": "v3.football.api-sports.io",
    }

    quota = cache.get("quota", {})
    req_today = quota.get("requests_today", 0)
    rem_api = quota.get("last_api_remaining", 100)
    print(f"📊 Quota: {req_today}/{MAX_DAILY_REQUESTS} | Saldo API: {rem_api}")

    url_live = "https://v3.football.api-sports.io/fixtures?live=all"
    dados_live = fazer_requisicao_api(url_live, headers, cache)

    if not dados_live or "response" not in dados_live:
        print("⚠️ Não foi possível obter partidas ao vivo da API.")
        return

    partidas_live = dados_live["response"]
    print(f"📡 Partidas ao vivo encontradas: {len(partidas_live)}")

    jogos_na_base = 0
    alertas_enviados = 0

    for fixture in partidas_live:
        fix_id = fixture.get("fixture", {}).get("id")
        home_api = fixture.get("teams", {}).get("home", {}).get("name", "")
        away_api = fixture.get("teams", {}).get("away", {}).get("name", "")
        elapsed = fixture.get("fixture", {}).get("status", {}).get("elapsed") or 0
        league_name = fixture.get("league", {}).get("name", "")

        goals_home = fixture.get("goals", {}).get("home") if fixture.get("goals", {}).get("home") is not None else 0
        goals_away = fixture.get("goals", {}).get("away") if fixture.get("goals", {}).get("away") is not None else 0
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

        p_over25 = safe_float(row_dict.get(col_over25, 0)) or 0.0
        p_over15ht = safe_float(row_dict.get(col_over15ht, 0)) or 0.0
        p_over15ft = safe_float(row_dict.get(col_over15ft, 0)) or 0.0
        p_btts = safe_float(row_dict.get(col_btts, 0)) or 0.0
        p_corners85 = safe_float(row_dict.get(col_corners85, 0)) or 0.0
        p_corners95 = safe_float(row_dict.get(col_corners95, 0)) or 0.0

        if 0 < p_over25 <= 1.0: p_over25 *= 100
        if 0 < p_over15ht <= 1.0: p_over15ht *= 100
        if 0 < p_over15ft <= 1.0: p_over15ft *= 100
        if 0 < p_btts <= 1.0: p_btts *= 100
        if 0 < p_corners85 <= 1.0: p_corners85 *= 100
        if 0 < p_corners95 <= 1.0: p_corners95 *= 100

        p_corners = max(p_corners85, p_corners95)

        alerta_gatilho = None
        mercado_alerta = ""
        prob_alerta = 0.0
        stake_rec = "1.0u"
        metodo_id = ""
        exige_stats_corners = False

        # M1: Gol Limite HT (18'-32' min, 0x0, P_Over15HT >= 80%)
        if p_over15ht >= 80.0 and 18 <= elapsed <= 32 and total_gols == 0:
            alerta_gatilho = "📌 MÉTODO 1: GOL LIMITE HT (Over 0.5 HT)"
            mercado_alerta = "Over 0.5 HT"
            prob_alerta = p_over15ht
            stake_rec = "1.5u" if p_over15ht >= 90 else "1.0u"
            metodo_id = "M1_OVER05_HT"

        # M2: Over 1.5 FT (15'-42' min, total_gols <= 1, P_Over25 >= 80%)
        elif p_over25 >= 80.0 and 15 <= elapsed <= 42 and total_gols <= 1:
            alerta_gatilho = "📌 MÉTODO 2: OVER 1.5 FT LIVE"
            mercado_alerta = "Over 1.5 FT"
            prob_alerta = p_over25
            stake_rec = "1.5u" if p_over25 >= 90 else "1.0u"
            metodo_id = "M2_OVER15_FT"

        # M3: Over Limite 70+ (Minuto >= 68)
        elif (p_over25 >= 80.0 or p_over15ft >= 80.0) and elapsed >= 68:
            alerta_gatilho = "📌 MÉTODO 3: OVER LIMITE 70+ (LATE GOAL)"
            mercado_alerta = f"Over Limite FT (Placar Atual: {goals_home}x{goals_away})"
            prob_alerta = max(p_over25, p_over15ft)
            stake_rec = "1.5u" if prob_alerta >= 90 else "1.0u"
            metodo_id = "M3_OVER_LIMITE_70"

        # M4: Ambas Marcam Live (18'-32' min, 0x0, BTTS >= 80%)
        elif p_btts >= 80.0 and 18 <= elapsed <= 32 and total_gols == 0:
            alerta_gatilho = "📌 MÉTODO 4: AMBAS MARCAM LIVE (BTTS YES)"
            mercado_alerta = "Ambas Marcam (Sim)"
            prob_alerta = p_btts
            stake_rec = "1.5u" if p_btts >= 90 else "1.0u"
            metodo_id = "M4_BTTS_YES"

        # M5: Escanteios Live (28'-33' min)
        elif p_corners >= 70.0 and 28 <= elapsed <= 33:
            alerta_gatilho = "📌 MÉTODO 5: CANTS / ESCANTEIOS LIVE"
            mercado_alerta = "Over Escanteios HT"
            prob_alerta = p_corners
            stake_rec = "1.0u"
            metodo_id = "M5_CORNERS_HT"
            exige_stats_corners = True

        chave_alerta = f"{fix_id}_{metodo_id}"

        if alerta_gatilho and chave_alerta not in historico_alertas:
            stats = obter_estatisticas_live(fix_id, headers, cache)
            total_corners = stats["home_corners"] + stats["away_corners"]

            if exige_stats_corners and total_corners > 2:
                continue

            chutes_home = stats["home_shots_on_target"]
            chutes_away = stats["away_shots_on_target"]
            chutes_totais = chutes_home + chutes_away
            reds_home = stats["home_red_cards"]
            reds_away = stats["away_red_cards"]

            fair_odd = 100.0 / prob_alerta if prob_alerta > 0 else 1.25
            print(f"🎯 Aprovado: {home_api} {goals_home}x{goals_away} {away_api} | {mercado_alerta} | Prob.: {prob_alerta:.0f}%")

            mensagem = (
                f"🎯 *ALERTA LIVE EV+ FUTBET*\n"
                f"{alerta_gatilho}\n\n"
                f"⚽ *{home_api} {goals_home} x {goals_away} {away_api}*\n"
                f"🏆 *Liga:* {league_name}\n"
                f"⏱️ *Tempo:* {elapsed}' min\n\n"
                f"📊 *Estatísticas em Tempo Real:*\n"
                f"🎯 *Chutes no Gol:* {chutes_home} - {chutes_away} (Total: {chutes_totais})\n"
                f"🚩 *Escanteios:* {total_corners} ({stats['home_corners']} - {stats['away_corners']})\n"
                f"🛑 *Cartões Vermelhos:* {reds_home} (Casa) | {reds_away} (Fora)\n"
                f"📈 *Posse de Bola:* {stats['home_possession']} - {stats['away_possession']}\n\n"
                f"📌 *Mercado:* {mercado_alerta}\n"
                f"📈 *Probabilidade Base:* {prob_alerta:.0f}%\n"
                f"📐 *Odd Justa Estimada:* @{fair_odd:.2f}\n"
                f"🛡️ *Stake Recomendada:* {stake_rec}\n\n"
                f"🔗 https://www.bet365.bet.br/#/AX/\n"
            )

            if enviar_telegram(mensagem):
                historico_alertas[chave_alerta] = {
                    "status": "PENDENTE",
                    "fixture_id": fix_id,
                    "metodo_id": metodo_id,
                    "home": home_api,
                    "away": away_api,
                    "league": league_name,
                    "mercado": mercado_alerta,
                    "gols_no_alerta": total_gols,
                    "data_alerta": agora.strftime("%Y-%m-%d"),
                    "horario_alerta": agora.strftime("%H:%M"),
                }
                salvar_historico_alertas(historico_alertas)
                alertas_enviados += 1

    # Audita partidas pendentes
    auditar_apostas_pendentes(headers, cache, historico_alertas, partidas_live)

    # Verifica e envia relatório de fechamento diário (às 23:50 BRT)
    enviar_relatorio_fechamento_diario(historico_alertas)

    print("==============================================")
    print("📊 RESUMO DA EXECUÇÃO")
    print(f"Requests nesta execução: {requests_made_this_run}")
    print(f"Requests hoje: {cache.get('quota', {}).get('requests_today', 0)}/{MAX_DAILY_REQUESTS}")
    print(f"Saldo restante da API: {cache.get('quota', {}).get('last_api_remaining', 100)}")
    print(f"Jogos encontrados na base: {jogos_na_base}")
    print(f"Alertas enviados: {alertas_enviados}")
    print("==============================================")


if __name__ == "__main__":
    main()

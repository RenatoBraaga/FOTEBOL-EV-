import json
import os
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import pandas as pd
import requests


API_KEY = os.getenv("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CACHE_FILE = "cache_data.json"
LOG_ALERTAS_FILE = "alertas_enviados.json"

MAX_DAILY_REQUESTS = 80
MIN_API_REMAINING_SAFETY = 3

POSSIVEIS_CSVS = [
    "agenda_jogos_ev_positiva.csv.csv",
    "jogos_filtrados_notebooklm_v4.csv",
    "jogos_filtrados_notebooklm_sem_branco.csv",
    "agenda_jogos_ev_positiva.csv",
]

CSV_FILE = next(
    (caminho for caminho in POSSIVEIS_CSVS if os.path.exists(caminho)),
    "jogos_filtrados_notebooklm_v4.csv",
)

ODD_AUDITADA = 1.75
ODD_ALVO = 1.80
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

        for chave in [
            "finished_fixtures",
            "stats_cache",
            "live_fixtures_cache",
        ]:
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

        if "ratelimit" in nome_lower and "remaining" in nome_lower:
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
            f"🛑 Limite diário atingido: "
            f"{requisicoes_hoje}/{MAX_DAILY_REQUESTS}. "
            f"Chamada bloqueada: {url}"
        )
        return None

    if saldo_api <= MIN_API_REMAINING_SAFETY:
        print(
            f"🛑 Saldo crítico da API: {saldo_api} restantes. "
            f"Chamada bloqueada: {url}"
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
            print(
                f"⚠️ Resposta HTTP {resposta.status_code} "
                f"para a URL: {url}"
            )
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
            return {
                str(chave): {"status": "PENDENTE"}
                for chave in dados
            }

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
        print("⚠️ Token ou Chat ID do Telegram não configurado.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        resposta = requests.post(
            url,
            json=payload,
            timeout=10,
        )

        if resposta.status_code == 200:
            print("✅ Mensagem enviada ao Telegram.")
            return True

        print(
            f"❌ Erro no Telegram "
            f"({resposta.status_code}): {resposta.text}"
        )
        return False

    except requests.RequestException as erro:
        print(f"❌ Falha ao conectar ao Telegram: {erro}")
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
                horario_cache = datetime.strptime(
                    timestamp,
                    "%Y-%m-%d %H:%M:%S",
                )

                minutos = (
                    agora.replace(tzinfo=None) - horario_cache
                ).total_seconds() / 60

                if 0 <= minutos <= 15:
                    print(
                        f"⚡ Cache de estatísticas usado para "
                        f"o jogo {fixture_id}."
                    )
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

    url = (
        "https://v3.football.api-sports.io/"
        f"fixtures/statistics?fixture={fixture_id}"
    )

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

    dados_fixture = fixture.get("fixture", {})
    dados_times = fixture.get("teams", {})
    dados_gols = fixture.get("goals", {})
    dados_liga = fixture.get("league", {})

    if not all(
        isinstance(item, dict)
        for item in [
            dados_fixture,
            dados_times,
            dados_gols,
            dados_liga,
        ]
    ):
        return None

    casa = dados_times.get("home")
    fora = dados_times.get("away")

    if not isinstance(casa, dict) or not isinstance(fora, dict):
        return None

    try:
        fixture_id = int(dados_fixture.get("id"))
    except (TypeError, ValueError):
        return None

    status = dados_fixture.get("status", {})
    if not isinstance(status, dict):
        status = {}

    gols_casa = safe_int(dados_gols.get("home"))
    gols_fora = safe_int(dados_gols.get("away"))

    return {
        "id": fixture_id,
        "status": status.get("short", ""),
        "elapsed": safe_int(status.get("elapsed")),
        "home": casa.get("name", "Mandante"),
        "away": fora.get("name", "Visitante"),
        "league": dados_liga.get("name", "Liga não informada"),
        "goals_home": gols_casa,
        "goals_away": gols_fora,
        "total_goals": gols_casa + gols_fora,
    }


def auditar_apostas_pendentes(
    partidas_live,
    historico_alertas,
    headers,
    cache,
):
    if not historico_alertas:
        return

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
            print(
                f"⚡ Resultado em cache usado para o jogo "
                f"{fixture_id}."
            )
            live_data = finished_fixtures[fixture_str]

        if not live_data:
            print(
                f"🔍 Consultando status do jogo pendente "
                f"{fixture_id}."
            )

            url = (
                "https://v3.football.api-sports.io/"
                f"fixtures?id={fixture_id}"
            )

            resposta = fazer_requisicao_api(url, headers, cache)

            if resposta and isinstance(
                resposta.get("response"),
                list,
            ) and resposta["response"]:
                dados = extrair_dados_fixture(
                    resposta["response"][0]
                )

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
            elif status in ["HT", "2H", "FT", "AET", "PEN"] \
                    or elapsed > 45:
                novo_status = "RED"

        elif metodo_id in [
            "M2_OVER15_FT_1H",
            "M2_OVER15_FT_2H",
        ]:
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
            stats = obter_estatisticas_live(
                fixture_id,
                headers,
                cache,
            )

            total_corners = (
                safe_int(stats.get("home_corners")) +
                safe_int(stats.get("away_corners"))
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

        emoji = (
            "🟢 GREEN CONFIRMADO!"
            if novo_status == "GREEN"
            else "🔴 RED CONFIRMADO"
        )

        lucro = "+0.75u" if novo_status == "GREEN" else "-1.00u"

        mensagem = (
            f"{emoji}\n"
            f"⚽ *{home} {gols_casa} x {gols_fora} {away}*\n"
            f"📌 *Mercado:* {mercado}\n"
            f"💵 *Resultado:* {novo_status} ({lucro})"
        )

        enviar_telegram(mensagem)

    if houve_alteracao:
        cache["finished_fixtures"] = finished_fixtures
        salvar_cache(cache)
        salvar_historico_alertas(historico_alertas)


def enviar_relatorio_fechamento_diario(
    historico_alertas,
    forcar=False,
):
    agora = obter_horario_brt()
    hoje = agora.strftime("%Y-%m-%d")

    dentro_do_horario = (
        agora.hour == 23 and agora.minute >= 50
    )

    if not forcar and not dentro_do_horario:
        return

    chave_fechamento = f"FECHAMENTO_ENVIADO_{hoje}"

    if historico_alertas.get(chave_fechamento) and not forcar:
        return

    apostas = [
        item
        for item in historico_alertas.values()
        if isinstance(item, dict)
        and item.get("data_alerta") == hoje
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
            f"{indice}. {icone} [{horario}] "
            f"*{home} x {away}* - _{mercado}_"
        )

    resolvidos = greens + reds
    total_entradas = len(apostas)

    winrate = (
        greens / resolvidos * 100
        if resolvidos > 0
        else 0
    )

    profit_loss = greens * 0.75 - reds
    roi = (
        profit_loss / resolvidos * 100
        if resolvidos > 0
        else 0
    )

    sinal_pnl = "+" if profit_loss >= 0 else ""
    sinal_roi = "+" if roi >= 0 else ""

    mensagem = (
        "📊 *FECHAMENTO DIÁRIO DE AUDITORIA EV+*\n"
        f"📅 *Data:* {agora.strftime('%d/%m/%Y')}\n\n"
        f"🟢 *Greens:* {greens}\n"
        f"🔴 *Reds:* {reds}\n"
        f"⏳ *Pendentes:* {pendentes}\n"
        f"🎯 *Total de Entradas:* {total_entradas}\n\n"
        f"📈 *Winrate:* {winrate:.1f}%\n"
        f"💵 *Profit/Loss:* {sinal_pnl}{profit_loss:.2f}u\n"
        f"📊 *ROI:* {sinal_roi}{roi:.1f}%\n\n"
        "📋 *Apostas do Dia:*\n"
        + "\n".join(detalhes)
    )

    if enviar_telegram(mensagem):
        historico_alertas[chave_fechamento] = True
        salvar_historico_alertas(historico_alertas)
        print("✅ Fechamento diário enviado.")


def encontrar_coluna(df, opcoes):
    for coluna in opcoes:
        if coluna in df.columns:
            return coluna
    return None


def converter_percentual(valor):
    numero = safe_float(valor) or 0.0

    if 0 < numero <= 1:
        numero *= 100

    return numero


def encontrar_partida_na_base(df, col_home, col_away, home, away):
    if not col_home or not col_away:
        return None

    home_limpo = limpar_nome(home)
    away_limpo = limpar_nome(away)

    for _, linha in df.iterrows():
        home_csv = limpar_nome(str(linha.get(col_home, "")))
        away_csv = limpar_nome(str(linha.get(col_away, "")))

        home_ok = (
            similaridade(home_limpo, home_csv) > 0.65
            or home_limpo in home_csv
            or home_csv in home_limpo
        )

        away_ok = (
            similaridade(away_limpo, away_csv) > 0.65
            or away_limpo in away_csv
            or away_csv in away_limpo
        )

        if home_ok and away_ok:
            return linha.to_dict()

    return None


def main():
    global requests_made_this_run

    requests_made_this_run = 0
    agora = obter_horario_brt()

    print("==============================================")
    print("🚀 Robô EV+ FUTBET iniciado")
    print(f"📅 Horário BRT: {agora.strftime('%d/%m/%Y %H:%M:%S')}")

    if agora.hour < 8:
        print(
            "🛑 Execução bloqueada entre 00:00 e 07:59 BRT. "
            "Nenhuma chamada foi feita à API."
        )
        return

    if not API_KEY:
        print("❌ FOOTBALL_API_KEY não configurada.")
        return

    if not os.path.exists(CSV_FILE):
        print(f"❌ Arquivo CSV não encontrado: {CSV_FILE}")
        return

    try:
        df_base = pd.read_csv(CSV_FILE)
    except Exception as erro:
        print(f"❌ Erro ao ler CSV: {erro}")
        return

    print(
        f"📊 CSV carregado: {CSV_FILE} "
        f"({len(df_base)} partidas)"
    )

    cache = carregar_cache()
    quota = cache["quota"]

    print(
        f"📊 Quota: {quota['requests_today']}/"
        f"{MAX_DAILY_REQUESTS} | "
        f"Saldo API: {quota['last_api_remaining']}"
    )

    historico = carregar_historico_alertas()

    headers = {
        "x-apisports-key": API_KEY,
        "x-rapidapi-host": "v3.football.api-sports.io",
    }

    col_home = encontrar_coluna(
        df_base,
        ["Time_Casa", "Home Team"],
    )

    col_away = encontrar_coluna(
        df_base,
        ["Time_Fora", "Away Team"],
    )

    col_over25 = encontrar_coluna(
        df_base,
        ["Over25_Pct", "Over25 Average"],
    )

    col_over15ht = encontrar_coluna(
        df_base,
        ["Over15_HT_Pct", "Over15 FHG HT Average"],
    )

    col_over15ft = encontrar_coluna(
        df_base,
        ["Over15_FT_Pct", "Over15 Average"],
    )

    col_btts = encontrar_coluna(
        df_base,
        ["BTTS_Pct", "BTTS Average"],
    )

    col_over05_2hg = encontrar_coluna(
        df_base,
        ["Over05_2HG_Pct", "Over05 2HG Average"],
    )

    col_corners85 = encontrar_coluna(
        df_base,
        ["Average Over 8.5 Corners", "Over85_Corners_Pct"],
    )

    col_corners95 = encontrar_coluna(
        df_base,
        ["Average Over 9.5 Corners", "Over95_Corners_Pct"],
    )

    url_live = (
        "https://v3.football.api-sports.io/"
        "fixtures?live=all"
    )

    resposta_live = fazer_requisicao_api(
        url_live,
        headers,
        cache,
    )

    partidas_live = []

    if resposta_live and isinstance(
        resposta_live.get("response"),
        list,
    ):
        partidas_live = resposta_live["response"]

        cache["live_fixtures_cache"] = {
            "timestamp": agora.strftime("%Y-%m-%d %H:%M:%S"),
            "data": partidas_live,
        }

        salvar_cache(cache)

    else:
        cache_live = cache.get("live_fixtures_cache", {})
        partidas_cache = cache_live.get("data", [])

        if isinstance(partidas_cache, list):
            partidas_live = partidas_cache
            print("ℹ️ Dados live recuperados do cache.")

    print(
        f"📡 Partidas ao vivo encontradas: "
        f"{len(partidas_live)}"
    )

    auditar_apostas_pendentes(
        partidas_live,
        historico,
        headers,
        cache,
    )

    if not partidas_live:
        print("ℹ️ Nenhuma partida ao vivo.")
        enviar_relatorio_fechamento_diario(historico)

        print(
            f"📊 Requests nesta execução: "
            f"{requests_made_this_run}"
        )
        return

    colunas_percentuais = [
        col_over25,
        col_over15ht,
        col_over15ft,
        col_btts,
        col_over05_2hg,
        col_corners85,
        col_corners95,
    ]

    data_alerta = agora.strftime("%Y-%m-%d")
    horario_alerta = agora.strftime("%H:%M:%S")

    jogos_na_base = 0
    alertas_enviados = 0

    for partida in partidas_live:
        dados = extrair_dados_fixture(partida)

        if not dados:
            continue

        if not e_liga_elite(dados["league"]):
            continue

        linha = encontrar_partida_na_base(
            df_base,
            col_home,
            col_away,
            dados["home"],
            dados["away"],
        )

        if not linha:
            continue

        jogos_na_base += 1

        percentuais = {}

        for coluna in colunas_percentuais:
            percentuais[coluna] = converter_percentual(
                linha.get(coluna, 0) if coluna else 0
            )

        p_over25 = percentuais.get(col_over25, 0)
        p_over15ht = percentuais.get(col_over15ht, 0)
        p_over15ft = percentuais.get(col_over15ft, 0)
        p_btts = percentuais.get(col_btts, 0)
        p_over05_2hg = percentuais.get(col_over05_2hg, 0)
        p_corners85 = percentuais.get(col_corners85, 0)
        p_corners95 = percentuais.get(col_corners95, 0)

        p_corners = max(p_corners85, p_corners95)

        elapsed = dados["elapsed"]
        total_gols = dados["total_goals"]

        alerta_gatilho = None
        mercado = ""
        probabilidade = 0.0
        stake = "1.0u"
        metodo_id = ""
        exige_stats_corners = False

        if (
            p_over15ht >= 80
            and 15 <= elapsed <= 35
            and total_gols == 0
        ):
            alerta_gatilho = (
                "📌 MÉTODO 1: GOL LIMITE HT "
                "(Over 0.5 HT)"
            )
            mercado = "Over 0.5 HT"
            probabilidade = p_over15ht
            stake = "1.5u" if p_over15ht >= 90 else "1.0u"
            metodo_id = "M1_OVER05_HT"

        elif (
            p_over25 >= 80
            and 15 <= elapsed <= 60
            and total_gols == 0
        ):
            if elapsed <= 45:
                alerta_gatilho = (
                    "📌 MÉTODO 2: OVER 1.5 FT LIVE "
                    "(1º Tempo)"
                )
                metodo_id = "M2_OVER15_FT_1H"
            else:
                alerta_gatilho = (
                    "📌 MÉTODO 2: OVER 1.5 FT LIVE "
                    "(2º Tempo)"
                )
                metodo_id = "M2_OVER15_FT_2H"

            mercado = "Over 1.5 FT"
            probabilidade = p_over25
            stake = "1.5u" if p_over25 >= 90 else "1.0u"

        elif (
            p_btts >= 80
            and 0 <= elapsed <= 35
            and total_gols == 0
        ):
            alerta_gatilho = (
                "📌 MÉTODO 4: AMBAS MARCAM LIVE "
                "(BTTS YES)"
            )
            mercado = "Ambas Marcam (Sim)"
            probabilidade = p_btts
            stake = "1.5u" if p_btts >= 90 else "1.0u"
            metodo_id = "M4_BTTS_YES"

        elif p_corners >= 70 and 19 <= elapsed <= 35:
            alerta_gatilho = (
                "📌 MÉTODO 5: ESCANTEIOS LIVE HT"
            )
            mercado = "Over 2.5 Escanteios HT"
            probabilidade = p_corners
            stake = "1.0u"
            metodo_id = "M5_CORNERS_HT"
            exige_stats_corners = True

        elif (
            (
                p_over05_2hg >= 80
                or p_over25 >= 80
                or p_over15ft >= 80
            )
            and 68 <= elapsed <= 80
        ):
            alerta_gatilho = (
                "📌 MÉTODO 3: OVER LIMITE 70+ "
                "(LATE GOAL)"
            )
            mercado = (
                "Over Limite FT "
                f"(Placar Atual: {dados['goals_home']}x"
                f"{dados['goals_away']})"
            )
            probabilidade = max(
                p_over05_2hg,
                p_over25,
                p_over15ft,
            )
            stake = "1.5u" if probabilidade >= 90 else "1.0u"
            metodo_id = "M3_OVER_LIMITE_70"

        if not alerta_gatilho or not metodo_id:
            continue

        chave_alerta = f"{dados['id']}_{metodo_id}"

        if chave_alerta in historico:
            continue

        stats = obter_estatisticas_live(
            dados["id"],
            headers,
            cache,
        )

        corners_home = safe_int(
            stats.get("home_corners")
        )
        corners_away = safe_int(
            stats.get("away_corners")
        )
        total_corners = corners_home + corners_away

        if exige_stats_corners and total_corners > 2:
            continue

        shots_home = safe_int(
            stats.get("home_shots_on_target")
        )
        shots_away = safe_int(
            stats.get("away_shots_on_target")
        )

        reds_home = safe_int(
            stats.get("home_red_cards")
        )
        reds_away = safe_int(
            stats.get("away_red_cards")
        )

        total_shots = shots_home + shots_away

        odd_justa = (
            100 / probabilidade
            if probabilidade > 0
            else 1.25
        )

        ev = (
            probabilidade / 100 * ODD_ALVO
        ) - 1

        ev_percentual = ev * 100

        print(
            f"🎯 Aprovado: {dados['home']} "
            f"{dados['goals_home']}x{dados['goals_away']} "
            f"{dados['away']} | {mercado} | "
            f"Prob.: {probabilidade:.0f}% | "
            f"EV: {ev_percentual:+.1f}%"
        )

        mensagem = (
            "🎯 *ALERTA LIVE EV+ FUTBET*\n"
            f"{alerta_gatilho}\n\n"
            f"⚽ *{dados['home']} "
            f"{dados['goals_home']} x "
            f"{dados['goals_away']} "
            f"{dados['away']}*\n"
            f"🏆 *Liga:* {dados['league']}\n"
            f"⏱️ *Tempo:* {elapsed}'\n\n"
            "📊 *Estatísticas em Tempo Real:*\n"
            f"🎯 *Chutes no Gol:* "
            f"{shots_home} - {shots_away} "
            f"(Total: {total_shots})\n"
            f"🚩 *Escanteios:* {total_corners} "
            f"({corners_home} - {corners_away})\n"
            f"🛑 *Cartões Vermelhos:* "
            f"{reds_home} Casa | {reds_away} Fora\n"
            f"📈 *Posse:* "
            f"{stats.get('home_possession', '0%')} - "
            f"{stats.get('away_possession', '0%')}\n\n"
            f"📌 *Mercado:* {mercado}\n"
            f"📈 *Probabilidade Base:* "
            f"{probabilidade:.0f}%\n"
            f"🎯 *Odd Alvo:* @{ODD_ALVO:.2f}\n"
            f"📐 *Odd Justa:* @{odd_justa:.2f}\n"
            f"💵 *EV Estimado:* "
            f"{ev_percentual:+.1f}%\n"
            f"🛡️ *Stake:* {stake}\n\n"
            "🔗 https://www.bet365.bet.br/#/AX/"
        )

        if not enviar_telegram(mensagem):
            continue

        historico[chave_alerta] = {
            "fixture_id": dados["id"],
            "metodo_id": metodo_id,
            "home": dados["home"],
            "away": dados["away"],
            "league": dados["league"],
            "mercado": mercado,
            "status": "PENDENTE",
            "gols_no_alerta": total_gols,
            "data_alerta": data_alerta,
            "horario_alerta": horario_alerta,
        }

        salvar_historico_alertas(historico)
        alertas_enviados += 1

    enviar_relatorio_fechamento_diario(historico)

    print("==============================================")
    print("📊 RESUMO DA EXECUÇÃO")
    print(f"Requests nesta execução: {requests_made_this_run}")
    print(
        f"Requests hoje: "
        f"{quota['requests_today']}/{MAX_DAILY_REQUESTS}"
    )
    print(
        f"Saldo restante da API: "
        f"{quota['last_api_remaining']}"
    )
    print(f"Jogos encontrados na base: {jogos_na_base}")
    print(f"Alertas enviados: {alertas_enviados}")
    print("==============================================")


if __name__ == "__main__":
    main()

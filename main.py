import os
import pandas as pd
import datetime
import requests
from dotenv import load_dotenv

# Carrega as variáveis de ambiente
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CSV_FILE_PATH = os.getenv("CSV_FILE_PATH", "jogos_filtrados_notebooklm_v4.csv")

def is_elite_league(league_name):
    """Filtra ligas de base, amadoras e sem liquidez."""
    if not isinstance(league_name, str):
        return False
    
    league_lower = league_name.lower()
    
    excluded_keywords = [
        'u19', 'u20', 'u21', 'u23', 'sub-19', 'sub-20', 'sub-23', 'youth', 'junior',
        'women', 'feminino', 'femenil', 'w', 'reserve', 'reserva', 'amateur',
        '3rd division', '4th division', '3. division', '4. division', 'tercera',
        '3 liga', 'kolmonen', 'regional'
    ]
    
    for kw in excluded_keywords:
        if kw in league_lower:
            return False
            
    return True

def is_mature_or_cup(row):
    """Garante apenas rodadas com dados estáveis (Game Week >= 4) ou jogos de Copa."""
    league = str(row.get('League', '')).lower()
    gw = row.get('Game Week', 0)
    
    if any(cup in league for cup in ['cup', 'copa', 'taça', 'taca', 'super cup']):
        return True
        
    try:
        return float(gw) >= 4
    except:
        return True

def parse_date_brt(date_str):
    """Converte a data/hora GMT do banco para o Horário de Brasília (BRT / UTC-3)."""
    try:
        dt_gmt = datetime.datetime.strptime(str(date_str), '%b %d %Y - %I:%M%p')
        dt_brt = dt_gmt - datetime.timedelta(hours=3)
        return dt_brt
    except Exception:
        return None

def calculate_ev_opportunities(df):
    """Executa os cálculos estatísticos e validações +EV do Engine Master."""
    opportunities = []

    for idx, row in df.iterrows():
        # Apenas jogos futuros
        status = str(row.get('Match Status', '')).lower()
        if 'incomplete' not in status:
            continue

        # Filtro de Ligas de Elite e Maturidade
        if not is_elite_league(row.get('League', '')):
            continue
        if not is_mature_or_cup(row):
            continue

        home = row.get('Home Team', 'Mandante')
        away = row.get('Away Team', 'Visitante')
        league = row.get('League', 'Liga')
        dt_brt = parse_date_brt(row.get('date_GMT', ''))
        
        if not dt_brt:
            continue

        time_str = dt_brt.strftime('%H:%M')
        date_str = dt_brt.strftime('%d/%m/%Y')

        match_ops = []

        # Mapeamento dos mercados suportados
        markets_to_check = [
            ('Over 1.5 Gols', 'Over15 Average', 'Odds_Over15', None),
            ('Over 2.5 Gols', 'Over25 Average', 'Odds_Over25', None),
            ('Over 3.5 Gols', 'Over35 Average', 'Odds_Over35', None),
            ('Over 0.5 HT', 'Over05 FHG HT Average', 'Odds_1st_Half_Over05', None),
            ('Over 1.5 HT', 'Over15 FHG HT Average', 'Odds_1st_Half_Over15', None),
            ('Ambas Marcam (BTTS Yes)', 'BTTS Average', 'Odds_BTTS_Yes', None),
            ('Gol Limite (Over 0.5 2H @1.75 Live)', 'Over05 2HG Average', None, 1.75),
            ('Over 8.5 Cantos', 'Average Over 8.5 Corners', 'Odds_Corners_Over85', None),
            ('Over 9.5 Cantos', 'Average Over 9.5 Corners', 'Odds_Corners_Over95', None),
            ('Over 10.5 Cantos', 'Average Over 10.5 Corners', 'Odds_Corners_Over105', None),
        ]

        for m_name, prob_col, odd_col, fixed_odd in markets_to_check:
            prob_raw = row.get(prob_col, None)
            
            if pd.isna(prob_raw) or prob_raw is None:
                continue

            prob = float(prob_raw) / 100.0

            # Trava 1: Probabilidade Mínima >= 70%
            if prob < 0.70:
                continue

            # Identificação da Odd
            if fixed_odd:
                odd = float(fixed_odd)
            else:
                odd_raw = row.get(odd_col, None)
                if pd.isna(odd_raw) or odd_raw is None:
                    continue
                odd = float(odd_raw)

            # Trava 2: EV% = (Probabilidade * Odd) - 1
            ev = (prob * odd) - 1.0

            # Trava 3: Apenas EV% > +5.0%
            if ev > 0.05:
                fair_odd = 1.0 / prob if prob > 0 else 0
                
                # Gestão de Banca Dinâmica
                if ev <= 0.12:
                    stake = "1.0u (Padrão)"
                elif ev <= 0.20:
                    stake = "1.5u (Alta)"
                else:
                    stake = "2.0u (Máxima)"

                match_ops.append({
                    'date_brt': date_str,
                    'time_brt': time_str,
                    'dt_obj': dt_brt,
                    'league': league,
                    'home': home,
                    'away': away,
                    'market': m_name,
                    'prob': prob * 100.0,
                    'fair_odd': round(fair_odd, 2),
                    'odd': odd,
                    'ev': ev * 100.0,
                    'stake': stake
                })

        # Trava 4: Limite de Exposição (No máximo 2 apostas por jogo)
        if match_ops:
            match_ops = sorted(match_ops, key=lambda x: (x['prob'], x['ev']), reverse=True)
            opportunities.extend(match_ops[:2])

    # Ordenação cronológica por Horário de Brasília
    opportunities = sorted(opportunities, key=lambda x: x['dt_obj'])
    return opportunities

def send_telegram_alert(op):
    """Envia a notificação formatada para o Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ [AVISO] Telegram não configurado no .env")
        return False

    message = (
        f"🚨 *SINAL +EV DETECTADO - ENGINE MASTER* 🚨\n\n"
        f"⚽ *{op['home']} vs {op['away']}*\n"
        f"🏆 *Liga:* {op['league']}\n"
        f"📅 *Data/Hora:* {op['date_brt']} às {op['time_brt']} (BRT)\n\n"
        f"🎯 *Mercado Recomendado:* `{op['market']}`\n"
        f"📊 *Probabilidade Real:* `{op['prob']:.1f}%`\n"
        f"⚖️ *Odd Justa:* `@{op['fair_odd']:.2f}`\n"
        f"🔥 *Odd Oferecida:* `@{op['odd']:.2f}`\n"
        f"📈 *Valor Esperado (+EV):* `+{op['ev']:.1f}%`\n\n"
        f"💰 *Gestão Recomendada:* `{op['stake']}`\n"
        f"🛡️ *Filtro:* Select Elite (P ≥ 70% | EV > +5%)\n"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Erro na conexão com Telegram: {e}")
        return False

def main():
    print("🚀 Executando Varredura Quantitativa EV+...")
    
    if not os.path.exists(CSV_FILE_PATH):
        print(f"❌ Erro: O arquivo CSV '{CSV_FILE_PATH}' não foi encontrado.")
        return

    df = pd.read_csv(CSV_FILE_PATH)
    print(f"📊 Banco de dados carregado ({len(df)} jogos).")

    opportunities = calculate_ev_opportunities(df)
    print(f"🎯 Oportunidades +EV Encontradas: {len(opportunities)}\n")

    for op in opportunities:
        print(f"[{op['time_brt']}] {op['home']} x {op['away']} | {op['market']} | Prob: {op['prob']:.0f}% | Odd: {op['odd']} | EV: +{op['ev']:.1f}%")
        # Para ativar os disparos reais no Telegram, descomente a linha abaixo:
        # send_telegram_alert(op)

if __name__ == "__main__":
    main()

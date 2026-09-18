import os
import requests
import pandas as pd
from google import genai

# Variável da API Key do Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def gerar_analise_ia(home_team, away_team, league_name, elapsed, goals_home, goals_away, mercado, prob, fair_odd, stats):
    """
    Solicita ao Gemini uma análise tática e quantitativa em 2 a 3 frases rápidas.
    """
    if not GEMINI_API_KEY:
        return ""
    
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            f"Você é um analista sênior de apostas de valor (+EV).\n"
            f"O jogo {home_team} {goals_home}x{goals_away} {away_team} ({league_name}) está aos {elapsed} minutos.\n"
            f"O robô aprovou uma entrada no mercado '{mercado}' com probabilidade base de {prob:.0f}% e Odd Justa de @{fair_odd:.2f}.\n"
            f"Estatísticas live: Chutes no gol: {stats['home_shots_on_target']}-{stats['away_shots_on_target']}, "
            f"Posse de bola: {stats['home_possession']}-{stats['away_possession']}.\n\n"
            f"Escreva um comentário tático e motivado pelo valor esperado em no máximo 3 frases curtas e diretas para o operador."
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return f"\n🧠 *Análise IA (Gemini):*\n_{response.text.strip()}_\n"
    except Exception as e:
        print(f"⚠️ Não foi possível obter análise do Gemini: {e}")
        return ""
E no momento de montar a mensagem para o Telegram, basta incluir a variável da análise:
# Dentro do loop de envio do alerta:
analise_ia = gerar_analise_ia(
    home_api, away_api, league_name, elapsed, 
    goals_home, goals_away, mercado_alerta, prob_alerta, fair_odd, stats
)

mensagem = (
    f"🎯 *ALERTA LIVE EV+ FUTBET*\n"
    f"{alerta_gatilho}\n\n"
    f"⚽ *{home_api} {goals_home} x {goals_away} {away_api}*\n"
    f"🏆 *Liga:* {league_name}\n"
    f"⏱️ *Tempo:* {elapsed}' min\n\n"
    f"📊 *Estatísticas em Tempo Real:*\n"
    f"🎯 *Chutes no Gol:* {chutes_home} - {chutes_away}\n"
    f"📈 *Posse de Bola:* {stats['home_possession']} - {stats['away_possession']}\n\n"
    f"📌 *Mercado:* {mercado_alerta}\n"
    f"📈 *Probabilidade Base:* {prob_alerta:.0f}%\n"
    f"📐 *Odd Justa Estimada:* @{fair_odd:.2f}\n"
    f"🛡️ *Stake Recomendada:* {stake_rec}\n"
    f"{analise_ia}\n"
    f"🔗 https://www.bet365.bet.br/#/AX/\n"
)
enviar_telegram(mensagem)

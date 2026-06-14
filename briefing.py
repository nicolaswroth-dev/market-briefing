"""
Morning Market Briefing — Nicolas Roth 
Watchlist: TPG US, ECO US, ALPHA.AT, TRMD, FRO, DOV.MI, INTRUM.ST, TEN
Runs daily at 07:50 CET via GitHub Actions
Stack: yfinance (prices) + Perplexity API (news) + Claude API (synthesis) + Gmail SMTP (delivery)
"""

import os
import json
import smtplib
import datetime
import yfinance as yf
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from anthropic import Anthropic

# ── Config ────────────────────────────────────────────────────────────────────

WATCHLIST = {
    "TPG":       {"name": "TPG Inc.",                     "exchange": "NASDAQ"},
    "ECO":       {"name": "Okeanis Eco Tankers Corp.",    "exchange": "NYSE"},
    "ALPHA.AT":  {"name": "Alpha Bank S.A.",              "exchange": "Athens (ATHEX)"},
    "TRMD":      {"name": "TORM plc",                     "exchange": "Nasdaq US"},
    "FRO":       {"name": "Frontline plc",                "exchange": "NYSE"},
    "DOV.MI":    {"name": "doValue S.p.A.",               "exchange": "Borsa Italiana"},
    "INTRUM.ST": {"name": "Intrum AB",                    "exchange": "Nasdaq Stockholm"},
    "TEN":       {"name": "Tsakos Energy Navigation Ltd.","exchange": "NYSE"},
}

RECIPIENT_EMAIL = "Nicolas.w.roth@gmail.com"
SENDER_EMAIL    = os.environ["GMAIL_ADDRESS"]   # set in GitHub Secrets
GMAIL_APP_PASS  = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
PERPLEXITY_KEY  = os.environ["PERPLEXITY_API_KEY"]

# ── 1. Fetch Prices via yfinance ───────────────────────────────────────────────

def fetch_prices(tickers: list[str]) -> dict:
    data = {}
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            hist  = stock.history(period="5d")
            info  = stock.fast_info

            if len(hist) < 2:
                continue

            close_today = round(hist["Close"].iloc[-1], 2)
            close_prev  = round(hist["Close"].iloc[-2], 2)
            pct_change  = round((close_today - close_prev) / close_prev * 100, 2)
            volume      = int(hist["Volume"].iloc[-1])
            avg_volume  = int(hist["Volume"].mean())
            vol_ratio   = round(volume / avg_volume, 2)

            try:
                week52_high = round(info.year_high, 2)
                week52_low  = round(info.year_low,  2)
                pct_from_high = round((close_today - week52_high) / week52_high * 100, 1)
            except Exception:
                week52_high = week52_low = pct_from_high = "N/A"

            try:
                currency = info.currency
            except Exception:
                currency = "USD"

            data[ticker] = {
                "close":          close_today,
                "prev_close":     close_prev,
                "currency":       currency,
                "pct_change":     pct_change,
                "volume":         volume,
                "avg_volume":     avg_volume,
                "vol_ratio":      vol_ratio,
                "week52_high":    week52_high,
                "week52_low":     week52_low,
                "pct_from_high":  pct_from_high,
            }
        except Exception as e:
            data[ticker] = {"error": str(e)}

    return data


# ── 2. Fetch News via Perplexity API ──────────────────────────────────────────

def fetch_news_perplexity(ticker: str, company_name: str) -> str:
    url     = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_KEY}",
        "Content-Type":  "application/json",
    }
    prompt = (
        f"List the most important news items published in the last 24 hours about {company_name} ({ticker}). "
        f"Focus on: earnings, analyst upgrades/downgrades, M&A, regulatory events, management changes, macro events affecting the stock. "
        f"For each item: date, source, one-sentence summary. If nothing material, say 'No material news in last 24h'."
    )
    payload = {
        "model":    "sonar",
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"News fetch error: {e}"


# ── 3. Synthesize with Claude API ─────────────────────────────────────────────

def synthesize_briefing(prices: dict, news: dict) -> str:
    client = Anthropic(api_key=ANTHROPIC_KEY)

    today = datetime.date.today().strftime("%A %d %B %Y")

    price_block = json.dumps(prices, indent=2)
    news_block  = "\n\n".join(
        [f"=== {ticker} ===\n{content}" for ticker, content in news.items()]
    )

    system_prompt = (
        "You are a senior equity analyst writing a daily pre-market briefing for a professional portfolio manager. "
        "Register: institutional, dense, no marketing language. "
        "Each ticker's price data includes its own 'currency' field (USD, EUR, SEK, etc.) — always display prices in their native currency, never convert. "
        "Format: (1) a brief narrative paragraph per stock integrating price action and news signal, "
        "(2) a clean HTML summary table at the end. "
        "Group the narrative thematically in this order: "
        "Private Equity (TPG), Greek Banks (Alpha Bank), Tanker Shipping (Okeanis/ECO, TORM, Frontline, Tsakos/TEN), "
        "Credit/NPL Servicers (doValue, Intrum). "
        "Flag any move >2% or material news event at the top under an ALERT line. "
        "Be declarative — verdicts, not descriptions. Use the correct local currency symbol/code per ticker."
    )

    user_prompt = f"""
Date: {today}

PRICE DATA (JSON):
{price_block}

NEWS (last 24h):
{news_block}

Produce the morning briefing. Structure:
1. ALERT line (if any move >2% or material news — else omit)
2. One narrative paragraph per stock (3-5 sentences: price action → volume signal → news context → implication)
3. HTML summary table with columns: Ticker | Name | Last Price | Chg% | Vol/Avg | 52W High | % from High | Key Signal
"""

    response = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 3000,
        system     = system_prompt,
        messages   = [{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text


# ── 4. Build HTML Email ───────────────────────────────────────────────────────

def build_email_html(briefing_text: str) -> str:
    today = datetime.date.today().strftime("%A %d %B %Y")

    # Wrap narrative paragraphs in styled divs, keep HTML table as-is
    # Split on the HTML table if present
    if "<table" in briefing_text:
        parts     = briefing_text.split("<table", 1)
        narrative = parts[0]
        table_raw = "<table" + parts[1]
    else:
        narrative = briefing_text
        table_raw = ""

    # Convert newlines to <p> tags in narrative
    narrative_html = "".join(
        f"<p>{line.strip()}</p>" for line in narrative.split("\n") if line.strip()
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body       {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: #f4f4f4; margin:0; padding:0; }}
  .container {{ max-width: 680px; margin: 30px auto; background: #ffffff; border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }}
  .header    {{ background: #003d4d; color: #ffffff; padding: 22px 30px; }}
  .header h1 {{ margin:0; font-size:18px; font-weight:600; letter-spacing:0.5px; }}
  .header p  {{ margin:4px 0 0; font-size:12px; color:#a8c8d0; }}
  .body      {{ padding: 24px 30px; }}
  .alert     {{ background:#fff3cd; border-left:4px solid #e6a817; padding:10px 14px;
                border-radius:3px; margin-bottom:18px; font-size:13px; font-weight:600; }}
  p          {{ font-size:13.5px; line-height:1.65; color:#2c2c2c; margin:0 0 12px; }}
  h3         {{ font-size:13px; text-transform:uppercase; letter-spacing:0.8px;
                color:#003d4d; border-bottom:1px solid #e0e0e0; padding-bottom:6px; margin:22px 0 12px; }}
  table      {{ width:100%; border-collapse:collapse; font-size:12.5px; margin-top:10px; }}
  th         {{ background:#003d4d; color:#ffffff; padding:8px 10px; text-align:left; font-weight:500; }}
  td         {{ padding:7px 10px; border-bottom:1px solid #eeeeee; color:#2c2c2c; }}
  tr:nth-child(even) td {{ background:#f9f9f9; }}
  .pos       {{ color:#1a7a3f; font-weight:600; }}

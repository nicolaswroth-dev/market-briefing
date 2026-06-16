"""
Morning Market Briefing — Nicolas Roth
Watchlist: TPG, ECO (Okeanis), ALPHA.AT, TRMD, FRO, DOV.MI, INTRUM.ST, TEN
v3: Added macro overnight block (futures, oil, rates, BDTI, macro news)
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
    "TPG":       {"name": "TPG Inc.",                      "exchange": "NASDAQ"},
    "ECO":       {"name": "Okeanis Eco Tankers Corp.",     "exchange": "NYSE"},
    "ALPHA.AT":  {"name": "Alpha Bank S.A.",               "exchange": "Athens (ATHEX)"},
    "TRMD":      {"name": "TORM plc",                      "exchange": "Nasdaq US"},
    "FRO":       {"name": "Frontline plc",                 "exchange": "NYSE"},
    "DOV.MI":    {"name": "doValue S.p.A.",                "exchange": "Borsa Italiana"},
    "INTRUM.ST": {"name": "Intrum AB",                     "exchange": "Nasdaq Stockholm"},
    "TEN":       {"name": "Tsakos Energy Navigation Ltd.", "exchange": "NYSE"},
}

# Macro instruments fetched via yfinance
MACRO_TICKERS = {
    "ES=F":  "S&P 500 Futures",
    "NQ=F":  "Nasdaq 100 Futures",
    "BZ=F":  "Brent Crude Oil",
    "CL=F":  "WTI Crude Oil",
    "^TNX":  "US 10Y Treasury Yield",
    "^EURONEXT_BDTI": "Baltic Dirty Tanker Index",  # fallback if unavailable
}

RECIPIENT_EMAIL = "Nicolas.w.roth@gmail.com"
SENDER_EMAIL    = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASS  = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
PERPLEXITY_KEY  = os.environ["PERPLEXITY_API_KEY"]

# ── 1. Fetch Macro Data ────────────────────────────────────────────────────────

def fetch_macro() -> dict:
    macro = {}

    # Equity futures + oil + rates via yfinance
    for ticker, label in [
        ("ES=F",  "S&P 500 Futures"),
        ("NQ=F",  "Nasdaq 100 Futures"),
        ("BZ=F",  "Brent Crude (USD/bbl)"),
        ("CL=F",  "WTI Crude (USD/bbl)"),
        ("^TNX",  "US 10Y Yield (%)"),
        ("^IRX",  "US 3M T-Bill (%)"),
        ("EURUSD=X", "EUR/USD"),
        ("SEKUSD=X", "SEK/USD"),
    ]:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) < 2:
                macro[label] = {"error": "no data"}
                continue
            last  = round(hist["Close"].iloc[-1], 4)
            prev  = round(hist["Close"].iloc[-2], 4)
            chg   = round((last - prev) / prev * 100, 2)
            macro[label] = {"last": last, "chg_pct": chg}
        except Exception as e:
            macro[label] = {"error": str(e)}

    return macro


def fetch_macro_news() -> str:
    """Single Perplexity call for overnight macro headlines."""
    url     = "https://api.perplexity.ai/chat/completions"
    headers = {"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"}
    prompt  = (
        "List the 5 most important macro/market headlines from the last 12 hours relevant to equity investors. "
        "Focus on: central bank statements (Fed, ECB), geopolitical events, commodity moves, major economic data releases, "
        "and any systemic risk events. Format: bullet points, one sentence each, with source and time if available."
    )
    payload = {"model": "sonar", "messages": [{"role": "user", "content": prompt}]}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Macro news fetch error: {e}"


# ── 2. Fetch Stock Prices ──────────────────────────────────────────────────────

def fetch_prices(tickers: list) -> dict:
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
                week52_high   = round(info.year_high, 2)
                week52_low    = round(info.year_low,  2)
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


# ── 3. Fetch Stock News ────────────────────────────────────────────────────────

def fetch_news_perplexity(ticker: str, company_name: str) -> str:
    url     = "https://api.perplexity.ai/chat/completions"
    headers = {"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"}
    prompt  = (
        f"List the most important news items published in the last 24 hours about {company_name} ({ticker}). "
        f"Focus on: earnings, analyst upgrades/downgrades, M&A, regulatory events, management changes, macro events affecting the stock. "
        f"For each item: date, source, one-sentence summary. If nothing material, say 'No material news in last 24h'."
    )
    payload = {"model": "sonar", "messages": [{"role": "user", "content": prompt}]}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"News fetch error: {e}"


# ── 4. Synthesize with Claude ──────────────────────────────────────────────────

def synthesize_briefing(macro: dict, macro_news: str, prices: dict, news: dict) -> str:
    client = Anthropic(api_key=ANTHROPIC_KEY)
    today  = datetime.date.today().strftime("%A %d %B %Y")

    system_prompt = (
        "You are a senior equity analyst writing a daily pre-market briefing for a professional portfolio manager. "
        "Register: institutional, dense, no marketing language. "
        "Each ticker's price data includes a 'currency' field — display prices in their native currency, never convert. "
        "Structure the briefing as follows:\n"
        "  (A) MACRO OVERNIGHT: 3-4 sentence synthesis of the macro backdrop — futures, oil, rates, key headlines. "
        "      Explicitly connect the macro to the watchlist where relevant "
        "      (e.g. Brent move → tanker names; EUR/USD → doValue/Intrum; 10Y yield → Alpha Bank). "
        "      Flag any macro move >1% or major event with ⚠️.\n"
        "  (B) ALERTS: flag any stock move >2% or material news — if none, omit this section entirely.\n"
        "  (C) STOCK NARRATIVES: one paragraph per stock (3-5 sentences: price action → volume signal → "
        "      news context → macro linkage → implication). "
        "      Group in this order: Private Equity (TPG) | Greek Banks (Alpha Bank) | "
        "      Tanker Shipping (ECO, TORM, Frontline, TEN) | Credit/NPL Servicers (doValue, Intrum).\n"
        "  (D) HTML SUMMARY TABLE at the very end.\n"
        "Be declarative — verdicts, not descriptions."
    )

    user_prompt = f"""Date: {today}

MACRO DATA (last close vs prior close):
{json.dumps(macro, indent=2)}

MACRO NEWS (last 12h):
{macro_news}

STOCK PRICE DATA:
{json.dumps(prices, indent=2)}

STOCK NEWS (last 24h):
{"".join([f"=== {t} ===\n{n}\n\n" for t, n in news.items()])}

Produce the morning briefing following the structure in your instructions.
HTML table columns: Ticker | Name | Last Price | Chg% | Vol/Avg | 52W High | % from High | Key Signal
"""

    response = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 3500,
        system     = system_prompt,
        messages   = [{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


# ── 5. Build HTML Email ────────────────────────────────────────────────────────

def build_email_html(briefing_text: str, macro: dict) -> str:
    today = datetime.date.today().strftime("%A %d %B %Y")

    # Build macro ticker bar
    def fmt(label, d):
        if "error" in d:
            return f"<span class='macro-item'>{label}: N/A</span>"
        chg   = d["chg_pct"]
        color = "#1a7a3f" if chg >= 0 else "#c0392b"
        arrow = "▲" if chg >= 0 else "▼"
        return (f"<span class='macro-item'>{label}: "
                f"<b>{d['last']}</b> "
                f"<span style='color:{color}'>{arrow}{abs(chg)}%</span></span>")

    macro_bar_items = [
        fmt("S&P Fut",    macro.get("S&P 500 Futures", {"error": "N/A"})),
        fmt("NQ Fut",     macro.get("Nasdaq 100 Futures", {"error": "N/A"})),
        fmt("Brent",      macro.get("Brent Crude (USD/bbl)", {"error": "N/A"})),
        fmt("WTI",        macro.get("WTI Crude (USD/bbl)", {"error": "N/A"})),
        fmt("US 10Y",     macro.get("US 10Y Yield (%)", {"error": "N/A"})),
        fmt("EUR/USD",    macro.get("EUR/USD", {"error": "N/A"})),
    ]
    macro_bar = "  &nbsp;|&nbsp;  ".join(macro_bar_items)

    if "<table" in briefing_text:
        parts     = briefing_text.split("<table", 1)
        narrative = parts[0]
        table_raw = "<table" + parts[1]
    else:
        narrative = briefing_text
        table_raw = ""

    narrative_html = "".join(
        f"<p>{line.strip()}</p>" for line in narrative.split("\n") if line.strip()
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body        {{ font-family: 'Helvetica Neue', Arial, sans-serif; background:#f4f4f4; margin:0; padding:0; }}
  .container  {{ max-width:700px; margin:30px auto; background:#fff; border-radius:6px;
                 box-shadow:0 2px 8px rgba(0,0,0,0.08); overflow:hidden; }}
  .header     {{ background:#003d4d; color:#fff; padding:20px 30px 14px; }}
  .header h1  {{ margin:0; font-size:17px; font-weight:600; letter-spacing:0.5px; }}
  .header p   {{ margin:4px 0 0; font-size:11px; color:#a8c8d0; }}
  .macro-bar  {{ background:#012e3a; padding:10px 30px; font-size:11.5px; color:#d0e8ee;
                 border-bottom:1px solid #024055; line-height:2; }}
  .macro-item {{ margin-right:4px; }}
  .body       {{ padding:22px 30px; }}
  p           {{ font-size:13.5px; line-height:1.65; color:#2c2c2c; margin:0 0 11px; }}
  h3          {{ font-size:12px; text-transform:uppercase; letter-spacing:0.9px;
                 color:#003d4d; border-bottom:1px solid #e0e0e0; padding-bottom:5px; margin:20px 0 10px; }}
  table       {{ width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }}
  th          {{ background:#003d4d; color:#fff; padding:7px 9px; text-align:left; font-weight:500; }}
  td          {{ padding:6px 9px; border-bottom:1px solid #eee; color:#2c2c2c; }}
  tr:nth-child(even) td {{ background:#f9f9f9; }}
  .footer     {{ background:#f4f4f4; padding:12px 30px; font-size:11px; color:#999; text-align:center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Morning Market Briefing</h1>
    <p>{today} · 08:00 CET · TPG · ECO · ALPHA · TRMD · FRO · DOV · INTRUM · TEN</p>
  </div>
  <div class="macro-bar">{macro_bar}</div>
  <div class="body">
    {narrative_html}
    {"<h3>Summary Table</h3>" + table_raw if table_raw else ""}
  </div>
  <div class="footer">
    Generated by Claude · Prices: Yahoo Finance · News: Perplexity · For internal use only
  </div>
</div>
</body>
</html>"""
    return html


# ── 6. Send Email ──────────────────────────────────────────────────────────────

def send_email(html_body: str):
    today   = datetime.date.today().strftime("%d %b %Y")
    subject = f"Morning Briefing · 8 titres · {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, GMAIL_APP_PASS)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

    print(f"✅ Briefing sent to {RECIPIENT_EMAIL}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    tickers = list(WATCHLIST.keys())

    print("🌍 Fetching macro data...")
    macro = fetch_macro()

    print("📰 Fetching macro news...")
    macro_news = fetch_macro_news()

    print("📊 Fetching stock prices...")
    prices = fetch_prices(tickers)

    print("📰 Fetching stock news...")
    news = {}
    for ticker, meta in WATCHLIST.items():
        news[ticker] = fetch_news_perplexity(ticker, meta["name"])

    print("🤖 Synthesizing with Claude...")
    briefing = synthesize_briefing(macro, macro_news, prices, news)

    print("📧 Building & sending email...")
    html = build_email_html(briefing, macro)
    send_email(html)


if __name__ == "__main__":
    main()

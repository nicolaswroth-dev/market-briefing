"""
Morning Market Briefing — Nicolas Roth
v4: Macro section as inline HTML text (not CSS bar) + yfinance fallback for NaN prices
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

RECIPIENT_EMAIL = "Nicolas.w.roth@gmail.com"
SENDER_EMAIL    = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASS  = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
PERPLEXITY_KEY  = os.environ["PERPLEXITY_API_KEY"]

# ── 1. Fetch Macro Data ────────────────────────────────────────────────────────

def fetch_macro() -> dict:
    macro = {}
    instruments = [
        ("ES=F",     "S&P 500 Futures"),
        ("NQ=F",     "Nasdaq 100 Futures"),
        ("BZ=F",     "Brent Crude (USD/bbl)"),
        ("CL=F",     "WTI Crude (USD/bbl)"),
        ("^TNX",     "US 10Y Yield (%)"),
        ("EURUSD=X", "EUR/USD"),
        ("SEKUSD=X", "SEK/USD"),
    ]
    for ticker, label in instruments:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) < 2:
                macro[label] = {"error": "no data"}
                continue
            last = hist["Close"].iloc[-1]
            prev = hist["Close"].iloc[-2]
            import math
            if math.isnan(last) or math.isnan(prev):
                macro[label] = {"error": "NaN"}
                continue
            last = round(last, 4)
            prev = round(prev, 4)
            chg  = round((last - prev) / prev * 100, 2)
            macro[label] = {"last": last, "chg_pct": chg}
        except Exception as e:
            macro[label] = {"error": str(e)}
    return macro


def fetch_macro_news() -> str:
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


# ── 2. Fetch Stock Prices (with NaN fallback) ──────────────────────────────────

def fetch_prices(tickers: list) -> dict:
    import math
    data = {}
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            # Try multiple periods to get a valid close
            hist = None
            for period in ["5d", "1mo"]:
                h = stock.history(period=period)
                # Filter out NaN closes
                h = h[h["Close"].notna()]
                if len(h) >= 2:
                    hist = h
                    break

            if hist is None or len(hist) < 2:
                data[ticker] = {"error": "insufficient data"}
                continue

            close_today = round(hist["Close"].iloc[-1], 2)
            close_prev  = round(hist["Close"].iloc[-2], 2)

            if math.isnan(close_today) or math.isnan(close_prev):
                data[ticker] = {"error": "NaN price"}
                continue

            pct_change = round((close_today - close_prev) / close_prev * 100, 2)
            volume     = int(hist["Volume"].iloc[-1]) if not math.isnan(hist["Volume"].iloc[-1]) else 0
            avg_vol    = int(hist["Volume"].mean())   if not math.isnan(hist["Volume"].mean())   else 0
            vol_ratio  = round(volume / avg_vol, 2)   if avg_vol > 0 else 0

            info = stock.fast_info
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
                "avg_volume":     avg_vol,
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
        f"Focus on: earnings, analyst upgrades/downgrades, M&A, regulatory events, management changes, macro events. "
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
        "Structure the briefing strictly as follows — use these exact HTML heading tags:\n"
        "<h3>MACRO OVERNIGHT</h3>: 3-4 sentence synthesis of the macro backdrop — futures, oil, rates, key headlines. "
        "Explicitly connect macro to the watchlist where relevant "
        "(Brent/WTI move → tanker names ECO/TRMD/FRO/TEN; EUR/USD → doValue/Intrum; 10Y yield → Alpha Bank). "
        "Flag any macro move >1% with ⚠️.\n"
        "<h3>ALERTS</h3>: flag stock moves >2% or material news. If none, omit this section.\n"
        "<h3>PRIVATE EQUITY</h3>: TPG narrative paragraph.\n"
        "<h3>GREEK BANKS</h3>: Alpha Bank narrative paragraph.\n"
        "<h3>TANKER SHIPPING</h3>: ECO, TORM, Frontline, TEN — one paragraph each.\n"
        "<h3>CREDIT / NPL SERVICERS</h3>: doValue, Intrum — one paragraph each.\n"
        "<h3>SUMMARY TABLE</h3>: HTML table with columns: "
        "Ticker | Name | Last Price | Chg% | Vol/Avg | 52W High | % from High | Key Signal.\n"
        "Each narrative paragraph: 3-5 sentences covering price action → volume → news → macro linkage → implication. "
        "Be declarative — verdicts, not descriptions. Output clean HTML only, no markdown."
    )

    user_prompt = f"""Date: {today}

MACRO PRICES:
{json.dumps(macro, indent=2)}

MACRO NEWS (last 12h):
{macro_news}

STOCK PRICES:
{json.dumps(prices, indent=2)}

STOCK NEWS (last 24h):
{"".join([f"=== {t} ({WATCHLIST[t]['name']}) ===\n{n}\n\n" for t, n in news.items()])}

Write the morning briefing in clean HTML following the structure above."""

    response = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 3500,
        system     = system_prompt,
        messages   = [{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


# ── 5. Build HTML Email ────────────────────────────────────────────────────────

def build_macro_bar(macro: dict) -> str:
    """Renders macro snapshot as a simple HTML table row — Gmail-safe."""
    def fmt_cell(label, key):
        d = macro.get(key, {})
        if "error" in d or not d:
            return f"<td style='padding:4px 10px;color:#a8c8d0;font-size:11px'>{label}<br><b>N/A</b></td>"
        chg   = d["chg_pct"]
        color = "#7ee8a2" if chg >= 0 else "#ffaaaa"
        arrow = "▲" if chg >= 0 else "▼"
        return (f"<td style='padding:4px 10px;color:#d0e8ee;font-size:11px;white-space:nowrap'>"
                f"{label}<br><b style='font-size:12px'>{d['last']}</b> "
                f"<span style='color:{color}'>{arrow}{abs(chg)}%</span></td>")

    cells = (
        fmt_cell("S&P Fut",  "S&P 500 Futures") +
        fmt_cell("NQ Fut",   "Nasdaq 100 Futures") +
        fmt_cell("Brent",    "Brent Crude (USD/bbl)") +
        fmt_cell("WTI",      "WTI Crude (USD/bbl)") +
        fmt_cell("US 10Y",   "US 10Y Yield (%)") +
        fmt_cell("EUR/USD",  "EUR/USD") +
        fmt_cell("SEK/USD",  "SEK/USD")
    )
    return f"""<table width="100%" cellpadding="0" cellspacing="0"
               style="background:#012e3a;border-bottom:1px solid #024055">
               <tr>{cells}</tr></table>"""


def build_email_html(briefing_html: str, macro: dict) -> str:
    today     = datetime.date.today().strftime("%A %d %B %Y")
    macro_bar = build_macro_bar(macro)

    # Strip any markdown fences Claude may have added
    briefing_html = briefing_html.replace("```html", "").replace("```", "").strip()

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:'Helvetica Neue',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:30px 0">
<table width="700" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden">

  <!-- HEADER -->
  <tr><td style="background:#003d4d;padding:20px 30px">
    <div style="color:#ffffff;font-size:17px;font-weight:600;letter-spacing:0.5px">Morning Market Briefing</div>
    <div style="color:#a8c8d0;font-size:11px;margin-top:4px">{today} · 08:00 CET · TPG · ECO · ALPHA · TRMD · FRO · DOV · INTRUM · TEN</div>
  </td></tr>

  <!-- MACRO BAR -->
  <tr><td>{macro_bar}</td></tr>

  <!-- BODY -->
  <tr><td style="padding:24px 30px;color:#2c2c2c;font-size:13.5px;line-height:1.65">
    <style>
      h3{{font-size:11px;text-transform:uppercase;letter-spacing:0.9px;color:#003d4d;
          border-bottom:1px solid #e0e0e0;padding-bottom:5px;margin:20px 0 10px}}
      p{{margin:0 0 11px}}
      table.summary{{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}}
      table.summary th{{background:#003d4d;color:#fff;padding:7px 9px;text-align:left;font-weight:500}}
      table.summary td{{padding:6px 9px;border-bottom:1px solid #eee}}
      table.summary tr:nth-child(even) td{{background:#f9f9f9}}
    </style>
    {briefing_html}
  </td></tr>

  <!-- FOOTER -->
  <tr><td style="background:#f4f4f4;padding:12px 30px;font-size:11px;color:#999;text-align:center">
    Generated by Claude · Prices: Yahoo Finance · News: Perplexity · For internal use only
  </td></tr>

</table>
</td></tr></table>
</body></html>"""
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

"""
Morning Market Briefing — Nicolas Roth
v6: Macro bar removed. NaN fallback improved with Oslo/alternate tickers.
"""

import os
import json
import math
import smtplib
import datetime
import yfinance as yf
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from anthropic import Anthropic

# ── Config ────────────────────────────────────────────────────────────────────

WATCHLIST = {
    "TPG":       {"name": "TPG Inc.",                      "alt": None},
    "ECO":       {"name": "Okeanis Eco Tankers Corp.",     "alt": "OET.OL"},
    "ALPHA.AT":  {"name": "Alpha Bank S.A.",               "alt": None},
    "TRMD":      {"name": "TORM plc",                      "alt": "TRMD-B.CO"},
    "FRO":       {"name": "Frontline plc",                 "alt": "FRO.OL"},
    "DOV.MI":    {"name": "doValue S.p.A.",                "alt": None},
    "INTRUM.ST": {"name": "Intrum AB",                     "alt": None},
    "TEN":       {"name": "Tsakos Energy Navigation Ltd.", "alt": "TNP.AT"},
}

RECIPIENT_EMAIL = "Nicolas.w.roth@gmail.com"
SENDER_EMAIL    = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASS  = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
PERPLEXITY_KEY  = os.environ["PERPLEXITY_API_KEY"]

# ── 1. Fetch Stock Prices (with alternate ticker fallback) ─────────────────────

def get_valid_history(ticker: str):
    """Try primary ticker, then alternate, across multiple periods."""
    candidates = [ticker]
    alt = WATCHLIST.get(ticker, {}).get("alt")
    if alt:
        candidates.append(alt)

    for t in candidates:
        for period in ["5d", "1mo"]:
            try:
                h = yf.Ticker(t).history(period=period)
                h = h[h["Close"].notna()]
                if len(h) >= 2:
                    return h, t
            except Exception:
                continue
    return None, ticker


def fetch_prices(tickers: list) -> dict:
    data = {}
    for ticker in tickers:
        try:
            hist, used_ticker = get_valid_history(ticker)

            if hist is None or len(hist) < 2:
                data[ticker] = {"error": "no data", "name": WATCHLIST[ticker]["name"]}
                continue

            close_today = round(float(hist["Close"].iloc[-1]), 2)
            close_prev  = round(float(hist["Close"].iloc[-2]), 2)
            pct_change  = round((close_today - close_prev) / close_prev * 100, 2)
            vol         = float(hist["Volume"].iloc[-1])
            avg_vol     = float(hist["Volume"].mean())
            volume      = int(vol)     if not math.isnan(vol)     else 0
            avg_volume  = int(avg_vol) if not math.isnan(avg_vol) else 0
            vol_ratio   = round(volume / avg_volume, 2) if avg_volume > 0 else 0

            info = yf.Ticker(used_ticker).fast_info
            try:
                week52_high   = round(float(info.year_high), 2)
                week52_low    = round(float(info.year_low),  2)
                pct_from_high = round((close_today - week52_high) / week52_high * 100, 1)
            except Exception:
                week52_high = week52_low = pct_from_high = None

            try:
                currency = info.currency
            except Exception:
                currency = "USD"

            data[ticker] = {
                "name":           WATCHLIST[ticker]["name"],
                "source_ticker":  used_ticker,
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
            data[ticker] = {"error": str(e), "name": WATCHLIST[ticker]["name"]}
    return data


# ── 2. Fetch News ─────────────────────────────────────────────────────────────

def fetch_macro_news() -> str:
    url     = "https://api.perplexity.ai/chat/completions"
    headers = {"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"}
    prompt  = (
        "List the 5 most important macro/market headlines from the last 12 hours for equity investors. "
        "Focus on: Fed/ECB statements, geopolitical events, commodity moves (especially oil), "
        "major data releases. Bullet points, one sentence each, source and time if available."
    )
    payload = {"model": "sonar", "messages": [{"role": "user", "content": prompt}]}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Macro news unavailable: {e}"


def fetch_stock_news(ticker: str, name: str) -> str:
    url     = "https://api.perplexity.ai/chat/completions"
    headers = {"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"}
    prompt  = (
        f"List the most important news from the last 24 hours about {name} ({ticker}). "
        f"Focus on earnings, analyst calls, M&A, regulatory events, management changes. "
        f"One sentence per item with source. If nothing material: 'No material news in last 24h'."
    )
    payload = {"model": "sonar", "messages": [{"role": "user", "content": prompt}]}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"News unavailable: {e}"


# ── 3. Synthesize Narratives with Claude ──────────────────────────────────────

def synthesize_narratives(macro_news: str, prices: dict, stock_news: dict) -> str:
    client = Anthropic(api_key=ANTHROPIC_KEY)
    today  = datetime.date.today().strftime("%A %d %B %Y")

    system_prompt = (
        "You are a senior equity analyst writing a pre-market briefing for a portfolio manager. "
        "Institutional register: dense, declarative, no filler. "
        "Display prices in their native currency (see 'currency' field in data). "
        "Output ONLY the narrative sections below in clean HTML — no markdown, no code fences, no summary table.\n\n"
        "OUTPUT STRUCTURE — write every section in this exact order, no exceptions:\n\n"
        "1. <h3>MACRO OVERNIGHT</h3>\n"
        "MANDATORY — always write this section even if macro is quiet. "
        "4-5 sentences covering: equity futures direction, oil (Brent/WTI) and its implication for "
        "the tanker names (ECO/TRMD/FRO/TEN), rates (US 10Y) and implication for ALPHA, "
        "EUR/SEK moves and implication for DOV/INTRUM, and the single most important macro headline. "
        "Flag any move >1% with ⚠️. If macro is quiet, say so explicitly.\n\n"
        "2. <h3>ALERTS</h3>\n"
        "Stock moves >2% or material news only. Omit this section entirely if none qualify.\n\n"
        "3. <h3>PRIVATE EQUITY</h3>\n"
        "TPG: 3-4 sentences (price → volume → news → implication).\n\n"
        "4. <h3>GREEK BANKS</h3>\n"
        "Alpha Bank: 3-4 sentences.\n\n"
        "5. <h3>TANKER SHIPPING</h3>\n"
        "ECO, TRMD, FRO, TEN: 3 sentences each.\n\n"
        "6. <h3>CREDIT / NPL SERVICERS</h3>\n"
        "doValue, Intrum: 3-4 sentences each.\n\n"
        "No table. No preamble. Start directly with <h3>MACRO OVERNIGHT</h3>."
    )

    user_prompt = f"""Date: {today}

MACRO NEWS (last 12h):
{macro_news}

STOCK PRICE DATA:
{json.dumps(prices, indent=2)}

STOCK NEWS (last 24h):
{"".join([f"[{t}] {n}\n\n" for t, n in stock_news.items()])}"""

    response = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 3000,
        system     = system_prompt,
        messages   = [{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.replace("```html", "").replace("```", "").strip()


# ── 4. Build Summary Table in Python (never truncated) ────────────────────────

def build_summary_table(prices: dict) -> str:
    def pct_color(chg):
        if chg is None: return "#888"
        return "#1a7a3f" if chg >= 0 else "#c0392b"

    def pct_fmt(chg):
        if chg is None: return "—"
        return f"{'▲' if chg >= 0 else '▼'}{abs(chg):.2f}%"

    rows = ""
    for ticker, d in prices.items():
        name = d.get("name", "—")
        if "error" in d:
            rows += (f"<tr><td><b>{ticker}</b></td><td style='font-size:11px'>{name}</td>"
                     f"<td colspan='5' style='color:#999'>Data unavailable</td></tr>")
            continue

        cur   = d.get("currency", "")
        close = f"{cur} {d['close']:.2f}" if d.get("close") is not None else "—"
        chg   = d.get("pct_change")
        vr    = f"{d['vol_ratio']:.2f}×" if d.get("vol_ratio") else "—"
        h52   = f"{cur} {d['week52_high']:.2f}" if d.get("week52_high") else "—"
        pfh   = f"{d['pct_from_high']:.1f}%" if d.get("pct_from_high") is not None else "—"

        rows += f"""<tr>
          <td style="padding:6px 9px"><b>{ticker}</b></td>
          <td style="padding:6px 9px;font-size:11px">{name}</td>
          <td style="padding:6px 9px">{close}</td>
          <td style="padding:6px 9px;color:{pct_color(chg)};font-weight:600">{pct_fmt(chg)}</td>
          <td style="padding:6px 9px">{vr}</td>
          <td style="padding:6px 9px">{h52}</td>
          <td style="padding:6px 9px">{pfh}</td>
        </tr>"""

    return f"""<table width="100%" cellpadding="0" cellspacing="0"
       style="border-collapse:collapse;font-size:12px;margin-top:8px">
  <tr style="background:#003d4d;color:#fff">
    <th style="padding:7px 9px;text-align:left">Ticker</th>
    <th style="padding:7px 9px;text-align:left">Name</th>
    <th style="padding:7px 9px;text-align:left">Last</th>
    <th style="padding:7px 9px;text-align:left">Chg%</th>
    <th style="padding:7px 9px;text-align:left">Vol/Avg</th>
    <th style="padding:7px 9px;text-align:left">52W High</th>
    <th style="padding:7px 9px;text-align:left">% from High</th>
  </tr>
  {rows}
</table>"""


# ── 5. Build & Send Email ─────────────────────────────────────────────────────

def build_email_html(narratives: str, table_html: str) -> str:
    today = datetime.date.today().strftime("%A %d %B %Y")
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:'Helvetica Neue',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:30px 0">
<table width="700" cellpadding="0" cellspacing="0"
       style="background:#fff;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.08)">

  <tr><td style="background:#003d4d;padding:20px 30px;border-radius:6px 6px 0 0">
    <div style="color:#fff;font-size:17px;font-weight:600;letter-spacing:0.5px">Morning Market Briefing</div>
    <div style="color:#a8c8d0;font-size:11px;margin-top:4px">
      {today} · 08:00 CET · TPG · ECO · ALPHA · TRMD · FRO · DOV · INTRUM · TEN
    </div>
  </td></tr>

  <tr><td style="padding:24px 30px;color:#2c2c2c;font-size:13.5px;line-height:1.7">
    <style>
      h3{{font-size:11px;text-transform:uppercase;letter-spacing:0.9px;color:#003d4d;
          border-bottom:1px solid #e0e0e0;padding-bottom:5px;margin:22px 0 10px;font-weight:700}}
      p{{margin:0 0 10px}}
    </style>
    {narratives}
    <h3>SUMMARY TABLE</h3>
    {table_html}
    <p style="margin-top:12px;font-size:11px;color:#999">
      Data as of last close · NaN prices reflect feed gaps, not confirmed halts
    </p>
  </td></tr>

  <tr><td style="background:#f4f4f4;padding:12px 30px;font-size:11px;color:#999;
                 text-align:center;border-radius:0 0 6px 6px">
    Generated by Claude · Prices: Yahoo Finance · News: Perplexity · Internal use only
  </td></tr>

</table></td></tr></table>
</body></html>"""


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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    tickers = list(WATCHLIST.keys())

    print("📰 Fetching macro news...")
    macro_news = fetch_macro_news()

    print("📊 Fetching stock prices...")
    prices = fetch_prices(tickers)

    print("📰 Fetching stock news...")
    stock_news = {t: fetch_stock_news(t, WATCHLIST[t]["name"]) for t in tickers}

    print("🤖 Synthesizing with Claude...")
    narratives = synthesize_narratives(macro_news, prices, stock_news)

    print("📊 Building table...")
    table_html = build_summary_table(prices)

    print("📧 Sending email...")
    html = build_email_html(narratives, table_html)
    send_email(html)


if __name__ == "__main__":
    main()

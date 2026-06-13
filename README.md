# Morning Market Briefing

Daily pre-market email briefing — TPG · ECO  
Delivered 08:00 CET, Mon–Fri.

## Stack (100% free tier)
| Layer | Tool |
|---|---|
| Prices | yfinance (Yahoo Finance) |
| News 24h | Perplexity API (inclus dans Max) |
| Synthesis | Claude API (claude-sonnet-4-6) |
| Scheduler | GitHub Actions (cron) |
| Delivery | Gmail SMTP |

---

## Setup — 4 étapes

### 1. Fork / créer le repo GitHub
- Crée un repo privé sur github.com (ex: `market-briefing`)
- Upload les 3 fichiers : `briefing.py`, `requirements.txt`, `.github/workflows/briefing.yml`

### 2. Ajouter les 4 secrets GitHub
Dans ton repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Valeur |
|---|---|
| `ANTHROPIC_API_KEY` | Ta clé API Anthropic (console.anthropic.com) |
| `PERPLEXITY_API_KEY` | Ta clé API Perplexity (perplexity.ai/settings/api) |
| `GMAIL_ADDRESS` | nicolas.w.roth@gmail.com |
| `GMAIL_APP_PASSWORD` | Voir étape 3 ci-dessous |

### 3. Générer un Gmail App Password
1. Aller sur myaccount.google.com → Sécurité
2. Activer **Validation en 2 étapes** si pas encore fait
3. Chercher "Mots de passe des applications"
4. Créer un mot de passe pour "Mail" → copier les 16 caractères
5. Coller dans le secret `GMAIL_APP_PASSWORD`

### 4. Tester manuellement
Dans ton repo GitHub → **Actions → Morning Market Briefing → Run workflow**  
Tu reçois l'email dans les 2 minutes.

---

## Modifier la watchlist
Dans `briefing.py`, section Config :
```python
WATCHLIST = {
    "TPG":  {"name": "TPG Inc.",    "exchange": "NASDAQ"},
    "ECO":  {"name": "Enviva Inc.", "exchange": "NYSE"},
    # Ajouter ici : "TICKER": {"name": "Company Name", "exchange": "NYSE"},
}
```

## Ajuster l'heure (heure d'été)
Dans `.github/workflows/briefing.yml` :
- **Hiver CET (UTC+1)** : `cron: "50 6 * * 1-5"`
- **Été CEST (UTC+2)** : `cron: "50 5 * * 1-5"`

---

## Obtenir ta clé API Anthropic
- console.anthropic.com → API Keys → Create Key
- Note : Claude Pro n'inclut **pas** l'accès API — l'API est facturée séparément
- Pour ce script : ~$0.05–0.10 par briefing (claude-sonnet-4-6)
- Alternative gratuite : remplacer par `claude-haiku-4-5` (~$0.003 par briefing)

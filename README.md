# What Are Jacob & Mahima Going to Watch?

Combines two Letterboxd lists, checks where each film streams in the US on JustWatch, and publishes a page sorted by what's on our services.

- Jacob: https://letterboxd.com/jfmurphy198/list/to-watch/
- Mahima: https://letterboxd.com/astevemartini/list/jm-watchlist/

## How it refreshes

GitHub Actions (`.github/workflows/refresh.yml`) rebuilds the page every Sunday, on every push, and whenever you press **Refresh the programme** on the page. That button opens the workflow on GitHub: tap **Run workflow**, and the page updates about a minute later. Both lists must stay public.

## Changing things

- Lists and streaming services: top of `watchlist.py` (`LISTS`, `SERVICES`)
- Look and Easter eggs: `template.html`
- Home-screen icon and app manifest: `static/`
- Run locally: `python3 watchlist.py` writes `site/index.html` (Python 3, no packages needed)

## One-time setup

Repository → Settings → Pages → Source: **GitHub Actions**.

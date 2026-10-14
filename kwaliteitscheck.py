"""Kwaliteitscheck mijnwebservice: harde controles die elke site moet halen.

Gebruik:  python3 scripts/kwaliteitscheck.py http://localhost:8080 --modus demo
          python3 scripts/kwaliteitscheck.py https://www.voorbeeld.nl --modus oplevering
Optioneel: --paginas / /contact /diensten   (anders: alle interne links van de homepage, max 12)
Uitkomst: per controle PASS, WARN of FAIL, en exit-code 1 bij een FAIL.
"""
import argparse, asyncio, json, os, re, sys
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright

VIEWPORTS = [(390, 844), (1280, 720), (1440, 800), (1920, 1080)]
VERBODEN = [
    "welkom op onze website", "kwaliteit staat bij ons voorop", "uw partner in", "jouw partner in",
    "de klant staat centraal", "op maat gemaakte oplossingen", "wij ontzorgen", "met passie",
    "toonaangevend", "innovatief", "hoogwaardig", "uitstekende service", "naar een hoger niveau",
    "ontdek", "of je nu", "lorem ipsum",
]
results = []

def add(status, check, detail=""):
    results.append({"status": status, "check": check, "detail": detail})

HERO_JS = r"""
() => {
  const main = document.querySelector('main') || document.body;
  const hero = document.querySelector('.hero') || main.querySelector('section') || main;
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05; };
  const h1 = document.querySelector('h1');
  const ctas = [...hero.querySelectorAll('a, button')].filter(vis).filter(e => (e.innerText || '').trim().length > 2);
  const media = [...hero.querySelectorAll('img, video, canvas, picture')].filter(vis).filter(e => e.getBoundingClientRect().width > 120);
  const texts = [...hero.querySelectorAll('h1, h2, p, a, button')].filter(vis);
  const ov = [];
  for (const t of texts) { const a = t.getBoundingClientRect();
    for (const m of media) { const b = m.getBoundingClientRect();
      // take the visible content box of contain-fitted media roughly as its rect
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 4 && h > 4 && !m.contains(t) && !t.contains(m)) ov.push(`${t.tagName} "${(t.innerText||'').trim().slice(0,30)}" over ${m.tagName}`); } }
  return {
    h1Bottom: h1 ? h1.getBoundingClientRect().bottom : null,
    ctaBottom: ctas.length ? ctas[0].getBoundingClientRect().bottom : null,
    ctaText: ctas.length ? ctas[0].innerText.trim().slice(0, 40) : null,
    vh: innerHeight, overflow: document.documentElement.scrollWidth > innerWidth + 1, overlaps: ov.slice(0, 5)
  };
}
"""

PAGE_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const text = document.body.innerText;
  const imgs = [...document.images].filter(vis).filter(i => i.getBoundingClientRect().width > 120)
    .map(i => ({ src: (i.currentSrc || i.src).split('/').pop().slice(0, 60), natural: i.naturalWidth, shown: Math.round(i.getBoundingClientRect().width), complete: i.complete }));
  const vids = [...document.querySelectorAll('video')].map(v => ({ w: v.videoWidth, shown: Math.round(v.getBoundingClientRect().width) }));
  const internalArrows = [...document.querySelectorAll('a, button')].filter(e => {
    const href = e.getAttribute('href') || '';
    const external = /^https?:/.test(href) && !href.includes(location.host);
    const txt = (e.innerText || '').trim();
    const svgs = [...e.querySelectorAll('svg')].map(s => s.getAttribute('class') || '');
    const hasArrow = /[→↗➝➜]/.test(txt) || svgs.some(c => /arrow-(right|up-right)|move-right|chevron-right/.test(c));
    return hasArrow && !external && txt.replace(/[→↗➝➜]/g, '').trim().length > 2;
  }).map(e => (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 30));
  const labels = [...document.querySelectorAll('h2')].map(h => h.previousElementSibling).filter(p => {
    if (!p) return false; const s = getComputedStyle(p);
    return (s.fontFamily.toLowerCase().includes('mono') || s.textTransform === 'uppercase') && parseFloat(s.fontSize) <= 14 && (p.innerText||'').length < 40;
  }).length;
  const links = [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'));
  const meta = n => document.querySelector(`meta[name="${n}"]`)?.content || null;
  return {
    text, imgs, vids, internalArrows, labels, links,
    h1Count: document.querySelectorAll('h1').length,
    robots: meta('robots'), title: document.title, description: meta('description'),
    canonical: document.querySelector('link[rel=canonical]')?.href || null,
    ogImage: document.querySelector('meta[property="og:image"]')?.content || null,
    icons: [...document.querySelectorAll('link[rel*=icon]')].map(l => l.href),
    canvas: document.querySelectorAll('canvas').length,
    tel: !!document.querySelector('a[href^="tel:"]'), mail: !!document.querySelector('a[href^="mailto:"]'),
    lang: document.documentElement.getAttribute('lang') || '',
    viewport: !!document.querySelector('meta[name="viewport"]'),
    noAlt: [...document.images].filter(vis).filter(i => i.getBoundingClientRect().width > 120 && !i.hasAttribute('alt')).map(i => (i.currentSrc || i.src).split('/').pop().slice(0, 50)),
    badInputs: [...document.querySelectorAll('input')].filter(i => {
      const n = ((i.name || '') + ' ' + (i.id || '') + ' ' + (i.autocomplete || '')).toLowerCase(); const t = (i.type || 'text').toLowerCase();
      return (/(tel|telefoon|phone)/.test(n) && t !== 'tel') || (/(e-?mail)/.test(n) && t !== 'email');
    }).map(i => i.name || i.id),
  };
}
"""

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("--modus", choices=["demo", "oplevering"], required=True)
    ap.add_argument("--paginas", nargs="*")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    host = urlparse(base).netloc
    launch = {"headless": True}
    if os.environ.get("CHROME_PATH"): launch["executable_path"] = os.environ["CHROME_PATH"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 800})
        req = ctx.request

        # robots.txt en sitemap
        r = await req.get(base + "/robots.txt")
        robots_txt = (await r.text()) if r.ok else ""
        blocked = bool(re.search(r"(?im)^\s*disallow:\s*/\s*$", robots_txt))
        if a.modus == "demo":
            add("PASS" if blocked else "FAIL", "robots.txt blokkeert alles (demo)", robots_txt.strip()[:80])
        else:
            add("FAIL" if blocked else "PASS", "robots.txt laat Google toe (oplevering)", robots_txt.strip()[:80])
            sm = re.search(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt)
            sm_url = base + urlparse(sm.group(1)).path if sm else base + "/sitemap.xml"
            s = await req.get(sm_url)
            s_body = (await s.text()) if s.ok else ""
            if "<sitemapindex" in s_body:  # WordPress en andere sitemap-indexen: eerste deel-sitemap volgen
                first = re.search(r"<loc>\s*([^<\s]+)", s_body)
                if first:
                    s = await req.get(base + urlparse(first.group(1)).path)
            add("PASS" if s.ok and "<urlset" in (await s.text()) else "FAIL", "sitemap bestaat", sm_url.replace(base, "") or "/")
        if a.modus == "oplevering":
            # zoekmachines en AI-zoekbots mogen niet geblokkeerd zijn
            blocks = {}
            agent = None
            for line in robots_txt.splitlines():
                l = line.split("#")[0].strip()
                if l.lower().startswith("user-agent:"): agent = l.split(":", 1)[1].strip().lower()
                elif l.lower().startswith("disallow:") and l.split(":", 1)[1].strip() == "/" and agent: blocks[agent] = True
            geblokt = [b for b in ("googlebot", "bingbot", "oai-searchbot", "applebot") if blocks.get(b)]
            add("FAIL" if geblokt else "PASS", "zoekmachines en AI-zoekbots niet geblokkeerd in robots.txt", ", ".join(geblokt))
            add("PASS" if re.search(r"(?im)^\s*sitemap:", robots_txt) else "WARN", "robots.txt verwijst naar de sitemap")
            s_txt = (await s.text()) if s.ok else ""
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", s_txt)[:30]
            wrong_host = [u for u in locs if urlparse(u).netloc and urlparse(u).netloc != host and not base.startswith("http://localhost") and not base.startswith("http://127.")]
            add("WARN" if wrong_host else "PASS", "sitemap gebruikt hetzelfde domein", ", ".join(wrong_host[:3]))
        if base.startswith("https://"):
            hr_ = await req.get("http://" + host + "/", max_redirects=0)
            loc = hr_.headers.get("location", "")
            add("PASS" if hr_.status in (301, 308) and loc.startswith("https://") else ("WARN" if hr_.status in (302, 307) else "FAIL"),
                "http stuurt door naar https", f"{hr_.status} {loc}")
        # niet-bestaande pagina moet een echte 404 geven
        nf = await req.get(base + "/deze-pagina-bestaat-niet-kwaliteitscheck")
        nf_txt = await nf.text()
        add("PASS" if nf.status == 404 else "FAIL", "niet-bestaande pagina geeft een 404", str(nf.status))
        # ruwe HTML: precies één html-document en één title
        raw = await (await req.get(base + "/")).text()
        # inhoud zonder JavaScript: Bing en veel AI-bots voeren geen JavaScript uit
        raw_txt = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", raw)
        raw_txt = re.sub(r"<[^>]+>", " ", raw_txt)
        raw_words = len(raw_txt.split())
        raw_h1 = bool(re.search(r"<h1[\s>]", raw, re.I))
        ok_raw = raw_h1 and raw_words >= 150
        add("PASS" if ok_raw else ("FAIL" if a.modus == "oplevering" else "WARN"),
            "tekst en H1 staan in de HTML zonder JavaScript", f"{raw_words} woorden, H1 {'ja' if raw_h1 else 'nee'}")
        # www en zonder www: één vaste variant met een permanente doorverwijzing
        if base.startswith("https://") and not host.startswith("www."):
            try:
                wr = await req.get("https://www." + host + "/", max_redirects=0)
                wl = wr.headers.get("location", "")
                add("PASS" if wr.status in (301, 308) else "WARN", "www stuurt permanent door (301)", f"{wr.status} {wl}")
            except Exception as e:
                add("WARN", "www stuurt permanent door (301)", f"www niet bereikbaar: {str(e)[:60]}")
        n_html, n_title = len(re.findall(r"<html[\s>]", raw, re.I)), len(re.findall(r"<title[\s>]", raw, re.I))
        add("PASS" if n_html <= 1 and n_title <= 1 else "FAIL", "homepage is één HTML-document met één title", f"html {n_html}, title {n_title}")
        fav = await req.get(base + "/favicon.ico")
        add("PASS" if fav.ok and "html" not in (fav.headers.get("content-type", "")) else "WARN", "/favicon.ico bestaat")

        # pagina's bepalen
        errors = []
        closing = set()
        async def fresh(url, w=1440, h=800):
            pg = await ctx.new_page()
            await pg.set_viewport_size({"width": w, "height": h})
            path = urlparse(url).path or "/"
            # errors fired while Playwright closes the page (unload) are test artefacts, not visitor errors
            pg.on("pageerror", lambda e: None if pg in closing else errors.append(f"{path} {w}px: {str(e)[:110]}"))
            pg.on("console", lambda m: None if pg in closing or m.type != "error" else errors.append(f"{path} {w}px: {m.text[:110]}"))
            resp = await pg.goto(url, wait_until="networkidle")
            return pg, resp
        page, _ = await fresh(base + "/")
        home = await page.evaluate(PAGE_JS)
        def internal(h):
            if not h or h.startswith(("#", "tel:", "mailto:", "javascript:")): return False
            u = urlparse(urljoin(base + "/", h))
            return u.scheme in ("http", "https") and u.netloc == host
        paths = a.paginas or ["/"] + sorted({urlparse(urljoin(base + "/", h)).path or "/" for h in home["links"] if internal(h)} - {"/"})[:11]

        # hero per viewport (alleen homepage)
        for w, h in VIEWPORTS:
            closing.add(page); await page.close()
            page, _ = await fresh(base + "/", w, h); await page.wait_for_timeout(600)
            hr = await page.evaluate(HERO_JS)
            ok = hr["h1Bottom"] is not None and hr["h1Bottom"] <= hr["vh"] and hr["ctaBottom"] is not None and hr["ctaBottom"] <= hr["vh"]
            add("PASS" if ok else "FAIL", f"hero {w}x{h}: kop en eerste knop in eerste scherm",
                f'h1 {round(hr["h1Bottom"] or -1)} / knop "{hr["ctaText"]}" {round(hr["ctaBottom"] or -1)} / scherm {hr["vh"]}')
            add("FAIL" if hr["overflow"] else "PASS", f"{w}px: geen horizontaal scrollen")
            add("FAIL" if hr["overlaps"] else "PASS", f"{w}px: geen tekst over beeld in de hero", "; ".join(hr["overlaps"]))

        # per pagina
        all_links, titles = set(), {}
        for path in paths:
            url = base + path
            closing.add(page); await page.close()
            page, resp = await fresh(url)
            # scroll de hele pagina zodat lazy beelden laden
            await page.evaluate("async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) { scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } scrollTo(0, 0); }")
            await page.wait_for_timeout(500)
            d = await page.evaluate(PAGE_JS)
            tag = path
            add("PASS" if resp and resp.ok else "FAIL", f"{tag}: pagina laadt", str(resp.status if resp else "geen antwoord"))
            add("PASS" if d["h1Count"] == 1 else "FAIL", f"{tag}: precies één H1", str(d["h1Count"]))
            rob = (d["robots"] or "").lower()
            if a.modus == "demo":
                add("PASS" if "noindex" in rob else "FAIL", f"{tag}: noindex (demo)", rob)
            elif path.rstrip("/") not in ("/bedankt",):
                add("FAIL" if "noindex" in rob else "PASS", f"{tag}: geen noindex (oplevering)", rob)
                add("PASS" if d["canonical"] else "FAIL", f"{tag}: canonical", d["canonical"] or "")
            add("PASS" if d["title"] and len(d["title"]) <= 65 else "WARN", f"{tag}: title", d["title"])
            add("PASS" if d["description"] else "FAIL", f"{tag}: meta description")
            low = d["text"].lower()
            dash = [m.group(0) for m in re.finditer(r".{0,15}(—| – ).{0,15}", d["text"])]
            add("FAIL" if dash else "PASS", f"{tag}: geen gedachtestreepjes", " | ".join(dash[:3]))
            excl = [m.group(0) for m in re.finditer(r"\S{0,20}!", d["text"])]
            add("FAIL" if excl else "PASS", f"{tag}: geen uitroeptekens", " | ".join(excl[:3]))
            bad = [v for v in VERBODEN if v in low]
            add("FAIL" if bad else "PASS", f"{tag}: geen verboden woorden", ", ".join(bad))
            add("FAIL" if d["internalArrows"] else "PASS", f"{tag}: geen pijltjes in knoppen of interne links", ", ".join(d["internalArrows"][:4]))
            add("FAIL" if d["labels"] > 1 else "PASS", f"{tag}: hooguit één label boven een kop", str(d["labels"]))
            soft = [f'{i["src"]} {i["natural"]}px voor {i["shown"]}px' for i in d["imgs"] if i["complete"] and i["natural"] and i["natural"] < 1.9 * i["shown"]]
            add("WARN" if soft else "PASS", f"{tag}: beelden minstens 2x weergavebreedte", "; ".join(soft[:4]))
            all_links.update(h for h in d["links"] if h and h.startswith("/") and not h.startswith("//"))
            titles.setdefault(d["title"], []).append(path)
            add("PASS" if d["lang"].lower().startswith("nl") else "WARN", f"{tag}: paginataal Nederlands", d["lang"])
            add("PASS" if d["viewport"] else "FAIL", f"{tag}: viewport-meta aanwezig")
            add("FAIL" if d["noAlt"] else "PASS", f"{tag}: alt-tekst op inhoudelijke afbeeldingen", ", ".join(d["noAlt"][:4]))
            add("WARN" if d["badInputs"] else "PASS", f"{tag}: juiste invoertype voor telefoon en e-mail", ", ".join(d["badInputs"][:4]))
            if d["description"] and not (70 <= len(d["description"]) <= 170):
                add("WARN", f"{tag}: lengte meta description", f'{len(d["description"])} tekens (richtlijn 70 tot 165)')
            empty = [h for h in d["links"] if h in ("#", "", "javascript:void(0)")]
            add("FAIL" if empty else "PASS", f"{tag}: geen lege links", str(len(empty)))
            if d["canvas"]:
                add("WARN", f"{tag}: realtime 3D/canvas aanwezig", "menselijke check op echte Mac nodig: vloeiend, geen sprong, gelijk aan poster")
            if path == "/":
                og = d["ogImage"] or ""
                add("PASS" if og and not any(x in og for x in ("screenshot", "lovable.dev", "gpt-engineer", "opengraph-image")) else "FAIL", "OG-afbeelding eigen bestand", og[:80])
                add("PASS" if d["icons"] and not any("lovable" in i for i in d["icons"]) else "FAIL", "eigen favicon", ", ".join(d["icons"])[:80])
                add("PASS" if d["tel"] or d["mail"] else "WARN", "klikbaar telefoonnummer of e-mail")

        dubbel = {t: p for t, p in titles.items() if t and len(p) > 1}
        add("WARN" if dubbel else "PASS", "elke pagina een unieke title", "; ".join(f'"{t[:40]}" op {", ".join(p)}' for t, p in list(dubbel.items())[:3]))
        # interne links bestaan (van alle gecontroleerde pagina's)
        dead = []
        for h in sorted(all_links | {h for h in home["links"] if h and h.startswith("/") and not h.startswith("//")}):
            rr = await req.get(base + h.split("#")[0])
            if rr.status >= 400: dead.append(f"{h} ({rr.status})")
        add("FAIL" if dead else "PASS", "geen dode interne links", ", ".join(dead))
        add("FAIL" if errors else "PASS", "geen foutmeldingen in de console", " | ".join(sorted(set(errors))[:4]))
        await browser.close()

    fails = [r for r in results if r["status"] == "FAIL"]; warns = [r for r in results if r["status"] == "WARN"]
    for r in results:
        if r["status"] != "PASS": print(f'{r["status"]:4}  {r["check"]}  {r["detail"]}')
    print(f"\nKWALITEITSCHECK: {len(results) - len(fails) - len(warns)} PASS, {len(warns)} WARN, {len(fails)} FAIL")
    print("BADGE: zet in Project settings, Publishing, 'Hide Lovable badge' aan (niet automatisch te controleren in de sandbox)")
    sys.exit(1 if fails else 0)

asyncio.run(main())

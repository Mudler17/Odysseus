#!/usr/bin/env python3
"""
Lädt die in tools/images.json gelisteten Bilder von Wikimedia Commons,
prüft Lizenz und Motiv, verkleinert sie zu schlanken WebP-Dateien (img/)
und trägt Maße, Lizenzzeile und Bildnachweis in index.html ein.

Aufruf (im Repo-Wurzelverzeichnis):
    pip install pillow requests
    python tools/fetch_images.py

Figuren, für die kein passendes, frei lizenziertes Bild gefunden wird,
werden aus index.html entfernt – es bleiben also nie kaputte Bildverweise.
"""
import html
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tools" / "images.json"
HTML_FILE = ROOT / "index.html"
IMG_DIR = ROOT / "img"
REPORT = ROOT / "tools" / "image-report.md"

API = "https://commons.wikimedia.org/w/api.php"
UA = "OdysseusWiki-ImageFetcher/1.0 (https://odysseus-wiki.ki-kernel.de; github.com/Mudler17/Odysseus)"
S = requests.Session()
S.headers["User-Agent"] = UA


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def license_ok(meta: dict) -> bool:
    lic = (meta.get("License", {}).get("value") or "").lower()
    short = (meta.get("LicenseShortName", {}).get("value") or "").lower()
    txt = f"{lic} {short}"
    if any(bad in txt for bad in ("-nc", "-nd", " nc", " nd", "fair use", "non-free")):
        return False
    return (lic.startswith("pd") or lic.startswith("cc0") or lic.startswith("cc-by")
            or "public domain" in short or short.startswith("cc by") or short.startswith("cc0")
            or short.startswith("pd"))


def query(title: str, width: int):
    r = S.get(API, params={
        "action": "query", "format": "json", "formatversion": "2",
        "titles": f"File:{title}", "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": str(width),
    }, timeout=30)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or "imageinfo" not in pages[0]:
        return None
    return pages[0]["imageinfo"][0]


def to_webp(raw: bytes, width: int, max_kb: int, quality: int):
    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    q = quality
    while True:
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=q, method=6)
        if buf.tell() <= max_kb * 1024 or q <= 50:
            return buf.getvalue(), im.size, q
        q -= 6


def main() -> int:
    cfg = json.loads(MANIFEST.read_text(encoding="utf-8"))
    d = cfg["defaults"]
    IMG_DIR.mkdir(exist_ok=True)
    results, report = {}, ["# Bildbericht", "", "| Slot | Ergebnis | Datei | Größe | Lizenz |", "|---|---|---|---|---|"]

    for slot in cfg["slots"]:
        sid = slot["id"]
        width = slot.get("width", d["width"])
        max_kb = slot.get("max_kb", d["max_kb"])
        kws = [k.lower() for k in slot.get("keywords", [])]
        chosen, why = None, []
        for cand in slot["candidates"]:
            try:
                info = query(cand, width)
            except Exception as e:  # Netzfehler
                why.append(f"{cand}: Fehler {e}")
                continue
            time.sleep(0.5)
            if not info:
                why.append(f"{cand}: nicht gefunden")
                continue
            meta = info.get("extmetadata", {})
            if not license_ok(meta):
                why.append(f"{cand}: Lizenz nicht frei ({meta.get('LicenseShortName', {}).get('value')})")
                continue
            if kws:
                hay = " ".join([cand, strip_tags(meta.get("ImageDescription", {}).get("value", "")),
                                strip_tags(meta.get("ObjectName", {}).get("value", ""))]).lower()
                if not any(k in hay for k in kws):
                    why.append(f"{cand}: Motiv passt nicht (Stichwörter fehlen)")
                    continue
            chosen = (cand, info, meta)
            break

        if not chosen:
            results[sid] = None
            report.append(f"| {sid} | **übersprungen** | – | – | {'; '.join(why)} |")
            print(f"[--] {sid}: kein passendes Bild ({'; '.join(why)})")
            continue

        cand, info, meta = chosen
        url = info.get("thumburl") or info["url"]
        raw = S.get(url, timeout=60).content
        data, (w, h), q = to_webp(raw, width, max_kb, slot.get("quality", d["quality"]))
        (IMG_DIR / f"{sid}.webp").write_bytes(data)
        artist = strip_tags(meta.get("Artist", {}).get("value", "")) or "unbekannt"
        artist = re.sub(r"\s*\(.*?\)\s*$", "", artist)[:90]
        lic = meta.get("LicenseShortName", {}).get("value", "frei")
        lic_url = meta.get("LicenseUrl", {}).get("value", "")
        results[sid] = dict(file=cand, w=w, h=h, kb=round(len(data) / 1024), artist=artist,
                            lic=lic, lic_url=lic_url, page=info["descriptionurl"])
        report.append(f"| {sid} | ok | [{cand}]({info['descriptionurl']}) | {w}×{h}, {len(data)//1024} KB (q{q}) | {lic} |")
        print(f"[ok] {sid}: {cand} → {w}x{h}, {len(data)//1024} KB, {lic}")

    patch_html(results)
    total = sum(r["kb"] for r in results.values() if r)
    n_ok = sum(1 for r in results.values() if r)
    report += ["", f"**{n_ok} von {len(results)} Bildern eingebunden, zusammen {total} KB.**"]
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\n{n_ok}/{len(results)} Bilder, gesamt {total} KB")
    return 0


def credit_html(r: dict) -> str:
    lic = html.escape(r["lic"])
    if r["lic_url"]:
        lic = f'<a href="{html.escape(r["lic_url"])}">{lic}</a>'
    return (f'{html.escape(r["artist"])} · {lic} · '
            f'<a href="{html.escape(r["page"])}">Wikimedia Commons</a>')


def patch_html(results: dict) -> None:
    s = HTML_FILE.read_text(encoding="utf-8")
    fig_re = re.compile(r'\s*<figure class="pic[^"]*" data-img="([^"]+)">.*?</figure>', re.S)

    def fix(m):
        sid, block = m.group(1), m.group(0)
        r = results.get(sid)
        if sid not in results:
            return block
        if not r:
            return ""  # Figur entfernen – kein kaputter Bildverweis
        block = re.sub(r'width="\d*" height="\d*"', f'width="{r["w"]}" height="{r["h"]}"', block, count=1)
        block = re.sub(r'<span class="pc-l">.*?</span>', f'<span class="pc-l">{credit_html(r)}</span>', block, count=1, flags=re.S)
        return block

    s = fig_re.sub(fix, s)

    # Bildnachweis-Liste zwischen den Markern neu erzeugen
    titles = dict(re.findall(r'data-img="([^"]+)">.*?<span class="pc-t">(.*?)</span>', s, re.S))
    items = []
    for sid, r in results.items():
        if r and sid in titles:
            items.append(f'      <li><span class="bn-t">{titles[sid]}</span> <span class="bn-c">{credit_html(r)}</span></li>')
    block = "\n".join(items) if items else "      <li>Noch keine Bilder eingebunden.</li>"
    s = re.sub(r"(<!-- BILDNACHWEIS:START -->).*?(<!-- BILDNACHWEIS:END -->)",
               lambda m: f"{m.group(1)}\n{block}\n      {m.group(2)}", s, flags=re.S)
    HTML_FILE.write_text(s, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Buscador de discos de vinil.

Lê urls.txt e termos.txt, abre cada página de resultados num navegador
headless (Playwright), guarda os anúncios já vistos em vistos.json e gera
relatorios/vinil-AAAA-MM-DD.html só com os anúncios novos.

Uso:
    python3 buscar.py            # busca e gera o relatório do dia
    python3 buscar.py --login    # abre o navegador visível para logar na Shopee
"""

import argparse
import datetime as dt
import html
import json
import sys
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

PASTA = Path(__file__).resolve().parent
PERFIL = PASTA / ".perfil-navegador"
VISTOS = PASTA / "vistos.json"
RELATORIOS = PASTA / "relatorios"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Extratores rodam dentro da página e devolvem [{titulo, preco, link, foto, extra}].
JS_ENJOEI = """() => [...document.querySelectorAll('.c-product-card')].map(c => ({
  titulo: c.querySelector('.c-product-card__title')?.innerText.trim(),
  preco: c.querySelector('.c-product-card__price span')?.innerText.trim(),
  link: c.querySelector('a[href*="/p/"]')?.href.split('?')[0],
  foto: c.querySelector('img')?.src,
  extra: c.querySelector('.c-product-card__price-discount')?.innerText.trim()
    ? 'de ' + c.querySelector('.c-product-card__price-discount').innerText.trim() : '',
}))"""

JS_MESSIAS = """() => [...document.querySelectorAll('.card-news')].map(c => {
  const a = c.querySelector('a[href*="/lp/"], a[href*="sebodomessias.com.br/"]');
  const txt = c.innerText.replace(/\\s+/g, ' ');
  const preco = (txt.match(/R\\$\\s*[\\d.,]+/g) || []).pop() || '';
  const artista = (txt.match(/Artista:\\s*(.*?)\\s*Ano:/) || [])[1] || '';
  const estado = (txt.match(/Conservação:\\s*(.*?)\\s*(Disponibilizado|Ver detalhes|$)/) || [])[1] || '';
  return {
    titulo: c.querySelector('[title]')?.getAttribute('title') || a?.innerText.trim(),
    preco, link: a?.href, foto: c.querySelector('img')?.src,
    extra: [artista, estado].filter(s => s && s !== 'Desconhecido').join(' · '),
  };
})"""

JS_SHOPEE = """() => [...document.querySelectorAll('a[href*="-i."]')].map(a => {
  const txt = a.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
  const precoIdx = txt.findIndex(s => s === 'R$' || s.startsWith('R$'));
  let preco = precoIdx >= 0 ? txt[precoIdx] : '';
  if (preco === 'R$' && txt[precoIdx + 1]) preco = 'R$ ' + txt[precoIdx + 1];
  return {
    titulo: a.querySelector('img')?.alt || txt.find(s => s.length > 15) || txt[0],
    preco, link: a.href.split('?')[0], foto: a.querySelector('img')?.src, extra: '',
  };
})"""

# Leitor genérico para sites novos: qualquer link com foto e texto.
JS_GENERICO = """() => [...document.querySelectorAll('a[href]')].filter(a => a.querySelector('img')).map(a => {
  const box = a.closest('li, article, [class*="card"], [class*="product"], [class*="item"]') || a;
  const txt = box.innerText.replace(/\\s+/g, ' ').trim();
  return {
    titulo: a.querySelector('img').alt || a.title || txt.slice(0, 120),
    preco: (txt.match(/R\\$\\s*[\\d.,]+/) || [''])[0],
    link: a.href, foto: a.querySelector('img').src, extra: '',
  };
}).filter(i => i.titulo && i.titulo.length > 3)"""

SITES = {
    "enjoei.com.br": ("Enjoei", JS_ENJOEI),
    "sebodomessias.com.br": ("Sebo do Messias", JS_MESSIAS),
    "shopee.com.br": ("Shopee", JS_SHOPEE),
}


def ler_lista(nome):
    linhas = (PASTA / nome).read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in linhas if l.strip() and not l.strip().startswith("#")]


def montar_buscas():
    termos = ler_lista("termos.txt")
    buscas = []
    for url in ler_lista("urls.txt"):
        if "{termo}" in url:
            for t in termos:
                buscas.append((url.replace("{termo}", urllib.parse.quote_plus(t)), t))
        else:
            buscas.append((url, ""))
    return buscas


def site_de(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    for dominio, (nome, js) in SITES.items():
        if host.endswith(dominio):
            return nome, js
    return host.removeprefix("www."), JS_GENERICO


def coletar(pagina, url):
    pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
    pagina.wait_for_timeout(3000)
    if "/verify/" in pagina.url or "/buyer/login" in pagina.url:
        raise RuntimeError("login necessário — rode: python3 buscar.py --login")
    # Rola a página para carregar fotos e itens preguiçosos.
    for _ in range(6):
        pagina.mouse.wheel(0, 2500)
        pagina.wait_for_timeout(700)
    nome, js = site_de(url)
    itens = [i for i in pagina.evaluate(js) if i.get("link") and i.get("titulo")]
    return nome, itens


def gerar_html(hoje, novos, erros, total_vistos):
    por_site = {}
    for item in novos:
        por_site.setdefault(item["site"], []).append(item)

    def card(i):
        foto = f'<img src="{html.escape(i["foto"])}" loading="lazy" alt="">' if i.get("foto") else '<div class="sem-foto">♪</div>'
        return (f'<a class="card" href="{html.escape(i["link"])}" target="_blank" rel="noopener">{foto}'
                f'<div class="info"><div class="titulo">{html.escape(i["titulo"])}</div>'
                f'<div class="preco">{html.escape(i.get("preco") or "")}</div>'
                f'<div class="extra">{html.escape(i.get("extra") or "")}'
                f'{" · busca: " + html.escape(i["termo"]) if i.get("termo") else ""}</div></div></a>')

    secoes = "".join(
        f'<section><h2>{html.escape(site)} <span>{len(itens)}</span></h2>'
        f'<div class="grade">{"".join(card(i) for i in itens)}</div></section>'
        for site, itens in sorted(por_site.items()))
    if not novos:
        secoes = '<p class="vazio">Nenhum disco novo desde a última busca.</p>'
    avisos = "".join(f"<li>{html.escape(e)}</li>" for e in erros)
    avisos = f'<ul class="avisos">{avisos}</ul>' if avisos else ""

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vinil novo — {hoje:%d/%m/%Y}</title>
<style>
:root {{ --bg:#faf8f5; --card:#fff; --tx:#1d1b18; --mut:#77716a; --ac:#b4421f; --bd:#e7e2db; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16140f; --card:#211e18; --tx:#efe9e1; --mut:#a39b90; --ac:#f08a5d; --bd:#353028; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px 16px 48px; background:var(--bg); color:var(--tx); font:15px/1.4 -apple-system, system-ui, sans-serif; }}
main {{ max-width:1200px; margin:0 auto; }}
h1 {{ margin:0 0 4px; font-size:28px; }}
.sub {{ color:var(--mut); margin-bottom:24px; }}
h2 {{ font-size:19px; margin:32px 0 12px; border-bottom:1px solid var(--bd); padding-bottom:6px; }}
h2 span {{ background:var(--ac); color:#fff; border-radius:99px; padding:1px 9px; font-size:13px; vertical-align:middle; }}
.grade {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:14px; }}
.card {{ background:var(--card); border:1px solid var(--bd); border-radius:10px; overflow:hidden; color:inherit; text-decoration:none; display:flex; flex-direction:column; }}
.card:hover {{ border-color:var(--ac); }}
.card img, .sem-foto {{ width:100%; aspect-ratio:1; object-fit:cover; background:var(--bd); display:flex; align-items:center; justify-content:center; font-size:40px; color:var(--mut); }}
.info {{ padding:8px 10px 10px; }}
.titulo {{ font-weight:600; font-size:14px; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }}
.preco {{ color:var(--ac); font-weight:700; margin-top:4px; }}
.extra {{ color:var(--mut); font-size:12px; margin-top:2px; }}
.avisos {{ background:#fff3cd; color:#664d03; border-radius:8px; padding:10px 10px 10px 28px; }}
.vazio {{ color:var(--mut); font-size:17px; }}
</style></head><body><main>
<h1>Discos de vinil novos</h1>
<div class="sub">{hoje:%d/%m/%Y %H:%M} · {len(novos)} novo(s) · {total_vistos} anúncios acompanhados</div>
{avisos}{secoes}
</main></body></html>"""


def buscar():
    vistos = json.loads(VISTOS.read_text()) if VISTOS.exists() else {}
    primeira_vez = not vistos
    agora = dt.datetime.now()
    novos, erros = [], []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PERFIL), headless=True, user_agent=UA, locale="pt-BR",
            viewport={"width": 1366, "height": 900})
        pagina = ctx.new_page()
        for url, termo in montar_buscas():
            nome = site_de(url)[0]
            try:
                _, itens = coletar(pagina, url)
                print(f"{nome} [{termo or '-'}]: {len(itens)} anúncios", file=sys.stderr)
                if not itens:
                    erros.append(f"{nome} ({termo or url}): nenhum anúncio lido — o site pode ter mudado.")
            except Exception as e:
                erros.append(f"{nome} ({termo or url}): {str(e).splitlines()[0]}")
                continue
            for i in itens:
                if i["link"] in vistos:
                    continue
                vistos[i["link"]] = agora.isoformat(timespec="seconds")
                novos.append({**i, "site": nome, "termo": termo})
        ctx.close()

    VISTOS.write_text(json.dumps(vistos, ensure_ascii=False, indent=0))
    if primeira_vez:
        erros.insert(0, "Primeira execução: tudo o que foi encontrado aparece como novo.")
    RELATORIOS.mkdir(exist_ok=True)
    destino = RELATORIOS / f"vinil-{agora:%Y-%m-%d}.html"
    conteudo = gerar_html(agora, novos, erros, len(vistos))
    destino.write_text(conteudo, encoding="utf-8")
    (RELATORIOS / "ultimo.html").write_text(conteudo, encoding="utf-8")
    print(f"{len(novos)} novos → {destino}", file=sys.stderr)
    return destino


def login():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PERFIL), headless=False, user_agent=UA, locale="pt-BR")
        pagina = ctx.new_page()
        pagina.goto("https://shopee.com.br/buyer/login")
        input("Faça login na janela que abriu e depois tecle Enter aqui... ")
        ctx.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="abrir navegador para logar na Shopee")
    if ap.parse_args().login:
        login()
    else:
        buscar()

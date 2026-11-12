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
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PASTA = Path(__file__).resolve().parent
PERFIL = PASTA / ".perfil-navegador"
VISTOS = PASTA / "vistos.json"
RELATORIOS = PASTA / "relatorios"
MODELO = PASTA / "modelo-relatorio.html"
VERSAO = PASTA / "VERSAO"  # versão do código, mostrada no rodapé; sobe a cada commit de código
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

JS_ESTACAO = """() => [...document.querySelectorAll('a.prods_destaque2')].map(a => {
  const spans = [...a.querySelectorAll('span')].map(s => s.innerText.trim());
  const img = a.querySelector('img');
  const loja = spans.find(s => s.startsWith('Loja:')) || '';
  return {
    titulo: [spans[0], spans[1]].filter(Boolean).join(' — '),
    preco: a.querySelector('strong:not(.feat strong)')?.innerText.trim()
      || spans.find(s => s.startsWith('R$')) || '',
    link: a.href,
    // a foto fica em data-bg e, depois do lazy load, em style background-image
    foto: img && new URL(img.dataset.bg
      || (img.style.backgroundImage.match(/url\\(["']?(.*?)["']?\\)/) || [])[1]
      || img.src, location.href).href,
    extra: [spans.find(s => s.startsWith('Condição:')), loja.replace(/^Loja:\\s*(Sebo\\s*)?/, '')]
      .filter(Boolean).join(' · '),
  };
})"""

JS_CASA_LARANJA = """() => [...document.querySelectorAll('.js-item-product')].map(c => {
  const a = c.querySelector('a[href*="/produtos/"]');
  const img = c.querySelector('img');
  const de = c.querySelector('.js-compare-price-display')?.innerText.trim() || '';
  // fotos fora da tela ainda são um GIF provisório (lazy load): a URL real
  // fica em data-srcset/srcset, com vários tamanhos; pega um de ~640px
  const srcset = img?.getAttribute('data-srcset') || img?.getAttribute('srcset') || '';
  const tamanhos = srcset.split(',').map(s => s.trim().split(/\s+/))
    .filter(([u]) => u).map(([u, w]) => [u, parseInt(w) || 0]);
  const escolhido = tamanhos.find(([, w]) => w >= 600) || tamanhos[tamanhos.length - 1];
  const real = [escolhido?.[0], img?.currentSrc, img?.getAttribute('src')]
    .find(u => u && !u.startsWith('data:'));
  return {
    titulo: a?.getAttribute('title') || c.querySelector('.js-item-name')?.innerText.trim(),
    preco: c.querySelector('.js-price-display')?.innerText.trim(),
    link: a?.href,
    foto: real ? new URL(real, location.href).href : '',
    extra: de && de !== 'R$0,00' ? 'de ' + de : '',
  };
})"""

# Só a lista de resultados: no meio dela o site põe um carrossel de livros
# recomendados (.recommendations), que também usa .product-item.
JS_ESTANTE = """() => [...document.querySelectorAll('.product-list__items .product-item')]
  .filter(c => !c.closest('.recommendations')).map(c => {
  const a = c.querySelector('a.product-item__link');
  const img = c.querySelector('.product-item__image');
  // fotos ainda não carregadas guardam a URL em data-src; "indisponivel" é a capa genérica de sem foto
  const foto = [img?.getAttribute('data-src'), img?.currentSrc, img?.getAttribute('src')]
    .find(u => u && !u.startsWith('data:') && !/indisponivel|loading/.test(u));
  const texto = sel => c.querySelector(sel)?.innerText.replace(/\\s+/g, ' ').trim() || '';
  const titulo = a?.getAttribute('title') || texto('.product-item__name');
  // muitos itens vêm sem <a>, só com data-code; o site aceita qualquer nome antes do código,
  // mas o nome igual ao dele mantém o link igual ao dos discos já vistos
  const nome = titulo.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9\\s]/g, '').trim().split(/\\s+/).join('-');
  const codigo = c.getAttribute('data-code');
  // a foto só carrega quando o item aparece na tela; as que não carregaram saem do código
  const reserva = codigo && `https://static.estantevirtual.com.br/book/00/${codigo}/${codigo}_detail1.jpg?ims=fit-in/300x400/filters:fill(ffffff)`;
  return {
    titulo,
    preco: texto('.product-item__sale-price').replace(/\\u00a0/g, ' '),
    link: a?.href || (codigo ? `${location.origin}/livro/${nome}-grp-${codigo}` : ''),
    foto: foto ? new URL(foto, location.href).href : reserva || '',
    extra: [texto('.product-item__author'), texto('.product-item__year')].filter(Boolean).join(' · '),
  };
})"""

JS_SEBO_VINIL = """() => [...document.querySelectorAll('article.product-card')].map(c => {
  const a = c.querySelector('.product-card__title a') || c.querySelector('a.product-card__cover');
  const img = c.querySelector('.product-card__img');
  // textContent, não innerText: o site põe o artista em maiúsculas só pelo CSS
  const texto = sel => c.querySelector(sel)?.textContent.replace(/\\s+/g, ' ').trim() || '';
  const artista = texto('.product-card__artist'), disco = texto('.product-card__title');
  return {
    titulo: [artista, disco].filter(Boolean).join(' — '),
    preco: texto('.product-card__price').replace(/R\\$\\s*/, 'R$ '),
    link: a?.href,
    foto: img?.currentSrc || img?.getAttribute('src') || '',
    // ex.: "Outros · 1985 · Odeon · Excelente · capa excelente"
    extra: [texto('.product-card__meta'), texto('.product-card__grade')].filter(Boolean).join(' · '),
  };
})"""

# Sebo Nova Floresta: cada disco é um .produto_box_01; título "Artista - Disco",
# e embaixo gênero (categoria2) e Artista/Gravadora/Tipo.
JS_NOVA_FLORESTA = """() => [...document.querySelectorAll('.produto_box_01')].map(c => {
  const a = c.querySelector('a[href*="produtos-interna.php"]');
  const img = c.querySelector('.produto_box_01_01 img');
  const foto = img?.getAttribute('src') || '';
  const texto = sel => c.querySelector(sel)?.textContent.replace(/\\s+/g, ' ').trim() || '';
  const genero = c.querySelector('.produto_categoria a + a')?.textContent.trim() || '';
  const ficha = Object.fromEntries([...c.querySelectorAll('.produto_box_01_02 strong')]
    .map(s => [s.textContent.trim(), (s.nextSibling?.textContent || '').replace(/^[:\\s]+/, '').trim()]));
  return {
    titulo: texto('.produto_titulo'),
    preco: texto('.produto_valor'),
    link: a?.href,
    foto: foto && !foto.includes('sem-foto') ? new URL(foto, location.href).href : '',
    // ex.: "Cantor Nacional · Kuarup · Usado"
    extra: [genero, ficha['Gravadora'], ficha['Tipo']].filter(Boolean).join(' · '),
  };
})"""

# Marcondes Discos (WooCommerce): em vez da página, que só mostra 18 discos,
# lê a API pública da loja (Store API), que devolve os mais novos em JSON.
JS_MARCONDES = """() => {
  const sem_html = t => new DOMParser().parseFromString(t || '', 'text/html').body.textContent.trim();
  const reais = (v, casas) => 'R$ ' + (Number(v) / 10 ** casas).toLocaleString('pt-BR', {minimumFractionDigits: 2});
  return JSON.parse(document.body.innerText).filter(p => p.is_in_stock).map(p => {
    const pr = p.prices, casas = pr.currency_minor_unit;
    const attr = nome => p.attributes.find(a => a.name === nome)?.terms.map(t => t.name).join(', ') || '';
    return {
      titulo: sem_html(p.name),
      preco: reais(pr.price, casas),
      link: p.permalink,
      foto: p.images[0]?.thumbnail || p.images[0]?.src || '',
      // ex.: "forró · Guriatã · Usado"
      extra: [p.tags.map(t => sem_html(t.name)).join(', '), attr('Gravadora'), attr('Condição'),
              p.on_sale ? 'de ' + reais(pr.regular_price, casas) : ''].filter(Boolean).join(' · '),
    };
  });
}"""

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
    "estacaocultural.com.br": ("Estação Cultural", JS_ESTACAO),
    "sebocasalaranja.com.br": ("Sebo Casa Laranja", JS_CASA_LARANJA),
    # mesma plataforma de loja do Casa Laranja (Nuvemshop), mesmo leitor
    "madeinquebradadiscos.com.br": ("Made in Quebrada", JS_CASA_LARANJA),
    "estantevirtual.com.br": ("Estante Virtual", JS_ESTANTE),
    "sebodovinil.com.br": ("Sebo do Vinil", JS_SEBO_VINIL),
    "sebonovafloresta.com.br": ("Sebo Nova Floresta", JS_NOVA_FLORESTA),
    "marcondesdiscos.com.br": ("Marcondes Discos", JS_MARCONDES),
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


def ler_credenciais():
    arquivo = PASTA / "credenciais.txt"
    if not arquivo.exists():
        return None, None
    dados = dict(l.split("=", 1) for l in ler_lista("credenciais.txt") if "=" in l)
    return dados.get("shopee_usuario", "").strip() or None, dados.get("shopee_senha", "").strip() or None


def ler_config(nome):
    if not (PASTA / nome).exists():
        return {}
    return {k.strip(): v.strip() for k, v in (l.split("=", 1) for l in ler_lista(nome) if "=" in l)}


def publicar(conteudo, hoje):
    """Commita o relatório como index.html neste mesmo repositório (o GitHub
    Pages serve a raiz) e envia ao GitHub. Devolve o endereço público, ou
    None se publicacao.txt não tiver a página ou se o envio falhar."""
    pagina = ler_config("publicacao.txt").get("pagina")
    if not pagina:
        return None
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # nunca travar pedindo senha
    git = lambda *a: subprocess.run(["git", "-C", str(PASTA), *a], check=True, env=env,
                                    capture_output=True, text=True)
    arquivos = ["index.html", "arquivo.html", "busca.json", ".nojekyll", "relatorios", "vistos.json"]
    try:
        # traz o que houver de novo no GitHub; mudanças locais em andamento são preservadas
        git("pull", "--rebase", "--autostash", "origin", "main")
        (PASTA / "index.html").write_text(conteudo, encoding="utf-8")  # o pull pode tê-lo trocado
        (PASTA / ".nojekyll").touch()  # serve o HTML como está, sem Jekyll
        git("add", "--", *arquivos)
        # commita só os arquivos do relatório, nunca outras alterações em andamento
        if git("diff", "--cached", "--name-only", "--", *arquivos).stdout.strip():
            git("-c", "user.name=Dig Dig Dig!", "-c", "user.email=digdigdig@users.noreply.github.com",
                "commit", "-m", f"Relatório de {hoje:%d/%m/%Y %H:%M}", "--", *arquivos)
        git("push", "origin", "HEAD:main")  # envia também commits de código ainda não enviados
        print(f"publicado → {pagina}", file=sys.stderr)
        return pagina
    except subprocess.CalledProcessError as e:
        print(f"falha ao publicar: {(e.stderr or e.stdout or str(e)).strip()}", file=sys.stderr)
        return None


def notificar(novos, erros, link=None):
    """Avisa no celular pelo ntfy (https://ntfy.sh) que o relatório saiu;
    tocar no aviso abre a página publicada, se houver.
    O canal fica em notificacao.txt; sem o arquivo, não faz nada."""
    dados = ler_config("notificacao.txt")
    topico = dados.get("ntfy_topico", "")
    if not topico:
        return
    servidor = dados.get("ntfy_servidor", "https://ntfy.sh").rstrip("/")
    sites = sorted({i["site"] for i in novos})
    texto = (f"{len(novos)} disco(s) novo(s) em {', '.join(sites)}." if novos
             else "Nenhum disco novo hoje.")
    if erros:
        texto += f" {len(erros)} aviso(s) no relatório."
    pedido = urllib.request.Request(
        # título vai na URL: cabeçalhos HTTP não levam acentos
        f"{servidor}/{urllib.parse.quote(topico)}?"
        + urllib.parse.urlencode({"title": "Dig Dig Dig! · relatório pronto", "tags": "cd",
                                  **({"click": link} if link else {})}),
        data=texto.encode("utf-8"), method="POST")
    try:
        urllib.request.urlopen(pedido, timeout=20).close()
        print("notificação enviada", file=sys.stderr)
    except Exception as e:
        print(f"falha ao notificar: {e}", file=sys.stderr)


def pede_login(pagina):
    return "/verify/" in pagina.url or "/buyer/login" in pagina.url


def logar_shopee(pagina, usuario, senha):
    """Preenche o login da Shopee. Não resolve captcha nem código por SMS:
    se a Shopee pedir verificação, é preciso logar à mão com --login."""
    pagina.goto("https://shopee.com.br/buyer/login", wait_until="domcontentloaded", timeout=60000)
    pagina.fill('input[name="loginKey"]', usuario, timeout=20000)
    pagina.fill('input[name="password"]', senha)
    pagina.press('input[name="password"]', "Enter")
    pagina.wait_for_timeout(8000)
    return not pede_login(pagina)


def coletar(pagina, url):
    pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
    pagina.wait_for_timeout(3000)
    if pede_login(pagina):
        usuario, senha = ler_credenciais()
        if not (usuario and senha):
            raise RuntimeError("login necessário — preencha credenciais.txt ou rode: python3 buscar.py --login")
        if not logar_shopee(pagina, usuario, senha):
            raise RuntimeError("a Shopee pediu verificação extra (captcha/código) — rode: python3 buscar.py --login")
        pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
        pagina.wait_for_timeout(3000)
        if pede_login(pagina):
            raise RuntimeError("login não aceito — confira credenciais.txt ou rode: python3 buscar.py --login")
    # Rola a página para carregar fotos e itens preguiçosos.
    for _ in range(6):
        pagina.mouse.wheel(0, 2500)
        pagina.wait_for_timeout(700)
    nome, js = site_de(url)
    itens = [i for i in pagina.evaluate(js) if i.get("link") and i.get("titulo")]
    return nome, itens


def valor(preco):
    """'R$ 1.234,56' -> '1234.56' (vazio se não houver preço)."""
    m = re.search(r"R\$\s*([\d.]+(?:,\d+)?)", preco or "")
    return m.group(1).replace(".", "").replace(",", ".") if m else ""


def gerar_html(hoje, novos, erros, total_vistos):
    por_site = {}
    for item in novos:
        por_site.setdefault(item["site"], []).append(item)
    sites = sorted({site_de(u)[0] for u, _ in montar_buscas()} | set(por_site))
    # cada site tem sempre o mesmo tom de fundo (pela posição na lista de sites)
    tom = lambda site: f"tom-{sites.index(site) % 6}"

    def card(i):
        foto = f'<img src="{html.escape(i["foto"])}" loading="lazy" alt="">' if i.get("foto") else '<div class="sem-foto"></div>'
        extra = i.get("extra") or ""
        return (f'<a class="card {tom(i["site"])}" href="{html.escape(i["link"])}" target="_blank" rel="noopener" '
                f'data-site="{html.escape(i["site"])}" data-preco="{valor(i.get("preco"))}">'
                f'<div class="capa">{foto}</div>'
                f'<div class="info"><div class="site">{html.escape(i["site"])}</div>'
                f'<div class="titulo">{html.escape(i["titulo"])}</div>'
                f'<div class="preco">{html.escape(i.get("preco") or "")}</div>'
                f'<div class="extra">{html.escape(extra)}</div></div></a>')

    def teclas(chave, rotulo, opcoes):
        botoes = "".join(f'<button type="button" data-valor="{html.escape(v)}" aria-pressed="{str(not v).lower()}">'
                         f'{html.escape(t)}</button>' for v, t in opcoes)
        return f'<div class="grupo"><span>{rotulo}</span><div class="teclas" data-chave="{chave}">{botoes}</div></div>'

    def menu(chave, rotulo, opcoes):
        itens = "".join(f'<option value="{html.escape(v)}">{html.escape(t)}</option>' for v, t in opcoes)
        return (f'<div class="grupo"><label for="menu-{chave}">{rotulo}</label>'
                f'<select class="menu" id="menu-{chave}" data-chave="{chave}">{itens}</select></div>')

    secoes = "".join(
        f'<section class="{tom(site)}" data-site="{html.escape(site)}"><h2>{html.escape(site)} <span>{len(itens):03d}</span></h2>'
        f'<div class="grade">{"".join(card(i) for i in itens)}</div></section>'
        for site, itens in sorted(por_site.items()))
    controles = ""
    if novos:
        controles = ('<div class="controles">'
                     + menu("site", "Site", [("", "Todos os sites")] + [(s, s) for s in sorted(por_site)])
                     + teclas("faixa", "Preço", [("", "Todos"), ("ate50", "Até R$ 50"),
                                                 ("51a100", "R$ 51 a 100"), ("mais100", "Mais de R$ 101")])
                     + teclas("ordem", "Ordem", [("", "Por site"), ("asc", "Menor preço"), ("desc", "Maior preço")])
                     + '</div><div id="lista" class="grade" hidden></div>')
    else:
        secoes = '<p class="vazio">Nenhum disco novo desde a última busca.</p>'
    avisos = "".join(f"<li>{html.escape(e)}</li>" for e in erros)
    avisos = f'<ul class="avisos">{avisos}</ul>' if avisos else ""
    pecas = {
        "DATA": f"{hoje:%d.%m.%Y}", "HORA": f"{hoje:%H:%M}", "NOVOS": str(len(novos)),
        "VISTOS": str(total_vistos), "AVISOS": avisos, "CONTROLES": controles,
        "SECOES": secoes, "SITES": html.escape(" · ".join(sites)),
        "GATILHO": html.escape(ler_config("publicacao.txt").get("gatilho", "")),
        "VERSAO": html.escape(VERSAO.read_text().strip() if VERSAO.exists() else "?"),
    }
    conteudo = MODELO.read_text(encoding="utf-8")
    for chave, texto in pecas.items():
        conteudo = conteudo.replace("{{" + chave + "}}", texto)
    return conteudo


# Cópias arquivadas ficam em relatorios/: sem o botão Dig, com o caminho de volta ao Arquivo.
ESTILO_ARQUIVADO = ("<style>.dig{display:none!important}.topo .tema{display:none}"
                    ".topo{justify-content:space-between}"
                    ".arquivo-nav{margin-left:auto;font-size:15px;font-weight:500;color:inherit;text-decoration:none}"
                    ".arquivo-nav:hover{text-decoration:underline}</style>")


def arquivar(conteudo):
    """Transforma uma página de Dig na sua cópia do Arquivo (vale também para
    páginas feitas com versões antigas do modelo)."""
    conteudo = re.sub(r'data-gatilho="[^"]*"', 'data-gatilho=""', conteudo)
    conteudo = conteudo.replace("</style>", "</style>" + ESTILO_ARQUIVADO, 1)
    if 'href="arquivo.html"' in conteudo:  # modelo atual: o link já existe no topo
        return conteudo.replace('href="arquivo.html">Arquivo</a>', 'href="../arquivo.html">← Arquivo</a>')
    return re.sub(r"<h1>(.*?)</h1>", r'<h1>\1</h1><a class="arquivo-nav" href="../arquivo.html">← Arquivo</a>',
                  conteudo, count=1, flags=re.S)


def dados_do_dig(arquivo):
    """Lê de uma página de Dig quando ele foi feito, quantos discos e de quais sites."""
    texto = arquivo.read_text(encoding="utf-8")
    quando = re.search(r"Atualizado em (\d\d)\.(\d\d)\.(\d{4}) às (\d\d):(\d\d)", texto)
    if not quando:
        return None
    d, m, a, h, mi = map(int, quando.groups())
    novos = re.search(r"(\d+) novos", texto)
    sites = list(dict.fromkeys(re.findall(r'<section[^>]*data-site="([^"]+)"', texto)))
    return {"quando": dt.datetime(a, m, d, h, mi), "novos": int(novos.group(1)) if novos else 0,
            "sites": [html.unescape(x) for x in sites], "arquivo": arquivo.name}


CARTAO = re.compile(r'<a class="card[^"]*" href="([^"]*)"[^>]*data-site="([^"]*)"[^>]*>(.*?)</a>', re.S)


def gerar_indice():
    """Todos os discos de todos os Digs, para a busca da página do Arquivo
    (busca.json). Cada disco aparece uma vez, com o Dig em que surgiu."""
    discos = {}
    digs = sorted(filter(None, map(dados_do_dig, RELATORIOS.glob("vinil-*.html"))), key=lambda x: x["quando"])
    for dig in digs:
        texto = (RELATORIOS / dig["arquivo"]).read_text(encoding="utf-8")
        for link, site, miolo in CARTAO.findall(texto):
            if link in discos:
                continue
            campo = lambda classe: html.unescape(re.sub(r"<[^>]+>", "", (re.search(
                rf'<div class="{classe}">(.*?)</div>', miolo, re.S) or [None, ""])[1])).strip()
            foto = re.search(r'<img src="([^"]*)"', miolo)
            discos[link] = {"t": campo("titulo"), "p": campo("preco"), "s": html.unescape(site),
                            "l": html.unescape(link), "f": html.unescape(foto[1]) if foto else "",
                            "d": f'{dig["quando"]:%d.%m.%Y às %H:%M}', "a": f'relatorios/{dig["arquivo"]}'}
    return json.dumps(list(reversed(discos.values())), ensure_ascii=False, separators=(",", ":"))


def gerar_arquivo():
    """Página do Arquivo: todos os Digs anteriores ao atual, do mais novo ao mais antigo."""
    digs = sorted(filter(None, map(dados_do_dig, RELATORIOS.glob("vinil-*.html"))),
                  key=lambda x: x["quando"], reverse=True)[1:]  # o primeiro é o Dig atual (index.html)
    linhas = "".join(
        f'<li><a href="relatorios/{html.escape(x["arquivo"])}">'
        f'<span class="quando">{x["quando"]:%d.%m.%Y}<small>{x["quando"]:%H:%M}</small></span>'
        f'<span class="quantos">{x["novos"]} disco{"s" if x["novos"] != 1 else ""}</span>'
        f'<span class="onde">{html.escape(" · ".join(x["sites"]))}</span></a></li>'
        for x in digs)
    lista = f'<ol class="lista-arquivo">{linhas}</ol>' if digs else '<p class="vazio">Nenhum Dig arquivado ainda.</p>'
    busca = (MODELO.parent / "modelo-busca.html").read_text(encoding="utf-8")
    modelo = MODELO.read_text(encoding="utf-8")
    cabeca = modelo[:modelo.index("<body>")].replace("<title>Dig Dig Dig!</title>", "<title>Arquivo · Dig Dig Dig!</title>")
    versao = html.escape(VERSAO.read_text().strip() if VERSAO.exists() else "?")
    return (cabeca + '<body><main>\n'
            '<header class="topo"><h1><a href="./" class="logo">Dig Dig Dig!</a></h1>'
            '<a class="link-topo" href="./">← Dig atual</a></header>\n'
            f'<div class="barra"><div><b>Arquivo</b><span class="mut">{len(digs)} Dig{"s" if len(digs) != 1 else ""} anteriores</span></div></div>\n'
            + busca.replace("{{LISTA}}", lista) +
            f'\n<footer><div><strong>Dig Dig Dig!</strong><span class="versao">v{versao}</span></div></footer>\n'
            '</main></body></html>\n')


PROGRESSO = PASTA / "logs" / "progresso.json"  # o servidor lê para a barra da página


def contar(etapa, feitas=0, total=0, site=""):
    """Anota em que ponto o Dig está: qual busca, de quantas, em qual site."""
    try:
        PROGRESSO.parent.mkdir(exist_ok=True)
        PROGRESSO.write_text(json.dumps({"etapa": etapa, "feitas": feitas, "total": total, "site": site,
                                         "quando": dt.datetime.now().isoformat(timespec="seconds")},
                                        ensure_ascii=False))
    except OSError:
        pass


def buscar():
    vistos = json.loads(VISTOS.read_text()) if VISTOS.exists() else {}
    primeira_vez = not vistos
    agora = dt.datetime.now()
    novos, erros = [], []
    buscas = montar_buscas()
    contar("buscando", 0, len(buscas), site_de(buscas[0][0])[0] if buscas else "")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PERFIL), headless=True, user_agent=UA, locale="pt-BR",
            viewport={"width": 1366, "height": 900})
        pagina = ctx.new_page()
        for n, (url, termo) in enumerate(buscas):
            nome = site_de(url)[0]
            contar("buscando", n, len(buscas), nome)
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
    destino = RELATORIOS / f"vinil-{agora:%Y-%m-%d-%H%M}.html"  # um por Dig, para o Arquivo
    conteudo = gerar_html(agora, novos, erros, len(vistos))
    destino.write_text(arquivar(conteudo), encoding="utf-8")
    (PASTA / "index.html").write_text(conteudo, encoding="utf-8")  # a página do servidor local
    (PASTA / "arquivo.html").write_text(gerar_arquivo(), encoding="utf-8")
    (PASTA / "busca.json").write_text(gerar_indice(), encoding="utf-8")
    print(f"{len(novos)} novos → {destino}", file=sys.stderr)
    contar("publicando", len(buscas), len(buscas))
    link = publicar(conteudo, agora)
    notificar(novos, [e for e in erros if not e.startswith("Primeira execução")], link)
    return destino


def login():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PERFIL), headless=False, user_agent=UA, locale="pt-BR")
        pagina = ctx.new_page()
        pagina.goto("https://shopee.com.br/buyer/login")
        usuario, senha = ler_credenciais()
        if usuario and senha:
            pagina.fill('input[name="loginKey"]', usuario, timeout=20000)
            pagina.fill('input[name="password"]', senha)
        input("Faça login na janela que abriu (complete a verificação, se houver) e tecle Enter aqui... ")
        ctx.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="abrir navegador para logar na Shopee")
    if ap.parse_args().login:
        login()
    else:
        buscar()

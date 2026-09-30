# Arma el manual de procesos desde los Google Docs, lo cifra y escribe index.html.
# Uso local: python scripts/build.py   (lee datos_trabajo/docs.json y datos_trabajo/clave.txt)
# En GitHub Actions: lee los secretos DOCS_JSON y CLAVE (variables de entorno).
# Solo reescribe index.html si cambió el contenido de algún documento.
import os, re, json, base64, hmac, hashlib, time, html, urllib.request, urllib.parse
from html.parser import HTMLParser
from datetime import datetime, timezone, timedelta
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(RAIZ, 'scripts')
ITER = 300000


def leer_config():
    docs = os.environ.get('DOCS_JSON')
    clave = os.environ.get('CLAVE')
    if not docs:
        docs = open(os.path.join(RAIZ, 'datos_trabajo', 'docs.json'), encoding='utf8').read()
    if not clave:
        clave = open(os.path.join(RAIZ, 'datos_trabajo', 'clave.txt'), encoding='utf8').read()
    return json.loads(docs), clave.strip()


def bajar(doc_id):
    url = f'https://docs.google.com/document/d/{doc_id}/export?format=html'
    with urllib.request.urlopen(url, timeout=60) as r:
        t = r.read().decode('utf8')
    if 'doc-content' not in t:
        raise RuntimeError(f'No se pudo leer el documento {doc_id} (¿está compartido con "cualquiera con el enlace"?)')
    return t


def clases_formato(t):
    css = t[t.find('<style'):t.find('</style>')]
    neg, cur, sub = set(), set(), set()
    for nombre, cuerpo in re.findall(r'\.(c\d+)\{([^}]*)\}', css):
        if 'font-weight:700' in cuerpo: neg.add(nombre)
        if 'font-style:italic' in cuerpo: cur.add(nombre)
        if 'text-decoration:underline' in cuerpo: sub.add(nombre)
    return neg, cur, sub


def limpiar_link(href):
    if href.startswith('https://www.google.com/url?'):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get('q')
        if q: return q[0]
    return href


class Limpiador(HTMLParser):
    """Pasa el HTML de Google Docs a HTML simple (sin estilos), manteniendo negrita, listas, tablas e imágenes."""
    BLOQUES = {'p', 'ol', 'ul', 'li', 'table', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'sup', 'sub'}

    def __init__(self, neg, cur, sub):
        super().__init__(convert_charrefs=True)
        self.neg, self.cur, self.sub = neg, cur, sub
        self.out, self.pila, self.dentro, self.saltar = [], [], False, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'body': self.dentro = True; return
        if not self.dentro: return
        if tag in ('style', 'script'): self.saltar += 1; return
        if tag == 'span':
            cl = set((a.get('class') or '').split())
            envs = [x for x, s in (('b', self.neg), ('i', self.cur), ('u', self.sub)) if cl & s]
            self.out.append(''.join(f'<{x}>' for x in envs)); self.pila.append(envs); return
        if tag == 'br': self.out.append('<br>'); return
        if tag == 'img':
            src = a.get('src', '')
            m = re.search(r'width:\s*([\d.]+)px', a.get('style', ''))
            ancho = f' width="{round(float(m.group(1)))}"' if m else ''
            self.out.append(f'<img src="{html.escape(src)}"{ancho} alt="">'); return
        if tag not in self.BLOQUES: return
        extra = ''
        if tag == 'ol' and a.get('start') and a['start'] != '1': extra = f' start="{a["start"]}"'
        if tag in ('td', 'th'):
            for k in ('colspan', 'rowspan'):
                if a.get(k) and a[k] != '1': extra += f' {k}="{a[k]}"'
        if tag == 'a':
            href = limpiar_link(a.get('href', ''))
            if href.startswith('#'): extra = ''
            else: extra = f' href="{html.escape(href)}" target="_blank" rel="noopener"'
        self.out.append(f'<{tag}{extra}>')

    def handle_endtag(self, tag):
        if tag == 'body': self.dentro = False; return
        if not self.dentro: return
        if tag in ('style', 'script'): self.saltar -= 1; return
        if tag == 'span':
            envs = self.pila.pop() if self.pila else []
            self.out.append(''.join(f'</{x}>' for x in reversed(envs))); return
        if tag in self.BLOQUES: self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if self.dentro and not self.saltar:
            self.out.append(html.escape(data.replace('\xa0', ' '), quote=False))


def convertir(t):
    p = Limpiador(*clases_formato(t)); p.feed(t)
    h = ''.join(p.out)
    for _ in range(3):
        h = re.sub(r'<(b|i|u)>(\s*)</\1>', r'\2', h)
        h = re.sub(r'<p>\s*</p>', '', h)
    h = re.sub(r'</(b|i|u)><\1>', '', h)
    return h


def texto_plano(h):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', h))).strip()


def metadatos(h):
    tx = texto_plano(h)
    def buscar(pat):
        m = re.search(pat, tx, re.I); return m.group(1).strip() if m else ''
    titulo = buscar(r'^(.+?)\s+Control de documentos') or tx[:80]
    return {
        'titulo': titulo,
        'codigo': buscar(r'C[óo]digo\s*:\s*(\S+)'),
        'version': buscar(r'Versi[óo]n\s*:\s*([\w.]+)'),
        'fecha': buscar(r'Fecha(?: de)? publicaci[óo]n\s*:\s*([\d/.-]+)'),
    }


def main():
    docs, clave = leer_config()
    items = []
    for i, d in enumerate(docs):
        h = convertir(bajar(d['id']))
        m = metadatos(h)
        if d.get('titulo'): m['titulo'] = d['titulo']
        items.append({**m, 'n': i, 'area': d.get('area', 'General'), 'id': d['id'], 'html': h})
    items.sort(key=lambda x: (x['area'], x['titulo']))

    # Huella del contenido (HMAC con la clave: no revela nada del texto). Si no cambió, no se publica.
    huella = hmac.new(clave.encode(), json.dumps(items, ensure_ascii=False, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    arch_huella = os.path.join(RAIZ, 'huella.txt')
    previa = open(arch_huella).read().strip() if os.path.exists(arch_huella) else ''
    if huella == previa and not os.environ.get('FORZAR'):
        print('Sin cambios en los documentos.'); return

    ahora = datetime.now(timezone(timedelta(hours=-6))).strftime('%d/%m/%Y %H:%M')  # hora CDMX
    plantilla = open(os.path.join(SCRIPTS, 'plantilla.html'), encoding='utf8').read()
    fuente = plantilla.replace('__DOCS__', json.dumps(items, ensure_ascii=False).replace('</', '<\\/')).replace('__ACTUALIZADO__', ahora)

    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(hashes.SHA256(), 32, salt, ITER).derive(clave.encode())
    ct = AESGCM(key).encrypt(iv, fuente.encode(), None)
    blob = json.dumps({'s': base64.b64encode(salt).decode(), 'i': base64.b64encode(iv).decode(), 'n': ITER, 'c': base64.b64encode(ct).decode()})
    page = open(os.path.join(SCRIPTS, 'candado.html'), encoding='utf8').read().replace('__BLOB__', blob)
    open(os.path.join(RAIZ, 'index.html'), 'w', encoding='utf8').write(page)
    open(arch_huella, 'w').write(huella + '\n')
    if os.environ.get('VISTA_PREVIA'):  # solo local: copia sin cifrar para revisar (datos_trabajo está en .gitignore)
        open(os.path.join(RAIZ, 'datos_trabajo', 'vista_previa.html'), 'w', encoding='utf8').write(fuente)
    print(f'Manual actualizado: {len(items)} documento(s), {len(page)//1024} KB')


if __name__ == '__main__':
    main()

"""Build a single self-contained HTML showcase of the Mini App (demo mode, phone frame).

    python tools/build_demo.py  ->  dist/vibecloud-demo.html
"""
import base64, html, io, re
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "webapp"
OUT = ROOT / "dist" / "vibecloud-demo.html"


def b64(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def jpeg(path: Path, size: int, q: int = 74) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def fonts_css() -> str:
    css = (WEB / "css/fonts.css").read_text()
    # drop latin-ext blocks to keep the file light
    css = re.sub(r"@font-face\s*{[^}]*latin-ext[^}]*}", "", css)
    return re.sub(r"url\(\.\./fonts/([^)]+)\)", lambda m: f"url({b64(WEB / 'fonts' / m.group(1), 'font/woff2')})", css)


def app_css(imgs: dict) -> str:
    css = (WEB / "css/app.css").read_text()
    return re.sub(r"url\(\.\./img/([a-z]+)\.jpg\)", lambda m: f"url({imgs[m.group(1)]})", css)


def main() -> None:
    imgs = {n: jpeg(WEB / "img" / f"{n}.jpg", 520, 76) for n in ("hero", "tower", "smoke")}
    covers = {p.stem: jpeg(p, 300, 72) for p in sorted((WEB / "img/demo").glob("*.jpg"))}
    covers_js = "window.VC_ASSETS=" + repr(covers).replace("'", '"') + ";window.VC_TOUR=true;window.VC_TOUR_DELAY=3200;"
    app_js = (WEB / "js/app.js").read_text().replace('src="img/hero.jpg"', f'src="{imgs["hero"]}"')
    demo_js = (WEB / "js/demo.js").read_text()

    page = (WEB / "index.html").read_text()
    page = re.sub(r'<link rel="preload"[^>]+>\n', "", page)
    page = re.sub(r'<script src="https://telegram.org[^"]+"></script>\n', "", page)
    page = page.replace('<link rel="stylesheet" href="css/fonts.css">', f"<style>{fonts_css()}</style>")
    page = page.replace('<link rel="stylesheet" href="css/app.css">', f"<style>{app_css(imgs)}</style>")
    page = page.replace('<script src="js/demo.js"></script>', f"<script>{covers_js}\n{demo_js}</script>")
    page = page.replace('<script src="js/app.js"></script>', f"<script>{app_js}</script>")
    assert "src=\"js/" not in page and "css/app.css" not in page

    shell = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VibeCloud — демо Mini App</title>
<style>
  html,body{{margin:0;height:100%;min-height:480px;background:#050507;color:#f4f2ee;font:500 14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow:hidden}}
  body{{display:flex;align-items:center;justify-content:center;gap:48px;
    background:radial-gradient(60% 50% at 20% 20%,rgba(91,43,214,.35),transparent 70%),radial-gradient(50% 50% at 85% 80%,rgba(35,53,232,.3),transparent 70%),#050507}}
  .stage{{position:relative;width:410px;height:864px;flex:none;transform-origin:center}}
  .phone{{position:absolute;inset:0;border-radius:58px;padding:10px;background:linear-gradient(145deg,#2a2a31,#0c0c0f);box-shadow:0 40px 120px rgba(0,0,0,.6),inset 0 0 0 1px rgba(255,255,255,.08)}}
  .screen{{width:390px;height:844px;border-radius:48px;overflow:hidden;background:#09090c;isolation:isolate;transform:translateZ(0);-webkit-mask-image:-webkit-radial-gradient(white,black)}}
  .phone iframe{{width:390px;height:844px;border:0;background:#09090c;display:block;color-scheme:dark}}
  .side{{max-width:300px}}
  .side h1{{margin:0 0 6px;font:900 54px/.9 Impact,"Arial Narrow",sans-serif;letter-spacing:.01em;text-transform:uppercase}}
  .side h1 span{{color:#d6f35b}}
  .side p{{margin:0 0 18px;color:rgba(244,242,238,.66)}}
  .side button{{display:block;width:100%;margin:0 0 8px;padding:12px 16px;border-radius:14px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.06);
    color:#f4f2ee;font:700 14px system-ui,sans-serif;text-align:left;cursor:pointer;transition:transform .2s,background .2s}}
  .side button:hover{{background:rgba(214,243,91,.14)}} .side button:active{{transform:scale(.97)}}
  .side button b{{color:#d6f35b;margin-right:8px}}
  @media (max-width:820px){{.side{{display:none}}}}
</style></head><body>
<div class="side">
  <h1>Vibe<br><span>Cloud</span></h1>
  <p>Демо Mini App: вымышленная медиатека, перенос симулируется. Можно нажимать всё — треки, плеер, микстейпы.</p>
  <button data-msg='{{"vc":"import","url":"https://soundcloud.com/highway-ghosts/sets/night-drive-2"}}'><b>01</b>Перенести плейлист</button>
  <button data-msg='{{"vc":"import","url":"https://soundcloud.com/vanta-club/glass-pills"}}'><b>02</b>Перенести трек</button>
  <button data-msg='{{"vc":"profile","url":"https://soundcloud.com/kairo-lights"}}'><b>03</b>Ссылка на профиль</button>
  <button data-msg='{{"vc":"go","view":"mixtapes"}}'><b>04</b>Микстейпы</button>
</div>
<div class="stage" id="stage"><div class="phone"><div class="screen"><iframe id="app" title="VibeCloud" srcdoc="{html.escape(page, quote=True)}"></iframe></div></div></div>
<script>
  const st=document.getElementById('stage'),fr=document.getElementById('app');
  const fit=()=>{{const k=Math.max(.5,Math.min(1,(Math.max(innerHeight,480)-24)/864,(innerWidth-16)/410));st.style.transform='scale('+k+')';st.style.margin=(-(1-k)*432)+'px '+(-(1-k)*205)+'px';}};
  addEventListener('resize',fit);fit();
  document.querySelectorAll('[data-msg]').forEach(b=>b.addEventListener('click',()=>fr.contentWindow.postMessage(JSON.parse(b.dataset.msg),'*')));
</script>
</body></html>"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(shell, encoding="utf-8")
    print(OUT, round(OUT.stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main()

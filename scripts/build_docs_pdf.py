"""Build a PDF from a Markdown document, rendering its Mermaid diagrams (a developer tool, not needed to run the project).

    python scripts/build_docs_pdf.py docs/architecture.md docs/architecture.pdf [--compact]

--compact uses slightly smaller type (the final summary is built that way to stay near four pages).

Needs: pandoc, Node.js with puppeteer-core and @mermaid-js/mermaid-cli (`mmdc`), and a local Chromium (set CHROMIUM to its path).
Each ```mermaid block becomes an image; a line `%% pdf: width=9cm` inside a block sets its printed width (it is a Mermaid
comment, invisible on GitHub). Links to other Markdown files are printed as plain text.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell")

CSS = """
@page { size: A4; }
html { font-size: 10.7pt; }
body { font-family: Carlito, "DejaVu Sans", sans-serif; line-height: 1.42; color: #1b1f24; max-width: none; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 6pt; color: #14316e; border-bottom: 2px solid #2557d6; padding-bottom: 3pt; }
h2 { font-size: 13pt; margin: 14pt 0 4pt; color: #14316e; page-break-after: avoid; }
h3 { font-size: 10.5pt; margin: 9pt 0 3pt; page-break-after: avoid; }
p { margin: 3.5pt 0 5pt; } ul, ol { margin: 3pt 0 5pt; padding-left: 15pt; } li { margin: 1.5pt 0; }
em { color: #333; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.7pt; background: #eef2f8; padding: 0 2pt; border-radius: 2pt; }
pre { background: #eef2f8; border-radius: 4pt; padding: 5pt 7pt; white-space: pre-wrap; word-break: break-word; margin: 4pt 0 7pt; } pre code { background: none; padding: 0; font-size: 8.2pt; }
table { border-collapse: collapse; width: 100%; margin: 5pt 0 8pt; font-size: 9.2pt; page-break-inside: auto; }
th { background: #14316e; color: #fff; text-align: left; padding: 3pt 5pt; }
td { border-bottom: 1px solid #d5dbe5; padding: 3pt 5pt; vertical-align: top; }
td { overflow-wrap: break-word; } td code { font-size: 7.4pt; } tr { page-break-inside: avoid; } tr:nth-child(even) td { background: #f6f8fb; }
.fig { margin: 6pt auto 8pt; text-align: center; page-break-inside: avoid; } .fig img { width: 100%; height: auto; }
a { color: #2557d6; text-decoration: none; } header { display: none; }
"""


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"command failed: {' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
    return r


def render_mermaid(blocks: list[str], work: Path) -> list[Path]:
    cfg = work / "mermaid.json"
    roomy = work / "mermaid-roomy.json"                       # for diagrams with titled sub-boxes, which need more breathing room
    roomy.write_text('{"theme":"default","flowchart":{"htmlLabels":true,"useMaxWidth":false,"padding":16,"nodeSpacing":45,"rankSpacing":55},'
                     '"themeVariables":{"fontFamily":"Carlito, DejaVu Sans, sans-serif","fontSize":"15px"}}')
    cfg.write_text('{"theme":"default","flowchart":{"htmlLabels":true,"useMaxWidth":false,"padding":6,"nodeSpacing":26,"rankSpacing":26},'
                   '"sequence":{"useMaxWidth":false,"messageMargin":20,"actorMargin":30,"boxMargin":6,"noteMargin":6,"mirrorActors":false},'
                   '"themeVariables":{"fontFamily":"Carlito, DejaVu Sans, sans-serif","fontSize":"15px"}}')
    pp = work / "puppeteer.json"
    pp.write_text('{"executablePath": "%s", "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"]}' % CHROMIUM)
    images = []
    for i, b in enumerate(blocks, 1):
        src, png = work / f"d{i}.mmd", work / f"d{i}.png"
        src.write_text(b, encoding="utf-8")
        chosen = roomy if re.search(r"%%\s*pdf:[^\n]*spacing=roomy", b) else cfg
        run(["mmdc", "-i", str(src), "-o", str(png), "-c", str(chosen), "-p", str(pp), "-b", "white", "-s", "3"], timeout=180)
        images.append(png)
    return images


def build(md: Path, pdf: Path, footer: str | None = None, compact: bool = False) -> int:
    work = Path(tempfile.mkdtemp(prefix="docpdf-"))
    text = md.read_text(encoding="utf-8")
    blocks = [m.group(1) for m in re.finditer(r"```mermaid\n(.*?)```", text, re.S)]
    images = render_mermaid(blocks, work)
    it = iter(images)

    def figure(m):
        img = next(it)
        w = re.search(r"%%\s*pdf:[^\n]*?width=([\d.]+(?:cm|%))", m.group(1))
        side = re.search(r"%%\s*pdf:[^\n]*?float=(left|right)", m.group(1))
        style = f"width:{w.group(1) if w else '100%'}" + (f";float:{side.group(1)};margin:2pt 0 6pt 12pt" if side else "")
        from PIL import Image
        iw, ih = Image.open(img).size
        cm = float(w.group(1).removesuffix("cm")) if w and w.group(1).endswith("cm") else 17.0
        print(f"  diagram {img.name}: printed {cm:.1f} x {cm * ih / iw:.1f} cm" + ("   <-- TOO TALL for a page" if cm * ih / iw > 22 else ""), file=sys.stderr)
        return f'<div class="fig" style="{style}"><img src="{img.name}" alt="diagram"></div>\n'
    text = re.sub(r"```mermaid\n(.*?)```", figure, text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\((?!https?://)([^)]+)\)", r"\1", text)          # local links are meaningless on paper
    (work / "doc.md").write_text(text, encoding="utf-8")
    css = CSS.replace("html { font-size: 10.7pt; }", "html { font-size: 10.1pt; }").replace("line-height: 1.42;", "line-height: 1.38;") if compact else CSS
    (work / "style.css").write_text(css)
    run(["pandoc", "doc.md", "-f", "markdown-smart-tex_math_dollars-citations-yaml_metadata_block-implicit_figures", "-t", "html5", "-s", "--embed-resources", "--css", "style.css",
         "--metadata", f"pagetitle={md.stem}", "-o", "doc.html"], cwd=work)
    html = (work / "doc.html").read_text(encoding="utf-8")
    html = re.sub(r"<table>(\s*<colgroup>)", r'<table style="table-layout:fixed">\1', html)      # honour the column widths
    html = re.sub(r"<code>([^<]*/[^<]*)</code>", lambda m: "<code>" + m.group(1).replace("/", "/<wbr>") + "</code>", html)   # wrap paths at slashes
    (work / "doc.html").write_text(re.sub(r"<p>((?:(?!</p>).)*:)</p>", r'<p style="break-after:avoid;page-break-after:avoid">\1</p>', html, flags=re.S), encoding="utf-8")
    version = re.search(r"Version: v([\d.]+)", (ROOT / "README.md").read_text(encoding="utf-8"))
    footer = footer or f"Agent Orchestrator - {md.stem.replace('-', ' ')} (v{version.group(1) if version else '?'})"
    run(["node", str(ROOT / "scripts" / "html_to_pdf.js"), str(work / "doc.html"), str(pdf), footer, CHROMIUM], timeout=180)
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(pdf))
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata({"/Title": text.splitlines()[0].lstrip("# ").strip(), "/Subject": f"built from {md.name}, sha256 {hashlib.sha256(md.read_bytes()).hexdigest()[:16]}"})
    with open(pdf, "wb") as fh:
        writer.write(fh)
    shutil.rmtree(work, ignore_errors=True)
    return len(reader.pages)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    pages = build(Path(sys.argv[1]), Path(sys.argv[2]), compact="--compact" in sys.argv)
    print(f"wrote {sys.argv[2]} ({pages} pages)")

"""Render the project's Markdown docs as standalone HTML.

    python render_docs.py            # write README.html, deploy/DEPLOY.html
    python render_docs.py --check    # fail if the HTML is out of date

Each page is self-contained - styles inlined, no external requests - so the
files work opened straight from disk, served from the NAS, or published through
GitHub Pages. They follow the dashboard's own palette and respect the reader's
light/dark preference.

GitHub renders Markdown natively, so these are for reading outside GitHub.
Re-run after editing any .md so the two stay in step.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent
DOCS = ["README.md", "deploy/DEPLOY.md"]

EXTENSIONS = ["tables", "fenced_code", "toc", "sane_lists", "attr_list"]

STYLE = """
:root {
  --bg:#f7f9f9; --surface:#fff; --border:#dde4e4; --text:#17201f;
  --muted:#62706e; --accent:#1f6f6b; --code-bg:#eef3f3;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#121817; --surface:#1a2221; --border:#2c3736; --text:#e6eceb;
    --muted:#93a3a1; --accent:#5cc4bb; --code-bg:#1f2a29;
  }
}
* { box-sizing:border-box; }
body {
  margin:0; padding:2rem 1.25rem 5rem; background:var(--bg); color:var(--text);
  font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-text-size-adjust:100%;
}
main { max-width:52rem; margin:0 auto; }
h1,h2,h3,h4 { line-height:1.25; margin:2rem 0 .6rem; }
h1 { font-size:1.9rem; margin-top:0; }
h2 { font-size:1.35rem; padding-bottom:.3rem; border-bottom:1px solid var(--border); }
h3 { font-size:1.1rem; }
p,ul,ol { margin:0 0 1rem; }
li { margin:.25rem 0; }
a { color:var(--accent); }
hr { border:0; border-top:1px solid var(--border); margin:2.5rem 0; }
strong { font-weight:650; }
blockquote {
  margin:0 0 1rem; padding:.5rem 1rem; border-left:3px solid var(--accent);
  background:var(--surface); color:var(--muted);
}
code {
  font-family:ui-monospace,"Cascadia Code",Consolas,monospace; font-size:.88em;
  background:var(--code-bg); padding:.12em .35em; border-radius:4px;
}
pre {
  background:var(--surface); border:1px solid var(--border); border-radius:8px;
  padding:.9rem 1rem; overflow-x:auto; margin:0 0 1rem;
}
pre code { background:none; padding:0; font-size:.85rem; }
.table-wrap { overflow-x:auto; margin:0 0 1.25rem; }
table { border-collapse:collapse; width:100%; font-size:.92rem;
        background:var(--surface); border:1px solid var(--border);
        border-radius:8px; overflow:hidden; }
th,td { padding:.5rem .7rem; text-align:left; border-bottom:1px solid var(--border);
        vertical-align:top; }
th { font-size:.76rem; text-transform:uppercase; letter-spacing:.04em;
     color:var(--muted); font-weight:600; }
tbody tr:last-child td { border-bottom:0; }
footer { max-width:52rem; margin:3rem auto 0; padding-top:1rem;
         border-top:1px solid var(--border); color:var(--muted); font-size:.82rem; }
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<main>
{body}
</main>
<footer>Generated from <code>{source}</code> by <code>render_docs.py</code>.
Re-run it after editing the Markdown.</footer>
</body>
</html>
"""


def title_of(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def link_md_to_html(html: str) -> str:
    """Point links at the rendered pages, so the HTML set navigates itself.

    Only rewrites relative links - anything with a scheme is left alone.
    """
    def replace(match: re.Match) -> str:
        href = match.group(1)
        if "://" in href or href.startswith(("#", "mailto:")):
            return match.group(0)
        target, _, anchor = href.partition("#")
        if not target.lower().endswith(".md"):
            return match.group(0)
        rebuilt = target[:-3] + ".html" + (("#" + anchor) if anchor else "")
        return f'href="{rebuilt}"'

    return re.sub(r'href="([^"]+)"', replace, html)


def wrap_tables(html: str) -> str:
    """Let wide tables scroll inside their own box rather than the page."""
    return html.replace("<table>", '<div class="table-wrap"><table>') \
               .replace("</table>", "</table></div>")


def render(source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=EXTENSIONS, output_format="html5")
    body = wrap_tables(link_md_to_html(body))
    return PAGE.format(
        title=title_of(text, source.stem),
        style=STYLE,
        body=body,
        source=source.name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the Markdown docs to HTML")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if any HTML is missing or stale")
    args = parser.parse_args()

    stale: list = []
    for rel in DOCS:
        source = ROOT / rel
        if not source.exists():
            print(f"  ! {rel} not found")
            stale.append(rel)
            continue

        target = source.with_suffix(".html")
        html = render(source)

        if args.check:
            current = target.read_text(encoding="utf-8") if target.exists() else None
            state = "ok" if current == html else "STALE"
            print(f"  [{state}] {target.relative_to(ROOT)}")
            if current != html:
                stale.append(rel)
        else:
            # write_bytes, not write_text: on Windows the latter would emit
            # CRLF, which git then normalises back and which would land on the
            # Linux NAS the wrong way round.
            target.write_bytes(html.encode("utf-8"))
            print(f"  {source.relative_to(ROOT)} -> {target.relative_to(ROOT)}"
                  f"  ({len(html):,} bytes)")

    if args.check and stale:
        print("\nOut of date. Run: python render_docs.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

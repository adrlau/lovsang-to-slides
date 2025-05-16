#!/usr/bin/env python3
import os
import re
import math
import tempfile
import subprocess
import shutil
from flask import (
    Flask, render_template, request,
    redirect, send_from_directory, abort, current_app
)
import requests

app = Flask(__name__)

# Global mapping build_id -> tmp_root
builds = {}

# ---------- Hjelpefunksjoner ----------

def fetch_chordpro_url(page_url):
    r = requests.get(page_url)
    r.raise_for_status()
    m = re.search(r'initializeSongPage\("([^"]+\.txt)"\)', r.text)
    if not m:
        abort(400, "Fant ingen ChordPro-tekst på siden")
    return m.group(1)

def download_chordpro(txt_url):
    r = requests.get(txt_url)
    r.encoding = "utf-8"
    r.raise_for_status()
    return r.text

def chordpro_to_plain(cp):
    out = []
    for ln in cp.splitlines():
        ln = ln.strip()
        if ln.startswith("{") and ln.endswith("}"):
            continue
        ln = re.sub(r"\[/?[^\]]+\]", "", ln)
        ln = re.sub(r"<[^>]+>", "", ln)
        out.append(ln)
    return out

def split_groups(lines):
    groups, buf = [], []
    for ln in lines:
        if ln == "":
            if buf:
                groups.append(buf)
                buf = []
        else:
            buf.append(ln)
    if buf:
        groups.append(buf)
    return groups

def clean_group(g):
    return [ln for ln in g if ":" not in ln]

def chunk_even(g, maxl):
    n = len(g)
    if n <= maxl:
        return [g]
    num  = math.ceil(n / maxl)
    base = n // num
    rem  = n % num
    out, idx = [], 0
    for i in range(num):
        sz = base + (1 if i < rem else 0)
        out.append(g[idx:idx+sz])
        idx += sz
    return out

# ---------- Routes ----------

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    urls      = request.form.get("urls", "").splitlines()
    max_lines = int(request.form.get("max_lines", 5))
    md_blocks = ["---\n"]  # slik at alle blokker får en foranstilt separator

    for url in urls:
        url = url.strip()
        if not url:
            continue

        txt_url = fetch_chordpro_url(url)
        cp_text = download_chordpro(txt_url)
        lines   = chordpro_to_plain(cp_text)
        groups  = split_groups(lines)

        for g in groups:
            cg = clean_group(g)
            if not cg:
                continue
            for chunk in chunk_even(cg, max_lines):
                md_blocks.append(
                    "\n".join(ln + "  " for ln in chunk)
                )
                md_blocks.append("\n---\n")

    full_md = "\n".join(md_blocks).strip()
    return render_template("editor.html", markdown=full_md)

@app.route("/build", methods=["POST"])
def build():
    md = request.form.get("markdown", "").strip()
    if not md:
        abort(400, "Ingen markdown mottatt")

    # Lag et midlertidig rotkatalog for build
    tmp_root = tempfile.mkdtemp(prefix="mkslides_")
    site_dir = os.path.join(tmp_root, "site")

    # Skriv slides.md i tmp_root
    md_file = os.path.join(tmp_root, "slides.md")
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md)

    # Kjør mkslides build ... --site-dir site_dir
    try:
        proc = subprocess.run(
            ["mkslides", "build", md_file, "--site-dir", site_dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=True
        )
        current_app.logger.info("mkslides stdout:\n%s", proc.stdout)
    except subprocess.CalledProcessError as e:
        # Rydd opp katalog på feil
        shutil.rmtree(tmp_root, ignore_errors=True)
        return (
            f"<h1>Build error</h1>"
            f"<h2>Command</h2><pre>mkslides build {md_file} --site-dir {site_dir}</pre>"
            f"<h2>stdout</h2><pre>{e.stdout}</pre>"
            f"<h2>stderr</h2><pre>{e.stderr}</pre>"
        ), 500

    # Lagre byggemappe og redirect til preview
    build_id = os.path.basename(tmp_root)
    builds[build_id] = site_dir
    return redirect(f"/preview/{build_id}/")

@app.route("/preview/<build_id>/", defaults={"path": ""})
@app.route("/preview/<build_id>/<path:path>")
def preview(build_id, path):
    if build_id not in builds:
        abort(404)
    root = builds[build_id]
    # Default dokument
    if path == "":
        for fn in ("slides.html", "index.html"):
            if os.path.exists(os.path.join(root, fn)):
                return send_from_directory(root, fn)
        abort(404)
    return send_from_directory(root, path)

if __name__ == "__main__":
    app.run(debug=True)

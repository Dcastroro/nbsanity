import asyncio
import json
import shutil
import subprocess
from urllib.parse import unquote

import pytest
from bs4 import BeautifulSoup

import main


@pytest.mark.parametrize("file_path,gist,raw_url", [
    ("example/project/blob/main/tutorials/demo.ipynb", False,
     "https://raw.githubusercontent.com/example/project/main/tutorials/demo.ipynb"),
    ("example/abc123", True,
     "https://gist.githubusercontent.com/example/abc123/raw/revision/demo.ipynb"),
])
def test_render_resolves_repository_images(tmp_path, monkeypatch, file_path, gist, raw_url):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "static").mkdir()
    (tmp_path / "nb.yml").write_text("format: html\n", encoding="utf-8")
    calls = []

    def download(url, directory):
        assert url == raw_url
        notebook = directory / "demo.ipynb"
        notebook.write_text(json.dumps({
            "nbformat": 4, "nbformat_minor": 5, "metadata": {},
            "cells": [{"cell_type": "markdown", "id": "images", "metadata": {},
                       "source": "![chart](images/chart.png)"}],
        }), encoding="utf-8")
        return notebook

    def render(command):
        calls.append(command)
        if command.startswith("quarto render "):
            notebook = next((tmp_path / "tmp_notebooks").glob("*/demo.ipynb"))
            figures = notebook.parent / "demo_files" / "figure-html"
            figures.mkdir(parents=True)
            (figures / "output.png").write_bytes(b"synthetic generated image")
            notebook.with_suffix(".html").write_text('''<html><head><title>Demo</title></head><body>
<img id="relative" src="images/chart.png" width="320">
<img id="generated" src="demo_files/figure-html/output.png">
<img id="remote" src="https://example.org/image.png">
<img id="embedded" src="data:image/png;base64,c3ludGhldGlj">
</body></html>''', encoding="utf-8")

    monkeypatch.setattr(main, "urlsave", download)
    monkeypatch.setattr(main, "run", render)
    if gist:
        monkeypatch.setattr(main, "gist_raw", lambda url: raw_url)
    response = asyncio.run(main.serve_notebook(file_path, gist=gist))
    html = tmp_path / unquote(response.headers["location"]).lstrip("/")
    soup = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
    assert soup.find(id="relative")["src"] == raw_url.rsplit("/", 1)[0] + "/images/chart.png"
    assert soup.find(id="relative")["width"] == "320"
    assert soup.find(id="generated")["src"] == "demo_files/figure-html/output.png"
    assert soup.find(id="remote")["src"] == "https://example.org/image.png"
    assert soup.find(id="embedded")["src"] == "data:image/png;base64,c3ludGhldGlj"
    assert all("onerror" in img.attrs for img in soup.find_all("img"))
    assert any(command.startswith("quarto render ") for command in calls)
    assert not list((tmp_path / "tmp_notebooks").iterdir())


@pytest.mark.parametrize("src,expected", [
    ("./images/chart.png", "https://raw.githubusercontent.com/example/project/main/tutorials/images/chart.png"),
    ("../images/chart.png?v=2#view", "https://raw.githubusercontent.com/example/project/main/images/chart.png?v=2#view"),
    ("https://example.org/chart.png", "https://example.org/chart.png"),
    ("//example.org/chart.png", "//example.org/chart.png"),
    ("/assets/icon.png", "/assets/icon.png"),
    ("data:image/png;base64,c3ludGhldGlj", "data:image/png;base64,c3ludGhldGlj"),
    ("attachment:chart.png", "attachment:chart.png"),
    ("#figure", "#figure"),
    ("", ""),
    ("http://[invalid", "http://[invalid"),
    ("images/%00.png", "https://raw.githubusercontent.com/example/project/main/tutorials/images/%00.png"),
])
def test_image_url_kinds(tmp_path, src, expected):
    html = tmp_path / "demo.html"
    soup = BeautifulSoup('<img width="320" onerror="customHandler()">', "html.parser")
    soup.img['src'] = src
    html.write_text(str(soup), encoding="utf-8")
    main.fix_image_paths(html, "https://raw.githubusercontent.com/example/project/main/tutorials/demo.ipynb")
    image = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser").img
    assert image['src'] == expected
    assert image['width'] == "320"
    assert image['onerror'] == "customHandler()"


def test_existing_encoded_local_image_is_preserved(tmp_path):
    (tmp_path / "chart with space.png").write_bytes(b"synthetic image")
    html = tmp_path / "demo.html"
    html.write_text('<img src="chart%20with%20space.png?v=1">', encoding="utf-8")
    main.fix_image_paths(html, "https://raw.githubusercontent.com/example/project/main/demo.ipynb")
    image = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser").img
    assert image['src'] == 'chart%20with%20space.png?v=1'


@pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto is required for the renderer smoke test")
def test_real_quarto_relative_image(tmp_path):
    notebook = tmp_path / "demo.ipynb"
    notebook.write_text(json.dumps({
        "nbformat": 4, "nbformat_minor": 5, "metadata": {},
        "cells": [{"cell_type": "markdown", "id": "chart", "metadata": {},
                   "source": "![Chart](images/chart.png)"}],
    }), encoding="utf-8")
    subprocess.run(["quarto", "render", str(notebook), "--no-execute", "--to", "html"],
                   check=True, capture_output=True, text=True, timeout=60)
    html = notebook.with_suffix('.html')
    before = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
    assert before.find('img')['src'] == 'images/chart.png'
    main.fix_image_paths(html, "https://raw.githubusercontent.com/example/project/main/tutorials/demo.ipynb")
    after = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
    assert after.find('img')['src'] == (
        "https://raw.githubusercontent.com/example/project/main/tutorials/images/chart.png")

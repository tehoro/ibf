"""
Forecast HTML generation utilities.
"""

from __future__ import annotations

from dataclasses import dataclass
import html
from pathlib import Path
from typing import Optional

from ..util import ensure_directory, write_text_file


@dataclass
class ForecastPage:
    destination: Path
    display_name: str
    issue_time: str
    forecast_text: str
    translated_text: Optional[str] = None
    translation_language: Optional[str] = None
    ibf_context: Optional[str] = None
    map_link: Optional[str] = None
    model_label: str = "ECMWF IFS 0.25° ensemble"
    model_ack_url: Optional[str] = None


def render_forecast_page(page: ForecastPage) -> Path:
    """
    Render the supplied forecast page to disk.
    """
    ensure_directory(page.destination.parent)

    display_name = html.escape(page.display_name, quote=True)
    issue_time = html.escape(page.issue_time, quote=True)
    forecast_html = _markdown_to_html(page.forecast_text)
    translated_html, translation_header = _render_translation_block(
        page.translated_text,
        page.translation_language,
    )
    ibf_html = _render_ibf_block(page.ibf_context)

    body_parts = [
        f"<h1>Forecast for {display_name}</h1>",
        f"<h3>Issued: {issue_time}</h3>",
    ]

    if page.map_link:
        safe_link = html.escape(page.map_link, quote=True)
        body_parts.append(
            f'<p class="map-link"><a href="{safe_link}" target="_blank" rel="noopener">Show map for {display_name}</a></p>'
        )

    body_parts.append(f'<div id="forecast-content">{forecast_html}</div>')

    if translation_header and translated_html:
        body_parts.append(translation_header)
        body_parts.append(f'<div id="translated-forecast-content">{translated_html}</div>')

    if ibf_html:
        body_parts.append(ibf_html)

    safe_model_label = html.escape(page.model_label, quote=True)
    footer_ack = ""
    if page.model_ack_url:
        safe_ack_url = html.escape(page.model_ack_url, quote=True)
        footer_ack = f'  Additional acknowledgement: <a href="{safe_ack_url}" target="_blank" rel="noopener">open data licence</a>.<br>'
    body_parts.extend(
        [
            '<p><a href="../index.html">Return to Menu</a></p>',
            f"""<div class="footer-note">
  Forecast produced using <a href="https://github.com/tehoro/ibf" target="_blank" rel="noopener">IBF</a>, developed by <a href="mailto:neil.gordon@hey.com?subject=Comment%20on%20IBF">Neil Gordon</a>.
  Data courtesy of <a href="https://open-meteo.com/" target="_blank" rel="noopener">open-meteo.com</a> using {safe_model_label}.
{footer_ack}  If you want to interactively request a forecast for a location, visit the <a href="https://chatgpt.com/g/g-4OgZFHOPA-global-ensemble-weather-forecaster" target="_blank" rel="noopener">Global Ensemble Weather Forecaster</a> (ChatGPT account required).
</div>""",
        ]
    )

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Forecast for {display_name}</title>
  {_STYLE_BLOCK}
</head>
<body>
{chr(10).join(body_parts)}
{_SCRIPT_BLOCK}
</body>
</html>
"""
    write_text_file(page.destination, html_doc)
    return page.destination


def _markdown_to_html(text: str) -> str:
    import re

    bullet_regex = re.compile(r"^([*\-•])\s+(.*)")

    def convert_lists(md: str) -> str:
        lines = md.splitlines()
        result = []
        in_list = False

        def start_list():
            nonlocal in_list
            result.append("<ul>")
            in_list = True

        def end_list():
            nonlocal in_list
            result.append("</ul>")
            in_list = False

        for line in lines:
            stripped = line.strip()
            match = bullet_regex.match(stripped)
            if match:
                if not in_list:
                    start_list()
                result.append(f"<li>{match.group(2).strip()}</li>")
            else:
                if in_list:
                    end_list()
                result.append(line)
        if in_list:
            end_list()
        return "\n".join(result)

    safe_text = html.escape(text or "", quote=True)
    html_output = convert_lists(safe_text)
    html_output = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html_output, flags=re.MULTILINE)
    html_output = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_output)
    html_output = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html_output)
    html_output = html_output.replace("\n", "<br>")
    html_output = re.sub(r"<br>\s*(<h3>)", r"\1", html_output)
    html_output = re.sub(r"(</h3>)\s*<br>", r"\1", html_output)
    html_output = re.sub(r"<br>(\s*<ul>)", r"\1", html_output)
    html_output = re.sub(r"(<ul>)<br>", r"\1", html_output)
    html_output = re.sub(r"</li><br><li>", r"</li><li>", html_output)
    html_output = re.sub(r"</li><br>(\s*</ul>)", r"</li>\1", html_output)
    html_output = re.sub(r"(</ul>)<br>", r"\1", html_output)
    return html_output.strip()


def _render_translation_block(text: Optional[str], language: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not text or not language:
        return None, None

    lang_map = {
        "Fr-CA": "French (Canada)",
        "fr": "French",
        "es": "Spanish",
        "de": "German",
    }
    display_name = lang_map.get(language, language)
    safe_display = html.escape(display_name, quote=True)
    safe_language = html.escape(language, quote=True)
    suffix = f" ({safe_language})" if display_name != language else ""
    header = f"<h2>Forecast in {safe_display}{suffix}</h2>"
    return _markdown_to_html(text), header


def _render_ibf_block(context: Optional[str]) -> Optional[str]:
    if not context:
        return None
    context_html = _markdown_to_html(context)
    return f"""<div id="ibf-context-wrapper">
  <div id="ibf-context-header" onclick="toggleIbfContext()">
    <span id="ibf-context-toggle">▶</span>
    <span id="ibf-context-header-text">Impact-Based Forecast Context</span>
  </div>
  <div id="ibf-context-content">{context_html}</div>
</div>"""


_STYLE_BLOCK = """<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f8f9fa; color: #212529; margin: 1em auto; padding: 0 1em; max-width: 800px; line-height: 1.6; }
h1 { color: #343a40; border-bottom: 2px solid #dee2e6; padding-bottom: 0.5em; margin-top: 1em; margin-bottom: 1em; font-size: 1.8em; }
h3 { color: #495057; font-size: 1.1em; font-weight: 600; margin-top: 0.8em; margin-bottom: 0.4em; }
#forecast-content, #translated-forecast-content { background: #ffffff; padding: 1.5em 2em; border: 1px solid #dee2e6; border-radius: 6px; white-space: pre-wrap; word-wrap: break-word; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 2em; }
#forecast-content strong, #translated-forecast-content strong { color: #0d6efd; font-weight: bold; }
#forecast-content em, #translated-forecast-content em { color: #6f42c1; font-style: italic; }
#translated-forecast-content { margin-top: 1em; border-top: 3px solid #6c757d; padding-top: 1.5em; }
.map-link { margin: 0.2em 0 1.2em; }
.map-link a { color: #198754; }
#ibf-context-wrapper { margin-bottom: 2em; }
#ibf-context-header { background: #ffffff; padding: 1em 1.5em; border: 1px solid #dee2e6; border-radius: 6px 6px 0 0; cursor: pointer; user-select: none; display: flex; align-items: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
#ibf-context-header:hover { background: #f8f9fa; }
#ibf-context-toggle { font-size: 0.8em; margin-right: 0.8em; transition: transform 0.2s; color: #495057; }
#ibf-context-toggle.expanded { transform: rotate(90deg); }
#ibf-context-header-text { color: #343a40; font-weight: 500; }
#ibf-context-content { display: none; margin-top: 0; background: #ffffff; border-top: 1px solid #dee2e6; border-radius: 0 0 6px 6px; padding: 1.5em 2em; white-space: normal; word-wrap: break-word; box-shadow: 0 2px 4px rgba(0,0,0,0.05); line-height: 1.5; }
#ibf-context-content ul { margin: 0 0 1em 1.2em; padding: 0; }
#ibf-context-content li { margin-bottom: 0.5em; }
#ibf-context-content li:last-child { margin-bottom: 0; }
#ibf-context-content.expanded { display: block; }
h2 { color: #343a40; margin-top: 1.5em; margin-bottom: 0.8em; font-size: 1.4em; }
a { color: #0d6efd; text-decoration: none; font-weight: 500; }
a:hover { text-decoration: underline; color: #0a58ca; }
.footer-note { margin-top: 2.5em; padding-top: 1em; border-top: 1px solid #dee2e6; font-size: 0.9em; color: #6c757d; text-align: center; }
.footer-note a { font-weight: normal; }
hr { display: none; }
@media (max-width: 600px) { body { margin: 0.5em; padding: 0 0.8em; } h1 { font-size: 1.5em; } #forecast-content, #translated-forecast-content, #ibf-context-content { padding: 1em 1.2em; } h2 { font-size: 1.2em;} }
</style>"""


_SCRIPT_BLOCK = """<script>
function toggleIbfContext() {
  const content = document.getElementById('ibf-context-content');
  const toggle = document.getElementById('ibf-context-toggle');
  if (content.classList.contains('expanded')) {
    content.classList.remove('expanded');
    toggle.classList.remove('expanded');
  } else {
    content.classList.add('expanded');
    toggle.classList.add('expanded');
  }
}
</script>"""

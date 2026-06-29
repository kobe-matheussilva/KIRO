import argparse
import base64
import binascii
import os
import re
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from pydantic import ValidationError

from customer_health.service import run_relationship_analysis
from customer_health.settings import RelationshipSettings


def _provider_defaults(provider: str) -> tuple[str, str]:
    if provider == "anthropic":
        return "claude-sonnet-4-20250514", "https://api.anthropic.com/v1"
    return "gemini-2.5-flash", "https://generativelanguage.googleapis.com/v1beta"


def _first(values: dict[str, list[str]], key: str, default: str = "") -> str:
    arr = values.get(key) or []
    return arr[0] if arr else default


def _markdown_to_html(markdown_text: str) -> str:
    text = escape(markdown_text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)

    html_lines: list[str] = []
    in_list = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if line.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{line[4:]}</h3>")
            continue
        if line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{line[3:]}</h2>")
            continue
        if line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{line[2:]}</h1>")
            continue
        if line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        html_lines.append(f"<p>{line}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _health_css(level: str) -> str:
    normalized = level.strip().lower()
    if normalized in {"baixa", "saudável", "saudavel"}:
        return "health-good"
    if normalized in {"moderada", "alta"}:
        return "health-mid"
    return "health-bad"


def _render_page(content: str) -> bytes:
    html = f"""<!doctype html>
<html lang=\"pt-BR\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Kizuma</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: linear-gradient(180deg, #f5faf7 0%, #f6f7fb 100%); color: #101525; }}
    .wrap {{ max-width: 980px; margin: 30px auto; padding: 0 16px; }}
    .card {{ background: #fff; border-radius: 14px; padding: 18px; box-shadow: 0 8px 24px rgba(10, 20, 50, .08); margin-bottom: 16px; }}
    .brand {{ font-size: .9rem; color: #0d7a47; text-transform: uppercase; letter-spacing: .08em; font-weight: 700; }}
    h1 {{ margin-top: 0; }}
    h2 {{ margin: 0 0 10px 0; font-size: 1.1rem; }}
    label {{ display: block; font-size: .92rem; margin: 10px 0 4px; color: #27304a; }}
    input, select {{ width: 100%; border: 1px solid #d3d8e4; border-radius: 10px; padding: 10px; font-size: .95rem; box-sizing: border-box; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .hint {{ font-size: .86rem; color: #5e6680; }}
    .btn {{ margin-top: 14px; background: #0b5fff; border: 0; color: #fff; padding: 10px 14px; border-radius: 10px; cursor: pointer; font-weight: 600; }}
    .btn-link {{ display: inline-block; text-decoration: none; background: #0b5fff; color: #fff; padding: 10px 14px; border-radius: 10px; font-weight: 600; white-space: nowrap; }}
    .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
    .metric {{ background: #f3f6ff; border: 1px solid #dbe4ff; border-radius: 10px; padding: 10px; }}
    .result-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 12px; flex-wrap: wrap; }}
    .result-actions {{ display: flex; align-items: center; gap: 10px; }}
    .result-actions strong {{ color: #27304a; font-size: .95rem; }}
    .health-good {{ background: #eaf8ef; border: 1px solid #9fddb6; color: #14532d; }}
    .health-mid {{ background: #fff7e6; border: 1px solid #f4cc7d; color: #7a4b07; }}
    .health-bad {{ background: #fdecec; border: 1px solid #f0a8a8; color: #7f1d1d; }}
    .md {{ line-height: 1.55; }}
    .md h1, .md h2, .md h3 {{ margin: .6em 0 .4em; }}
    .md ul {{ margin: .3em 0 .8em 1.2em; padding: 0; }}
    .md p {{ margin: .45em 0; }}
    @media (max-width: 760px) {{ .row, .metrics {{ grid-template-columns: 1fr; }} .result-head {{ flex-direction: column; align-items: stretch; }} }}
  </style>
</head>
<body>
  <div class=\"wrap\">{content}</div>
</body>
</html>"""
    return html.encode("utf-8")


def _form_html(defaults: RelationshipSettings | None, message: str = "") -> str:
    provider = defaults.llm_provider if defaults else "gemini"
    model_default, base_default = _provider_defaults(provider)
    msg = f"<div class='card'><strong>{escape(message)}</strong></div>" if message else ""
    return f"""
{msg}
<div class=\"card\">
  <div class=\"brand\">Kizuma</div>
  <h1>Kizuma</h1>
  <p class=\"hint\">Healthcheck de saúde do relacionamento cliente-suporte.</p>
  <p class=\"hint\">Preencha os campos e receba um diagnóstico amigável no final.</p>
  <form method=\"post\" action=\"/analyze\">
    <h2>1) Jira</h2>
    <div class=\"row\">
      <div>
        <label>Jira Base URL</label>
        <input value=\"{escape(defaults.jira_base_url if defaults else 'defina JIRA_BASE_URL no .env')}\" disabled />
      </div>
      <div>
        <label>Project Key</label>
        <input value=\"{escape(defaults.jira_project_key if defaults else 'defina JIRA_PROJECT_KEY no .env')}\" disabled />
      </div>
    </div>
    <div class=\"row\">
      <div>
        <label>E-mail Jira</label>
        <input value=\"{escape(defaults.jira_user_email if defaults else 'defina JIRA_USER_EMAIL no .env')}\" disabled />
      </div>
    </div>
    <p class=\"hint\">Configuração do Jira carregada do ambiente (.env).</p>

    <h2>2) IA</h2>
    <div class=\"row\">
      <div>
        <label>Provedor</label>
        <select name=\"llm_provider\">
          <option value=\"gemini\" {'selected' if provider == 'gemini' else ''}>gemini</option>
          <option value=\"anthropic\" {'selected' if provider == 'anthropic' else ''}>anthropic</option>
        </select>
      </div>
      <div>
        <label>LLM API Key</label>
        <input name=\"llm_api_key\" type=\"password\" required />
      </div>
    </div>
    <div class=\"row\">
      <div>
        <label>Modelo</label>
        <input name=\"llm_model\" value=\"{escape(defaults.llm_model if defaults else model_default)}\" />
      </div>
      <div>
        <label>Base URL</label>
        <input name=\"llm_base_url\" value=\"{escape(defaults.llm_base_url if defaults else base_default)}\" />
      </div>
    </div>

    <h2>3) Cliente + Janela</h2>
    <div class=\"row\">
      <div>
        <label>Nome do cliente (como está no Jira)</label>
        <input name=\"customer_name\" required />
      </div>
      <div>
        <label>Máximo de tickets</label>
        <input name=\"ticket_limit\" type=\"number\" min=\"5\" max=\"500\" value=\"{defaults.ticket_limit if defaults else 40}\" />
      </div>
    </div>
    <div class=\"row\">
      <div>
        <label>Modo de janela</label>
        <select name=\"time_mode\">
          <option value=\"all\">Histórico completo</option>
          <option value=\"months\">Últimos meses</option>
          <option value=\"days\">Últimos dias</option>
        </select>
      </div>
      <div>
        <label>Meses</label>
        <input name=\"lookback_months\" type=\"number\" min=\"1\" max=\"36\" value=\"6\" />
      </div>
    </div>
    <div class=\"row\">
      <div>
        <label>Dias</label>
        <input name=\"lookback_days\" type=\"number\" min=\"1\" max=\"3650\" value=\"90\" />
      </div>
    </div>

    <button class=\"btn\" type=\"submit\">Gerar healthcheck</button>
    <p class=\"hint\">Privacidade: o relatório evita expor nomes de empresa/time interno.</p>
  </form>
</div>
"""


def _result_html(report) -> str:
    md_html = _markdown_to_html(report.assessment_text)
    health_class = _health_css(report.temperature.level)
    return f"""
<div class=\"card\"><a href=\"/\">&larr; Nova análise</a></div>
<div class=\"card\">
  <div class=\"brand\">Kizuma</div>
  <h1>Healthcheck concluído</h1>
  <div class=\"result-head\">
    <div class=\"result-actions\">
      <strong>Download opcional:</strong>
      <a class=\"btn-link\" href=\"/download?file={quote(str(report.output_markdown))}\">Baixar Markdown</a>
    </div>
  </div>
  <div class=\"metrics\">
    <div class=\"metric {health_class}\"><strong>Temperatura</strong><br/>{escape(report.temperature.level)}</div>
    <div class=\"metric\"><strong>Score</strong><br/>{report.temperature.score}</div>
    <div class=\"metric\"><strong>Tickets</strong><br/>{report.temperature.ticket_count}</div>
  </div>
</div>
<div class=\"card\">
  <h2>Resumo amigável (Markdown)</h2>
  <div class=\"md\">{md_html}</div>
</div>
<div class=\"card\">
  <h2>Auditoria</h2>
  <p><strong>JQL:</strong> {escape(report.jql_used)}</p>
  <p><strong>Markdown:</strong> {escape(str(report.output_markdown))}</p>
  <p><strong>JSON:</strong> {escape(str(report.output_json))}</p>
</div>
"""


class CustomerHealthHandler(BaseHTTPRequestHandler):
  def _auth_config(self) -> tuple[str, str] | None:
    user = os.getenv("KIZUMA_BASIC_AUTH_USER", "").strip()
    password = os.getenv("KIZUMA_BASIC_AUTH_PASSWORD", "").strip()
    if user and password:
      return user, password
    return None

  def _is_authorized(self) -> bool:
    config = self._auth_config()
    if not config:
      return True

    header = self.headers.get("Authorization", "")
    if not header.startswith("Basic "):
      return False

    encoded = header[6:].strip()
    try:
      decoded = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
      return False

    user, password = config
    return decoded == f"{user}:{password}"

  def _require_auth(self) -> bool:
    if self._is_authorized():
      return False
    self.send_response(401)
    self.send_header("WWW-Authenticate", 'Basic realm="Kizuma"')
    body = _render_page("<div class='card'>Acesso restrito.</div>")
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)
    return True

  def _output_root(self) -> Path:
    return Path("output/customer_relationship").resolve()

  def _send_file(self, file_path: Path) -> None:
    root = self._output_root()
    target = file_path.resolve()
    if root not in target.parents or not target.is_file():
      self._send_html(_render_page("<div class='card'>Arquivo não encontrado.</div>"), status=404)
      return

    if target.suffix.lower() != ".md":
      self._send_html(_render_page("<div class='card'>Apenas download de Markdown está disponível.</div>"), status=400)
      return

    content = target.read_bytes()
    content_type = "text/markdown; charset=utf-8"

    self.send_response(200)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Disposition", f"attachment; filename={target.name}")
    self.send_header("Content-Length", str(len(content)))
    self.end_headers()
    self.wfile.write(content)

  def _defaults(self) -> RelationshipSettings | None:
    try:
      return RelationshipSettings()
    except Exception:
      return None

  def _send_html(self, body: bytes, status: int = 200) -> None:
    self.send_response(status)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def do_GET(self) -> None:  # noqa: N802
    if self._require_auth():
      return

    parsed = urlparse(self.path)
    if parsed.path == "/download":
      query = parse_qs(parsed.query)
      raw_file = _first(query, "file").strip()
      if not raw_file:
        self._send_html(_render_page("<div class='card'>Parâmetro de download ausente.</div>"), status=400)
        return
      self._send_file(Path(raw_file))
      return

    if parsed.path != "/":
      self._send_html(_render_page("<div class='card'>Página não encontrada.</div>"), status=404)
      return
    body = _render_page(_form_html(self._defaults()))
    self._send_html(body)

  def do_POST(self) -> None:  # noqa: N802
    if self._require_auth():
      return

    if self.path != "/analyze":
      self._send_html(_render_page("<div class='card'>Rota inválida.</div>"), status=404)
      return

    length = int(self.headers.get("Content-Length", "0"))
    raw = self.rfile.read(length).decode("utf-8")
    data = parse_qs(raw)

    time_mode = _first(data, "time_mode", "all")
    all_history = time_mode == "all"
    lookback_months = int(_first(data, "lookback_months", "6") or "6")
    lookback_days = int(_first(data, "lookback_days", "90") or "90")

    provider = _first(data, "llm_provider", "gemini")
    model_default, base_default = _provider_defaults(provider)
    env_defaults = self._defaults()
    if env_defaults is None:
      body = _render_page(_form_html(None, message="Configuração inválida no .env para Jira/LLM."))
      self._send_html(body, status=400)
      return

    try:
      settings = RelationshipSettings(
        jira_base_url=env_defaults.jira_base_url,
        jira_user_email=env_defaults.jira_user_email,
        jira_api_token=env_defaults.jira_api_token.get_secret_value(),
        jira_project_key=env_defaults.jira_project_key,
        llm_provider=provider,
        llm_api_key=_first(data, "llm_api_key"),
        llm_model=_first(data, "llm_model", model_default),
        llm_base_url=_first(data, "llm_base_url", base_default),
        all_history=all_history,
        lookback_days=lookback_days if not all_history and time_mode == "days" else 0,
        lookback_months=lookback_months if not all_history and time_mode == "months" else 0,
        ticket_limit=int(_first(data, "ticket_limit", "40") or "40"),
      )
    except ValidationError as exc:
      body = _render_page(_form_html(self._defaults(), message=f"Configuração inválida: {exc}"))
      self._send_html(body, status=400)
      return

    customer_name = _first(data, "customer_name").strip()
    if not customer_name:
      body = _render_page(_form_html(self._defaults(), message="Informe o nome do cliente."))
      self._send_html(body, status=400)
      return

    try:
      report = run_relationship_analysis(
        settings,
        customer_name=customer_name,
        all_history=all_history,
        lookback_days=settings.lookback_days,
        lookback_months=settings.lookback_months,
        ticket_limit=settings.ticket_limit,
      )
      body = _render_page(_result_html(report))
      self._send_html(body, status=200)
    except Exception as exc:  # noqa: BLE001
      body = _render_page(_form_html(self._defaults(), message=f"Falha na análise: {exc}"))
      self._send_html(body, status=500)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kizuma — frontend local de relacionamento cliente-suporte")
    parser.add_argument("--host", default=os.getenv("KIZUMA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", os.getenv("KIZUMA_PORT", "8501"))))
    args = parser.parse_args()

    try:
        server = HTTPServer((args.host, args.port), CustomerHealthHandler)
    except OSError as exc:
        if exc.errno == 48:
            print(
                f"[kizuma] porta {args.port} já está em uso. "
                f"Tente: python -m customer_health.webapp --host {args.host} --port {args.port + 1}"
            )
            return
        raise

    print(f"Kizuma disponível em http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""
Clipping — Saúde & Educação (News Scrapper)
- Coleta multi-fonte (Google News + ANS + Anvisa + Valor RSS + Brazil Stock Guide) via clipping_core
- Decodifica os links do Google News para a URL real do veiculo
- Envia e-mail com XLSX + TXT (input AI) anexados, e sincroniza com o Google Drive
"""

import os

# =============================================================================
# >>>>>>>>>>>>>>>>>>>>>>>>>  AJUSTE MANUAL AQUI  <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
# =============================================================================
# Periodo de busca:
#   '1h', '2h', '6h', '12h'  -> horas
#   '1d', '2d', '7d'         -> dias
WHEN = os.environ.get("WHEN_OVERRIDE", "").strip() or "1d"

# Destinatarios vem do env EMAIL_TO_OVERRIDE (app / agendamento / variavel CLIP_RECIPIENTS).
# Sem e-mail no codigo (repo publico). Se vazio, nao envia e-mail (so salva XLSX + Drive).
EMAIL_TO = [e.strip() for e in os.environ.get("EMAIL_TO_OVERRIDE", "").split(",") if e.strip()]
# =============================================================================

import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

import clipping_core


def fetch_news() -> pd.DataFrame:
    """Coleta multi-fonte (clipping_core) e devolve no schema esperado pelo restante do pipeline."""
    df = clipping_core.collect(WHEN, progress=lambda m: print("·", m, flush=True))
    cols = ["title", "count_news", "link", "source", "date", "hour",
            "searched_keyword", "source_link"]
    if df.empty:
        return df
    return df[cols].reset_index(drop=True)


RED = "#334155"   # cor neutra da divisória/realce (sem identidade de marca)


def build_email_html(df: pd.DataFrame, total: int, drive_url: str, novas_backlog: int) -> str:
    """HTML do e-mail: título + divisória neutra."""
    header = f"""
    <table width="100%" style="border-collapse:collapse;margin-bottom:8px;">
      <tr>
        <td style="vertical-align:middle;font-family:Arial,sans-serif;font-size:18px;
                   font-weight:bold;color:#111;">
          Clipping — Saúde &amp; Educação
        </td>
      </tr>
    </table>
    <hr style="border:none;border-top:2px solid {RED};margin:6px 0 18px 0;">
    """

    if df.empty:
        return f"<html><body style='font-family:Arial,sans-serif;max-width:760px;margin:auto;'>{header}<p>Nenhuma noticia encontrada no periodo.</p></body></html>"

    meta = f"""
    <p style="font-family:Arial,sans-serif;font-size:13px;color:#555;margin:0 0 18px 0;">
      <b>News Scrapper</b> &nbsp;|&nbsp; Gerado em: <b>{date.today().strftime('%d/%m/%Y')}</b>
      &nbsp;|&nbsp; Janela: <b>{WHEN}</b> &nbsp;|&nbsp; Total: <b>{total}</b>
      &nbsp;|&nbsp; Ineditas: <b>{novas_backlog}</b>
    </p>
    """

    aviso = f"""
    <table width="100%" style="border-collapse:collapse;margin:0 0 24px 0;">
      <tr><td style="padding:14px 16px;background:#fff5f7;border-left:4px solid {RED};
                     font-family:Arial,sans-serif;font-size:13px;color:#333;">
        <b>📤 Enviar para o AI:</b> clique no botao, de <b>Ctrl+A</b> para selecionar tudo
        e <b>Ctrl+C</b> para copiar. Cole no Claude junto com seu prompt de selecao.
        <div style="margin-top:10px;">
          <a href="{drive_url}" style="display:inline-block;padding:8px 18px;
             background:{RED};color:#fff;text-decoration:none;border-radius:3px;
             font-size:13px;font-weight:bold;">Abrir lista para copiar</a>
        </div>
      </td></tr>
    </table>
    """

    items = []
    for i, row in enumerate(df.itertuples(index=False), start=1):
        items.append(f"""
        <tr><td style="padding:10px 0;border-bottom:1px solid #eee;vertical-align:top;
                       font-family:Arial,sans-serif;">
          <div style="font-size:14px;color:#111;line-height:1.4;">
            <b style="color:{RED};">{i}.</b> {row.title}
          </div>
          <div style="font-size:12px;color:#666;margin:4px 0 8px 22px;">
            Fonte: <b>{row.source}</b> &middot; {row.date} {row.hour}
          </div>
          <div style="margin-left:22px;">
            <a href="{row.link}" style="display:inline-block;padding:6px 14px;
               background:{RED};color:#fff;text-decoration:none;border-radius:3px;
               font-size:12px;">Ler noticia</a>
          </div>
        </td></tr>
        """)

    footer = f"""
    <hr style="border:none;border-top:1px solid #ddd;margin:32px 0 12px 0;">
    <p style="font-family:Arial,sans-serif;font-size:11px;color:#888;text-align:center;">
      Monitor automatico via GitHub Actions
    </p>
    """

    return f"""<html><body style="font-family:Arial,sans-serif;max-width:760px;margin:auto;padding:16px;">
      {header}{meta}{aviso}
      <table style="width:100%;border-collapse:collapse;">{''.join(items)}</table>
      {footer}
    </body></html>"""


AI_PROMPT = """Estou fazendo um clipping de equity research para os setores de saúde e educação. Analise APENAS as notícias listadas nesta mensagem (ignore notícias ou listas enviadas em mensagens anteriores deste chat) e selecione todas que sejam relevantes para investidores, considerando:
- empresas privadas e listadas em bolsa;
- movimentos de consolidação (M&A), expansão ou retração;
- decisões e regulamentações de órgãos governamentais ou reguladores (ex.: MEC, ANS, SUS, etc.);
- tendências estruturais que possam impactar a demanda, custos ou competitividade;
- mudanças no comportamento do consumidor, cursos e matrículas em universidades/faculdades;
- inovações, tecnologias ou políticas públicas que afetem os setores;
- notícias sobre concorrência, preços, parcerias ou financiamentos.

Não deixe passar nenhuma notícia que possa ter implicações para avaliação de empresas ou do setor. Em caso de dúvida, inclua — prefiro revisar falsos positivos a perder notícia relevante.

Formato da resposta: lista numerada em markdown, cada item no formato `[título](link)` para que os títulos fiquem clicáveis. Use exatamente os links da lista abaixo.

Vou te enviar periodicamente as notícias que selecionei manualmente para você ir calibrando o critério.

"""


def _load_ai_prompt() -> str:
    """Prompt editável pelo app (arquivo ai_prompt.txt no repo); cai no AI_PROMPT padrão se não existir."""
    try:
        p = Path("ai_prompt.txt")
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
    except Exception:
        pass
    return AI_PROMPT.strip()


def build_ai_text(df: pd.DataFrame) -> str:
    lines = [f"{i}. {row.title} | {row.link}"
             for i, row in enumerate(df.itertuples(index=False), start=1)]
    return _load_ai_prompt() + "\n\nNOTÍCIAS:\n" + "\n".join(lines)


def sync_to_drive(df: pd.DataFrame, xlsx_path: Path, txt_path: Path) -> tuple[str, int]:
    """Atualiza ai_input.txt e news_scrapper.xlsx; adiciona ao backlog so links ineditos.
    Retorna (url do news_scrapper, qtd de noticias novas adicionadas ao backlog)."""
    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    folder_id = os.environ["DRIVE_FOLDER_ID"].strip()
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def find_file_id(name: str) -> str:
        q = f"name='{name}' and '{folder_id}' in parents and trashed=false"
        files = service.files().list(q=q, fields="files(id)").execute().get("files", [])
        if not files:
            raise RuntimeError(
                f"Arquivo '{name}' nao existe na pasta do Drive. "
                "Service Accounts nao podem criar arquivos em contas pessoais — "
                "crie manualmente (upload de um placeholder vazio) uma unica vez."
            )
        return files[0]["id"]

    def update_file(local_path: Path, drive_name: str, mimetype: str) -> str:
        file_id = find_file_id(drive_name)
        media = MediaFileUpload(str(local_path), mimetype=mimetype, resumable=False)
        service.files().update(fileId=file_id, media_body=media).execute()
        return f"https://drive.google.com/file/d/{file_id}/view"

    def download_file(drive_name: str, local_path: Path) -> None:
        file_id = find_file_id(drive_name)
        request = service.files().get_media(fileId=file_id)
        with open(local_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

    ai_url = update_file(txt_path, "ai_input.txt", "text/plain")
    update_file(xlsx_path, "news_scrapper.xlsx", XLSX_MIME)
    print("[ok] Drive: ai_input.txt e news_scrapper.xlsx atualizados")

    backlog_local = Path("backlog.xlsx")
    download_file("backlog.xlsx", backlog_local)
    try:
        df_backlog = pd.read_excel(backlog_local)
    except Exception:
        df_backlog = pd.DataFrame(columns=["added_at"] + list(df.columns))

    if "link" in df_backlog.columns and not df_backlog.empty:
        novos = df[~df["link"].isin(df_backlog["link"])].copy()
    else:
        novos = df.copy()

    if not novos.empty:
        novos.insert(0, "added_at", date.today().isoformat())
        df_backlog_novo = pd.concat([df_backlog, novos], ignore_index=True)
        df_backlog_novo.to_excel(backlog_local, index=False, engine="openpyxl")
        update_file(backlog_local, "backlog.xlsx", XLSX_MIME)
        print(f"[ok] Drive: backlog +{len(novos)} novas (total {len(df_backlog_novo)})")
    else:
        print("[ok] Drive: backlog inalterado (nenhuma noticia inedita)")

    return ai_url, len(novos)


def send_email(df: pd.DataFrame, xlsx_path: Path, drive_url: str, novas_backlog: int) -> None:
    if not EMAIL_TO:
        print("[info] sem destinatarios (EMAIL_TO_OVERRIDE / CLIP_RECIPIENTS vazio) — e-mail nao enviado.")
        return
    user = os.environ["EMAIL_REMETENTE"].strip()
    pwd = os.environ["EMAIL_SENHA"].replace(" ", "").strip()

    msg = EmailMessage()
    msg["Subject"] = f"Clipping — Saúde & Educação — {date.today().strftime('%d/%m/%Y')} ({WHEN})"
    msg["From"] = user
    msg["To"] = ", ".join(EMAIL_TO)
    msg.set_content(f"News Scrapper: {len(df)} noticias ({novas_backlog} ineditas).")
    msg.add_alternative(build_email_html(df, len(df), drive_url, novas_backlog), subtype="html")

    if xlsx_path.exists():
        with open(xlsx_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=xlsx_path.name,
            )

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(user, pwd)
        s.send_message(msg)
    print(f"[ok] e-mail enviado para {', '.join(EMAIL_TO)}")


def main() -> None:
    df = fetch_news()

    xlsx_path = Path("news_scrapper.xlsx")
    df.to_excel(xlsx_path, index=False, engine="openpyxl")
    print(f"[ok] {len(df)} noticias salvas em {xlsx_path}")

    txt_path = Path("ai_input.txt")
    txt_path.write_text(build_ai_text(df), encoding="utf-8")
    print(f"[ok] input AI salvo em {txt_path}")

    drive_url, novas_backlog = sync_to_drive(df, xlsx_path, txt_path)
    send_email(df, xlsx_path, drive_url, novas_backlog)


if __name__ == "__main__":
    main()

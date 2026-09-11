"""Executor da VARREDURA DOU no GitHub Actions (ou no runner do PC).

Fluxo: baixa o Regulacao_Cursos.xlsx do Drive -> dou_varredura.varrer(inicio, fim)
-> sobe de volta -> e-mail do relatorio. 100% deterministico, sem IA.

Entradas por variavel de ambiente (setadas pelo workflow varredura.yml):
  VARR_INICIO      AAAA-MM-DD (vazio = usa VARR_DIAS_RETRO)
  VARR_FIM         AAAA-MM-DD (vazio = hoje)
  VARR_DIAS_RETRO  n dias para tras a partir de hoje (para agendamentos: "revarre os
                   ultimos N dias" — o dedup garante que nada duplica)
  VARR_RECIPIENTS  e-mails separados por virgula (vazio = so log, sem e-mail)
Segredos ja existentes do clipping: GOOGLE_CREDENTIALS_JSON, EMAIL_REMETENTE, EMAIL_SENHA.
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ARQUIVO = "Regulacao_Cursos.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _drive():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
        scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _file_id(service):
    files = service.files().list(q=f"name='{ARQUIVO}' and trashed=false",
                                 fields="files(id,name)").execute().get("files", [])
    if not files:
        sys.exit(f"[varredura_ci] '{ARQUIVO}' nao encontrado no Drive — nada a fazer")
    return files[0]["id"]


def main():
    fim = date.fromisoformat(os.environ["VARR_FIM"]) \
        if os.environ.get("VARR_FIM", "").strip() else date.today()
    if os.environ.get("VARR_INICIO", "").strip():
        inicio = date.fromisoformat(os.environ["VARR_INICIO"])
    else:
        retro = int(os.environ.get("VARR_DIAS_RETRO", "").strip() or 30)
        inicio = fim - timedelta(days=retro)

    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    service = _drive()
    fid = _file_id(service)
    local = Path(ARQUIVO)
    with open(local, "wb") as f:
        dl = MediaIoBaseDownload(f, service.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
    print(f"[varredura_ci] baixado do Drive ({local.stat().st_size/1e6:.1f} MB)")

    import dou_varredura
    resumo = dou_varredura.varrer(inicio, fim, str(local))

    service.files().update(
        fileId=fid,
        media_body=MediaFileUpload(str(local), mimetype=XLSX_MIME, resumable=False),
    ).execute()
    url = f"https://drive.google.com/file/d/{fid}/view"
    print(f"[varredura_ci] planilha atualizada no Drive: {url}")

    dou_varredura.enviar_relatorio(resumo, os.environ.get("VARR_RECIPIENTS", ""), url)
    # dia nao coberto = job vermelho no Actions (alem do [INCOMPLETO] no e-mail)
    if resumo["dias_falha"]:
        sys.exit(2)


if __name__ == "__main__":
    main()

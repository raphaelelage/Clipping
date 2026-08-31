"""
Clipping — Saúde & Educação (News Scrapper)
- Coleta multi-fonte (Google News + ANS + Anvisa + Valor RSS + Brazil Stock Guide) via clipping_core
- Decodifica os links do Google News para a URL real do veiculo
- Envia e-mail com XLSX + TXT (input AI) anexados, e sincroniza com o Google Drive
"""

import os
import glob
import re

# =============================================================================
# >>>>>>>>>>>>>>>>>>>>>>>>>  AJUSTE MANUAL AQUI  <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
# =============================================================================
# Periodo de busca:
#   '1h', '2h', '6h', '12h'  -> horas
#   '1d', '2d', '7d'         -> dias
WHEN = os.environ.get("WHEN_OVERRIDE", "").strip() or "1d"

# Vertical: 'saude' (padrao) ou 'educacao'. Define keywords/fontes/prompt e a pasta no Drive.
VERTICAL = (os.environ.get("VERTICAL", "").strip().lower() or "saude")

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

clipping_core.set_vertical(VERTICAL)
VERTICAL = clipping_core.VERTICAL                       # normalizado
VLABEL = clipping_core.VERTICAIS[VERTICAL]["label"]     # 'Saúde' / 'Educação'
VFILES = clipping_core.arquivos_vertical(VERTICAL)
LEGACY_VERTICAL = "saude"   # dona dos arquivos que ficavam soltos na raiz do Drive
DRIVE_PASTA = "Saúde e Educação"   # pasta unica no Drive — as 3 secoes usam os mesmos arquivos


# Preenchido por _juntar_shards() quando algum robo falhou e a fatia dele nao pode ser
# refeita. Vira um aviso no topo do e-mail e no assunto — o clipping vai assim mesmo,
# mas voce sabe que veio incompleto e o que ficou de fora.
AVISO_COLETA = ""

# Radar DOU: frases prontas para o topo do e-mail, preenchidas durante o sync do Drive
# (so nas verticais educacao/saude_educacao). Cada item: {"frase","link","medicina","tipo"}.
RADAR_FRASES: list = []

# Tabela de valuation da cobertura (valuation.py), montada durante o sync do Drive
# e injetada no e-mail logo apos o botao de download. Vazia = sem summary (lista de
# empresas vazia ou fontes fora do ar — o clipping segue normal).
VALUATION_HTML = ""


def _refazer_fatia(n, esperadas):
    """Refaz aqui mesmo a fatia do robo que caiu.

    E mais simples e mais rapido do que reexecutar o job la no Actions: este processo ja tem
    a mesma lista de keywords e o mesmo codigo de busca. So essa fatia e refeita — as outras
    tres ja estao prontas. Custa ~30s.
    """
    kws = clipping_core.fatia_keywords(n, esperadas)
    print(f"[retry] robo {n} falhou — refazendo as {len(kws)} keywords dele aqui", flush=True)
    df, _ = clipping_core._google_news(WHEN, kws=kws, incluir_bsg=False)
    print(f"[retry] robo {n}: {len(df)} itens recuperados", flush=True)
    return df


def _juntar_shards():
    """Junta as fatias do Google News coletadas pelos robos paralelos do Actions.

    Devolve None quando a coleta dividida nao esta em uso (aí o clipping_core busca sozinho,
    do jeito sequencial de sempre — o modo dividido e opcional e reversivel).

    Se faltar alguma fatia, tenta REFAZER a fatia aqui mesmo. So se a segunda tentativa
    tambem falhar o clipping segue incompleto — e nesse caso avisa em cima de tudo (assunto
    do e-mail + faixa vermelha no topo), dizendo exatamente quais palavras-chave ficaram de
    fora. O que nao pode acontecer e sair um clipping incompleto com cara de completo."""
    global AVISO_COLETA
    esperadas = int(os.environ.get("GN_SHARDS", "0") or 0)
    if esperadas <= 0:
        return None
    achadas = sorted(glob.glob("gn_shards/**/gn_shard_*.csv", recursive=True))
    partes = [pd.read_csv(a) for a in achadas]
    nums = sorted(int(re.search(r"gn_shard_(\d+)", a).group(1)) for a in achadas)
    faltando = [n for n in range(1, esperadas + 1) if n not in nums]

    perdidas = []
    for n in faltando:
        try:
            df_n = _refazer_fatia(n, esperadas)
            if len(df_n):
                partes.append(df_n)
            else:
                perdidas.append(n)
        except Exception as e:
            print(f"[retry] robo {n} falhou de novo: {e}", flush=True)
            perdidas.append(n)

    if perdidas:
        kws = [k for n in perdidas for k in clipping_core.fatia_keywords(n, esperadas)]
        AVISO_COLETA = (
            f"Coleta incompleta: {len(perdidas)} de {esperadas} robos do Google News "
            f"falharam e nao foi possivel refazer. Estas {len(kws)} palavras-chave nao "
            f"foram buscadas nesta rodada: {', '.join(kws)}. "
            f"As demais fontes (portais, DOU, CVM, RSS) vieram normalmente.")
        print(f"[AVISO] {AVISO_COLETA}", flush=True)

    df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
    print(f"[ok] coleta dividida: {len(partes)} fatia(s), {len(df)} itens do Google News "
          f"({[len(x) for x in partes]})", flush=True)
    return df


def fetch_news() -> pd.DataFrame:
    """Coleta multi-fonte (clipping_core) e devolve no schema esperado pelo restante do pipeline."""
    df = clipping_core.collect(WHEN, progress=lambda m: print("·", m, flush=True),
                               vertical=VERTICAL, gn_pronto=_juntar_shards())
    cols = ["title", "count_news", "link", "source", "date", "hour",
            "searched_keyword", "source_link", "resumo"]
    if df.empty:
        return df
    return df[cols].reset_index(drop=True)


RED = "#CC092F"   # cor de realce da divisória


def build_email_html(df: pd.DataFrame, total: int, drive_url: str, novas_backlog: int) -> str:
    """HTML do e-mail: título + divisória neutra."""
    header = f"""
    <table width="100%" style="border-collapse:collapse;margin-bottom:8px;">
      <tr>
        <td style="vertical-align:middle;font-family:Arial,sans-serif;font-size:18px;
                   font-weight:bold;color:#111;">
          Clipping {VLABEL}
        </td>
      </tr>
    </table>
    <hr style="border:none;border-top:2px solid {RED};margin:6px 0 18px 0;">
    """

    radar_html = ""
    if RADAR_FRASES:
        from html import escape as _esc
        itens = "".join(
            '<li style="margin:5px 0;">' + ("&#9878;&#65039; " if f["medicina"] else "")
            + _esc(f["frase"]) + ' &nbsp;<a href="' + _esc(f["link"], quote=True)
            + f'" style="color:{RED};font-weight:bold;">ver ato</a></li>'
            for f in RADAR_FRASES[:12])
        rotulo_extra = ("" if len(RADAR_FRASES) <= 12
                        else f'<p style="margin:4px 0 0 0;">e mais {len(RADAR_FRASES)-12} '
                             f'documento(s) — ver planilha no Drive.</p>')
        radar_html = f"""
    <table width="100%" style="border-collapse:collapse;margin:0 0 18px 0;">
      <tr><td style="background:#FDF6EC;border-left:4px solid {RED};padding:10px 12px;
                     font-family:Arial,sans-serif;font-size:13px;color:#4A3B10;">
        <b>&#128225; Radar DOU &mdash; regula&ccedil;&atilde;o de cursos</b>
        <ul style="margin:6px 0 0 18px;padding:0;">{itens}</ul>{rotulo_extra}
      </td></tr>
    </table>
    """

    if AVISO_COLETA:
        header += f"""
    <table width="100%" style="border-collapse:collapse;margin:0 0 18px 0;">
      <tr><td style="background:#FFF3F3;border-left:4px solid {RED};padding:10px 12px;
                     font-family:Arial,sans-serif;font-size:13px;color:#8A1020;">
        <b>&#9888; Atencao — este clipping pode estar incompleto.</b><br>{AVISO_COLETA}
      </td></tr>
    </table>
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
      {header}{meta}{aviso}{VALUATION_HTML}{radar_html}
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
    """Prompt da vertical, editável pelo app (ai_prompt_<vertical>.txt);
    cai no arquivo legado ai_prompt.txt e depois no AI_PROMPT padrão."""
    for nome in (VFILES["prompt"], "ai_prompt.txt"):
        try:
            p = Path(nome)
            if p.exists():
                t = p.read_text(encoding="utf-8").strip()
                if t:
                    return t
        except Exception:
            pass
    return AI_PROMPT.strip()


def build_ai_text(df: pd.DataFrame) -> str:
    """Lista para o AI: titulo + link + os primeiros paragrafos da materia.
    So o titulo costuma nao dizer nada (ex.: DECISAO de 7 de agosto de 2026)."""
    linhas = []
    for i, row in enumerate(df.itertuples(index=False), start=1):
        linhas.append(f"{i}. {row.title} | {row.link}")
        resumo = str(getattr(row, "resumo", "") or "").strip()
        if resumo:
            linhas.append(f"   TRECHO: {resumo}")
    nl = chr(10)
    return _load_ai_prompt() + nl + nl + "NOTÍCIAS:" + nl + nl.join(linhas)


RADAR_DRIVE_NOME = "Regulacao_Cursos.xlsx"
RADAR_SEED = "seed_regulacao_cursos.xlsx"     # levantamento 2018-2026 versionado no repo


def _radar_e_excel(download_file, update_file, xlsx_mime):
    """Radar DOU: detecta atos novos de regulacao de curso, alimenta o Excel do Drive e
    deixa as frases prontas para o topo do e-mail.

    So alerta documento com linha INEDITA no Excel — rodadas seguidas no mesmo dia nao
    repetem o alarme. Se o Drive ainda nao tem o arquivo, comeca da semente versionada
    no repo (o levantamento historico completo)."""
    global RADAR_FRASES
    _bases = clipping_core.BASES_RAIZ.get(VERTICAL, [VERTICAL])
    if "educacao" not in _bases:
        return                      # radar DOU so faz sentido com educacao na heranca
    import dou_alerta
    frases, cru = dou_alerta.coletar_novidades(dias=3, log=lambda m: print(m, flush=True))
    if not frases:
        print("[radar] nenhum ato alarmante nos ultimos dias uteis", flush=True)
        return
    novas = dou_alerta.para_formato_excel(cru)

    local = Path(RADAR_DRIVE_NOME)
    abas_extra = {}
    existentes = None
    if download_file(RADAR_DRIVE_NOME, local):
        try:
            xl = pd.ExcelFile(local)
            existentes = xl.parse("Atos")
            for aba in xl.sheet_names:
                if aba not in ("Atos", "Medicina"):
                    abas_extra[aba] = xl.parse(aba)
        except Exception as e:
            print(f"[radar] arquivo do Drive ilegivel ({e}) — recomecando da semente", flush=True)
    if existentes is None and Path(RADAR_SEED).exists():
        xl = pd.ExcelFile(RADAR_SEED)
        existentes = xl.parse("Atos")
        for aba in xl.sheet_names:
            if aba not in ("Atos", "Medicina"):
                abas_extra[aba] = xl.parse(aba)
        print("[radar] Drive sem o arquivo — comecando da semente do repo", flush=True)
    if existentes is None:
        existentes = novas.iloc[0:0]

    def _chave(d):
        # Celula VAZIA vira NaN na volta do Excel e viraria a string "nan", enquanto do
        # lado recem-coletado ela e "". A chave nunca batia e a linha se declarava inedita
        # TODA rodada (medido: 1 alerta falso por dia). Normaliza os dois lados.
        def _col(nome):
            return (d[nome].fillna("").astype(str).str.strip()
                    .replace({"nan": "", "None": "", "<NA>": ""}))
        return _col("link") + "|" + _col("processo") + "|" + _col("curso")
    ineditas = novas[~_chave(novas).isin(set(_chave(existentes)))]
    links_ineditos = set(ineditas["link"].astype(str))
    RADAR_FRASES = [f for f in frases if str(f["link"]) in links_ineditos]
    if ineditas.empty:
        print("[radar] tudo ja registrado no Excel do Drive — sem alerta novo", flush=True)
        return

    todas = pd.concat([existentes, ineditas], ignore_index=True)
    for c in ("data_pedido", "data_decisao"):
        todas[c] = pd.to_datetime(todas[c], errors="coerce").dt.date
    med = todas[todas["curso"].astype(str).str.contains(r"\bMEDICINA\b", case=False,
                                                        regex=True)
                & ~todas["curso"].astype(str).str.contains("VETERIN", case=False)]
    with pd.ExcelWriter(local, engine="openpyxl",
                        date_format="DD/MM/YYYY", datetime_format="DD/MM/YYYY") as xw:
        todas.to_excel(xw, sheet_name="Atos", index=False)
        med.to_excel(xw, sheet_name="Medicina", index=False)
        for aba, conteudo in abas_extra.items():
            conteudo.to_excel(xw, sheet_name=aba, index=False)
        for aba in ("Atos", "Medicina"):
            ws = xw.book[aba]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
    update_file(local, RADAR_DRIVE_NOME, xlsx_mime)
    print(f"[ok] radar: {len(ineditas)} linha(s) nova(s) no {RADAR_DRIVE_NOME} "
          f"({len(RADAR_FRASES)} documento(s) no alerta do e-mail)", flush=True)


def _valuation_summary(download_file, update_file):
    """Summary de valuation: baixa cache/snapshot do Drive, roda a cadeia BBG > Yahoo >
    cache (valuation.py), guarda o HTML pro e-mail e sobe o cache atualizado."""
    global VALUATION_HTML
    import valuation
    if not valuation.empresas_da_vertical(VERTICAL):
        return
    for nome in ("valuation_cache.json", "bbg_snapshot.json"):
        download_file(nome, Path(nome))          # ok nao existir ainda
    dados = valuation.coletar(VERTICAL, log=lambda m: print(m, flush=True))
    if dados:
        VALUATION_HTML = valuation.tabela_html(dados, red=RED)
        if Path("valuation_cache.json").exists():
            update_file(Path("valuation_cache.json"), "valuation_cache.json",
                        "application/json")


def sync_to_drive(df: pd.DataFrame, xlsx_path: Path, txt_path: Path) -> tuple[str, int]:
    """Atualiza ai_input.txt e news_scrapper.xlsx; adiciona ao backlog so links ineditos.
    Retorna (url do news_scrapper, qtd de noticias novas adicionadas ao backlog)."""
    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    root_id = os.environ["DRIVE_FOLDER_ID"].strip()
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    FOLDER_MIME = "application/vnd.google-apps.folder"

    def _find(name: str, parent: str, mime: str | None = None):
        q = (f"name='{name}' and '{parent}' in parents and trashed=false"
             + (f" and mimeType='{mime}'" if mime else ""))
        files = service.files().list(q=q, fields="files(id,name)").execute().get("files", [])
        return files[0]["id"] if files else None

    # --- pasta UNICA para todas as verticais (as 3 secoes compartilham os mesmos arquivos)
    folder_id = _find(DRIVE_PASTA, root_id, FOLDER_MIME)
    if not folder_id:
        try:
            folder_id = service.files().create(
                body={"name": DRIVE_PASTA, "mimeType": FOLDER_MIME, "parents": [root_id]},
                fields="id").execute()["id"]
            print(f"[ok] Drive: pasta '{DRIVE_PASTA}' criada")
        except Exception as e:
            print(f"[aviso] nao consegui criar a pasta '{DRIVE_PASTA}' ({e}) — usando a raiz.")
            folder_id = root_id
    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    print(f"[ok] Drive ({DRIVE_PASTA}): {folder_url}")

    def find_file_id(name: str, create_from: Path | None = None,
                     mimetype: str = "text/plain") -> str | None:
        """Acha o arquivo na pasta da vertical. Se nao existir:
        1) migra o legado da pasta raiz (metadata-only, sem custo de cota); ou
        2) cria (pode falhar: Service Account nao tem cota propria em conta pessoal)."""
        fid = _find(name, folder_id)
        if fid:
            return fid
        # Os arquivos legados da raiz sao da vertical original (saude) — NUNCA migrar p/ outra
        # vertical, senao o historico da saude iria parar na pasta errada.
        if VERTICAL == LEGACY_VERTICAL and folder_id != root_id:
            legado = _find(name, root_id)
            if legado:
                service.files().update(fileId=legado, addParents=folder_id,
                                       removeParents=root_id, fields="id").execute()
                print(f"[ok] Drive: '{name}' movido da raiz para a pasta '{VLABEL}'")
                return legado
        # Reparo pontual (DRIVE_REPAIR_FROM=<nome da pasta>): traz de volta arquivos que
        # foram parar na pasta errada.
        origem = os.environ.get("DRIVE_REPAIR_FROM", "").strip()
        if origem:
            oid = _find(origem, root_id, FOLDER_MIME)
            perdido = _find(name, oid) if oid else None
            if perdido:
                service.files().update(fileId=perdido, addParents=folder_id,
                                       removeParents=oid, fields="id").execute()
                print(f"[ok] Drive: '{name}' devolvido de '{origem}' para '{VLABEL}'")
                return perdido
        if create_from is not None:
            try:
                fid = service.files().create(
                    body={"name": name, "parents": [folder_id]},
                    media_body=MediaFileUpload(str(create_from), mimetype=mimetype,
                                               resumable=False),
                    fields="id").execute()["id"]
                print(f"[ok] Drive: '{name}' criado em '{VLABEL}'")
                return fid
            except Exception as e:
                print(f"[aviso] nao consegui criar '{name}' no Drive: {e}\n"
                      f"        -> crie um arquivo vazio com esse nome em {folder_url} "
                      f"(uma unica vez) e rode de novo.")
        return None

    def update_file(local_path: Path, drive_name: str, mimetype: str) -> str:
        file_id = find_file_id(drive_name, create_from=local_path, mimetype=mimetype)
        if not file_id:
            return folder_url
        media = MediaFileUpload(str(local_path), mimetype=mimetype, resumable=False)
        service.files().update(fileId=file_id, media_body=media).execute()
        return f"https://drive.google.com/file/d/{file_id}/view"

    def download_file(drive_name: str, local_path: Path) -> bool:
        file_id = find_file_id(drive_name)
        if not file_id:
            return False
        request = service.files().get_media(fileId=file_id)
        with open(local_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return True

    ai_url = update_file(txt_path, "ai_input.txt", "text/plain")
    xlsx_url = update_file(xlsx_path, "news_scrapper.xlsx", XLSX_MIME)

    try:
        _radar_e_excel(download_file, update_file, XLSX_MIME)
    except Exception as e:
        print(f"[radar] erro nao-fatal (clipping segue normal): {e}", flush=True)

    try:
        _valuation_summary(download_file, update_file)
    except Exception as e:
        print(f"[valuation] erro nao-fatal (clipping segue normal): {e}", flush=True)
    if ai_url != folder_url and xlsx_url != folder_url:
        print("[ok] Drive: ai_input.txt e news_scrapper.xlsx atualizados")
    else:
        print(f"[atencao] Drive ({VLABEL}): faltam os arquivos na pasta. Crie uma vez em "
              f"{folder_url} os arquivos vazios: ai_input.txt, news_scrapper.xlsx, backlog.xlsx "
              f"(ou copie os da pasta da outra vertical). Ate la, so o e-mail e o XLSX anexo funcionam.")

    backlog_local = Path("backlog.xlsx")
    tem_backlog = download_file("backlog.xlsx", backlog_local)
    origem_merge = os.environ.get("BACKLOG_MERGE_FROM", "").strip()
    if origem_merge:
        oid = _find(origem_merge, root_id, FOLDER_MIME)
        ofid = _find("backlog.xlsx", oid) if oid else None
        if ofid:
            outro = Path("backlog_outro.xlsx")
            req = service.files().get_media(fileId=ofid)
            with open(outro, "wb") as fh:
                dl = MediaIoBaseDownload(fh, req); done = False
                while not done:
                    _, done = dl.next_chunk()
            try:
                df_o = pd.read_excel(outro)
                df_a = pd.read_excel(backlog_local) if tem_backlog else pd.DataFrame()
                juntos = pd.concat([df_o, df_a], ignore_index=True)
                if "link" in juntos.columns:
                    juntos = juntos.drop_duplicates(subset="link")
                juntos.to_excel(backlog_local, index=False, engine="openpyxl")
                tem_backlog = True
                print(f"[ok] Drive: backlog de '{origem_merge}' mesclado "
                      f"({len(df_o)} + {len(df_a)} -> {len(juntos)} linhas)")
            except Exception as e:
                print(f"[aviso] falha ao mesclar backlog de '{origem_merge}': {e}")
        else:
            print(f"[aviso] backlog.xlsx nao encontrado na pasta '{origem_merge}'")

    if os.environ.get("BACKLOG_RESET", "").strip() in ("1", "true", "sim"):
        # zera o historico desta vertical (usar quando o backlog veio de outra vertical)
        print(f"[atencao] BACKLOG_RESET: zerando o historico de {VLABEL}")
        tem_backlog = False
    try:
        df_backlog = pd.read_excel(backlog_local) if tem_backlog else pd.DataFrame()
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
    prefixo = "[INCOMPLETO] " if AVISO_COLETA else ""
    msg["Subject"] = (f"{prefixo}Clipping {VLABEL} — "
                      f"{date.today().strftime('%d/%m/%Y')} ({WHEN})")
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
    print(f"=== Clipping {VLABEL} | vertical={VERTICAL} | janela={WHEN} | "
          f"keywords={len(clipping_core.keywords)} | fontes={len(clipping_core.WHITELIST)} ===",
          flush=True)
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

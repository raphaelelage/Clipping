# PROMPT PRONTO — atualizar a foto da planilha SERES (pendentes de Medicina)

**Quando usar:** quando a SERES publicar planilhas novas de processos de Medicina
(em tramitação e/ou sobrestados). A foto atual da base é de **04/06/2024**.
Onde conferir se saiu versão nova:
https://www.gov.br/mec/pt-br/assuntos/es/cursos-de-medicina/regulacao-e-supervisao/documentos
(baixe o PDF e olhe a data DENTRO dele — a data da página engana; em 04/2025 a página
dizia "atualizado" mas o PDF continuava sendo o de 07/06/2024.)

**Como usar:** baixe os PDFs novos para a pasta do projeto, abra uma sessão do Claude
Code neste repositório e cole o prompt abaixo (ajuste os nomes dos arquivos).

---

## PROMPT (copiar daqui para baixo)

Preciso atualizar a foto da planilha SERES na base Regulacao_Cursos. Os PDFs novos
estão em: `<CAMINHO_DO_PDF_TRAMITACAO>` e `<CAMINHO_DO_PDF_SOBRESTADOS>`.
A data da nova foto é `<DD/MM/AAAA>` (a data que consta DENTRO do PDF).

Faça exatamente isto, nesta ordem:

1. **Extraia as tabelas dos dois PDFs** para dois CSVs (`seres_tramitacao.csv` e
   `seres_sobrestados.csv`), com no mínimo as colunas `ref_emec` (nº do processo
   e-MEC) e `ies`, e — quando existirem no PDF — `data_protocolo`, `natureza`,
   `tipo_processo`, `regime_juridico`, `ref_sei`, `ref_judicial`, `cod_mantenedora`,
   `mantenedora`, `cod_ies`, `municipio`, `uf`, `regiao_saude`, `cod_curso`, `curso`.
   REGRAS: não invente nada — célula ilegível fica vazia; confira o TOTAL de linhas
   de cada CSV contra o total impresso no PDF e me mostre os dois números.

2. **Rode a cirurgia determinística** (o script já existe e é testado):
   `python atualizar_seres.py --arquivo <planilha.xlsx> --data <DD/MM/AAAA>
    --tramitacao seres_tramitacao.csv --sobrestados seres_sobrestados.csv`
   Ele remove os pendentes da foto antiga da aba Atos, insere os novos com os tipos
   exatos que o funil entende e reconstrói a aba Medicina_SERES.
   (Planilha alvo: baixe a `Regulacao_Cursos.xlsx` oficial do Drive, rode nela e suba
   de volta — ou rode na cópia local e depois espelhe.)

3. **Atualize a data da foto** na constante `ST_SOBRESTADO` do `funil.py`
   (o texto cita "foto de 06/2024") e no que mais citar a data antiga
   (`grep -rn "06/2024\|04/06/2024" *.py ARCHITECTURE.md`).

4. **Regenere o funil** (`python funil.py <planilha.xlsx>`) e confira:
   - nº de "0. Protocolado" + "0. Sobrestado" ≈ totais dos PDFs (a consolidação pode
     fundir pendentes que já foram decididos desde a foto — isso é correto, me mostre
     quantos);
   - nenhuma linha com tipo antigo `pendente:` órfã na aba Atos;
   - matriz status_regulatorio × fase sem status de pendente em fase decidida.

5. **Commite** código + planilha-semente se aplicável, e atualize o ARCHITECTURE.md
   com a nova data da foto.

Restrições permanentes deste projeto: nunca inventar dado; célula preenchida de fonte
externa é amarela com a fonte declarada na nota de cabeçalho; trabalhar sem
multi-agente; patches Python via arquivo (nunca heredoc com barra invertida).

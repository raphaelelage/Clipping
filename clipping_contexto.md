# Setup: Google Drive Service Account

Você só faz isso **uma vez**. Depois é automático.

## 1. Criar projeto no Google Cloud (grátis)

1. Acesse https://console.cloud.google.com/
2. No topo, clique no seletor de projeto → **New Project**
3. Nome: `clipping` (qualquer um). Clique **Create**.
4. Aguarde criar e selecione o projeto.

## 2. Ativar a API do Drive

1. Menu lateral (☰) → **APIs & Services** → **Library**
2. Procure por **Google Drive API** → clique → **Enable**

## 3. Criar a Service Account

1. Menu lateral → **APIs & Services** → **Credentials**
2. Botão **+ CREATE CREDENTIALS** no topo → **Service account**
3. Service account name: `clipping-bot` (qualquer um)
4. Clique **Create and Continue**
5. Pode pular as duas próximas telas (Grant access e Grant users) clicando **Continue** → **Done**

## 4. Gerar a chave JSON

1. Na lista de credenciais, clique no e-mail da service account que apareceu
   (algo como `clipping-bot@clipping-XXXX.iam.gserviceaccount.com`)
2. Aba **KEYS** → **ADD KEY** → **Create new key** → **JSON** → **Create**
3. Um arquivo `.json` baixa automaticamente. **Guarde esse arquivo, ele tem a credencial.**
4. **Copie o e-mail da service account** (na aba Details, campo "Email") — vai usar no próximo passo.

## 5. Criar pasta no seu Drive e compartilhar

1. Vá em https://drive.google.com/
2. **+ New** → **New folder** → nome `Clipping` (ou o que quiser)
3. Clique com botão direito na pasta → **Share** → **Share**
4. Cole o **e-mail da service account** (do passo 4) → mude para **Editor** → clique **Send**
5. Entre na pasta. A URL fica `https://drive.google.com/drive/folders/XXXXXXX` — **copie esse XXXXXXX**, é o ID da pasta.

## 6. Cadastrar secrets no GitHub

No repo `Clipping` → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Nome | Valor |
|---|---|
| `GOOGLE_CREDENTIALS_JSON` | Conteúdo **inteiro** do arquivo `.json` baixado no passo 4 (abra no bloco de notas, Ctrl+A, Ctrl+C, cola aqui) |
| `DRIVE_FOLDER_ID` | O ID da pasta do passo 5 |

## 7. Pronto

Roda **Actions → clipping → Run workflow**. Vai chegar um e-mail com o botão **"Abrir ai_input.txt no Drive"**. O arquivo é criado dentro da pasta `Clipping` do seu Drive e é atualizado a cada execução (o link nunca muda).

## Como usar com o Claude

Em qualquer conversa, com o conector do Drive ativo, é só dizer:
> "Lê o `ai_input.txt` do meu Drive e seleciona as notícias sobre [tema]"

E eu leio direto, sem você copiar nada.

## Custo

Zero. A Service Account não tem cota própria, mas como ela escreve numa pasta da sua conta pessoal (que tem 15GB grátis), o arquivo conta contra a sua cota — um `.txt` de ~50KB nunca chega perto do limite.

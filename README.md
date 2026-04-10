# Dashboard Operações — Setup

Dashboard semanal de operações com dados automáticos do Plane e insights gerados por IA.

## Pré-requisitos

- Conta no GitHub (gratuito)
- Token da API do Plane
- API Key da Anthropic (Claude)

## Ambiente local de teste

Para validar tudo localmente antes de subir para o GitHub Actions:

1. Criar ambiente virtual Python:
   - `python -m venv .venv`
   - `source .venv/bin/activate`
2. Instalar dependências:
   - `pip install -r requirements.txt`
3. Configurar variáveis locais:
   - `cp .env.example .env`
   - preencha os campos no arquivo `.env`
4. Gerar dados:
   - `python scripts/fetch_metrics.py`
5. Visualizar dashboard local:
   - `python -m http.server 8000`
   - abra `http://localhost:8000`

Se faltar alguma env obrigatória, o script agora mostra claramente qual variável precisa ser configurada.

---

## Passo a passo — 15 minutos

### 1. Criar o repositório no GitHub

1. Acesse [github.com](https://github.com) e clique em **New repository**
2. Nome sugerido: `ops-dashboard`
3. Marque **Public** (necessário para GitHub Pages gratuito)
4. Clique em **Create repository**

### 2. Subir os arquivos

Faça upload de todos os arquivos deste projeto para o repositório:
- `index.html`
- `scripts/fetch_metrics.py`
- `.github/workflows/update.yml`
- `data/latest.json` (dados de exemplo para o dashboard funcionar imediatamente)

Você pode arrastar e soltar os arquivos diretamente na interface do GitHub.

### 3. Ativar o GitHub Pages

1. No repositório, vá em **Settings → Pages**
2. Em "Source", selecione **Deploy from a branch**
3. Branch: `main` / Folder: `/ (root)`
4. Clique em **Save**

Aguarde 1-2 minutos. Seu dashboard estará disponível em:
`https://SEU-USUARIO.github.io/ops-dashboard`

### 4. Configurar os secrets (credenciais)

Vá em **Settings → Secrets and variables → Actions → New repository secret**

**Obrigatórios** (sem eles o workflow falha ao rodar o script):

| Nome | Valor |
|------|-------|
| `PLANE_API_TOKEN` | Token do Plane (Settings → API Tokens) |
| `PLANE_WORKSPACE_SLUG` | Slug do workspace (ex: `beemessage`) |
| `PLANE_PROJECT_IDS` | UUIDs dos projetos, separados por vírgula |
| `ANTHROPIC_API_KEY` | API key da Anthropic (Claude) |

**Opcionais** (só crie se quiser OKR/Stripe reais no JSON; se omitir, o script usa fallbacks ou ignora):

| Nome | Valor |
|------|-------|
| `STRIPE_SECRET_KEY` | Secret key do Stripe (MRR, conversão Pro→Enterprise) |
| `BASELINE_MRR` | MRR base numérico para cálculo de crescimento (ex: `10000`) |
| `API_IMPLEMENTATIONS_COUNT` | Número manual de implementações API (padrão no código: 6) |
| `PLANE_OKR_CYCLE_ID` | UUID do ciclo de OKR no Plane |
| `PLANE_OKR_MODULE_IDS` | Reservado para evolução do script; pode ficar vazio |

#### Como encontrar o PLANE_WORKSPACE_SLUG
Na URL do Plane: `app.plane.so/SEU-SLUG/projects/...` — o slug é a parte após `app.plane.so/`.

#### Como encontrar os PLANE_PROJECT_IDS
Na URL de cada projeto: `app.plane.so/workspace/projects/ESTE-UUID-AQUI/issues/`

### 5. Testar manualmente

1. Vá em **Actions** no repositório
2. Abra o workflow **Atualizar dashboard**
3. **Run workflow → Run workflow**
4. Aguarde ~1–3 minutos
5. Confira o commit em `main` em `data/latest.json` e a URL do GitHub Pages (o `index.html` já evita cache com `?v=timestamp` no fetch)

### 6. Agendamento automático

O workflow roda **todo dia às 07:00 (horário de Brasília, `America/Sao_Paulo`)**. O agendamento do GitHub Actions é em **UTC**, por isso o cron é `0 10 * * *` (10:00 UTC ≈ 07:00 BRT).

Para mudar o horário, edite a linha `cron` em [`.github/workflows/update.yml`](.github/workflows/update.yml) convertendo o horário desejado em Brasília para UTC (BRT = UTC−3).

---

## Custo estimado

| Serviço | Custo |
|---------|-------|
| GitHub Pages | Gratuito |
| GitHub Actions | Gratuito (2000 min/mês — você usará ~10 min/mês) |
| Plane API | Gratuito |
| Claude API (insights) | ~$0.01 por execução (centavos) |

**Total mensal: menos de R$ 0,50**

---

## Estrutura dos arquivos

```
ops-dashboard/
├── index.html                    # Dashboard (abre no navegador)
├── data/
│   ├── latest.json               # Dados da semana atual (atualizado pelo script)
│   └── snapshots.json            # Histórico das últimas 12 semanas
├── scripts/
│   └── fetch_metrics.py          # Script que puxa dados do Plane e gera insights
└── .github/
    └── workflows/
        └── update.yml            # Agendamento automático (GitHub Actions)
```

---

## Troubleshooting

**Dashboard não atualiza:**
- Verifique se os secrets foram configurados corretamente
- Acesse Actions → clique no último run → expanda os logs para ver o erro

**Erro de autenticação no Plane:**
- Confirme que o `PLANE_API_TOKEN` tem permissão de leitura no workspace

**Erro na API da Anthropic:**
- Confirme que a `ANTHROPIC_API_KEY` é válida e tem créditos disponíveis

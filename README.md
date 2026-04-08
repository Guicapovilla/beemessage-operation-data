# Dashboard Operações — Setup

Dashboard semanal de operações com dados automáticos do Plane e insights gerados por IA.

## Pré-requisitos

- Conta no GitHub (gratuito)
- Token da API do Plane
- API Key da Anthropic (Claude)

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

Adicione os seguintes secrets:

| Nome | Valor |
|------|-------|
| `PLANE_API_TOKEN` | Seu token do Plane (Settings → API Tokens) |
| `PLANE_WORKSPACE_SLUG` | O slug do seu workspace (ex: `beemessage`) |
| `PLANE_PROJECT_IDS` | IDs dos projetos separados por vírgula (ex: `uuid1,uuid2`) |
| `ANTHROPIC_API_KEY` | Sua API key da Anthropic |

#### Como encontrar o PLANE_WORKSPACE_SLUG
Na URL do Plane: `app.plane.so/SEU-SLUG/projects/...` — o slug é a parte após `app.plane.so/`.

#### Como encontrar os PLANE_PROJECT_IDS
Na URL de cada projeto: `app.plane.so/workspace/projects/ESTE-UUID-AQUI/issues/`

### 5. Testar manualmente

1. Vá em **Actions** no repositório
2. Clique em **Atualizar Dashboard Semanal**
3. Clique em **Run workflow → Run workflow**
4. Aguarde ~2 minutos
5. Acesse sua URL do GitHub Pages — o dashboard estará atualizado

### 6. Agendamento automático

O script já está configurado para rodar toda **sexta-feira às 13h00 (horário de Brasília)**, antes da sua reunião das 14h.

Para alterar o horário, edite a linha `cron` em `.github/workflows/update.yml`:
```
# Formato: minuto hora dia mês dia-da-semana (UTC, BRT = UTC-3)
- cron: "0 16 * * 5"   # sexta 13h BRT
```

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

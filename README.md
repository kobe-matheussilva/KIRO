# KIRO

> Análise mensal de tickets recorrentes do Jira → drafts automáticos no Confluence → resumo no Slack.

KIRO lê tickets fechados do seu projeto Jira, agrupa os recorrentes por similaridade,
gera rascunhos de artigos de Base de Conhecimento com um LLM, salva tudo localmente
para auditoria e — se você quiser — publica no Confluence e avisa o time no Slack.

---

## Como funciona

```
   ┌─────────┐   ┌───────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
   │ Jira API│──▶│Clusterize │──▶│ LLM (Claude) │──▶│ output/ (md, │──▶│ Confluence (opt) │
   │ search  │   │heurístico │   │  draft JSON  │   │ json, report)│   │  + Slack (opt)   │
   └─────────┘   └───────────┘   └──────────────┘   └──────────────┘   └──────────────────┘
```

Cada estágio é independente. Você pode rodar até `fetch`, até `cluster`, até `generate`,
ou tudo. Pode rodar em `--dry-run` para gerar só artefatos locais. Pode rodar sem
Confluence ou sem Slack — KIRO continua produzindo os drafts em `output/drafts/`.

---

## Pré-requisitos

- Python 3.11+
- API token Atlassian (Jira + Confluence usam o mesmo)
- Chave de API de um LLM:
  - **Google Gemini** (default) — https://aistudio.google.com/apikey
  - ou **Anthropic Claude** (alternativa) — https://console.anthropic.com/
- (Opcional) Espaço no Confluence onde a service account tem permissão de criar páginas
- (Opcional) Webhook do Slack

---

## Setup

### Opção recomendada: script de setup

Use o script para automatizar o bootstrap do ambiente:

```bash
bash kiro/setup_env.sh
source .venv/bin/activate
```

O script `kiro/setup_env.sh` faz:

1. escolhe um Python com suporte a `venv` (`PYTHON_BIN` tem prioridade, se definido)
2. cria/recria `.venv` quando necessário
3. ativa o `.venv` durante a execução
4. instala dependências de `requirements.txt`
5. cria `.env` a partir de `.env.example` (se ainda não existir)
6. no macOS/Homebrew, aplica workaround automático para erro de `pyexpat`/`ensurepip`

Modo estrito (recomendado para CI ou troubleshooting):

```bash
PYTHON_BIN=python3.12 STRICT_SETUP=1 bash kiro/setup_env.sh
source .venv/bin/activate
```

Importante:

- Não rode `source kiro/setup_env.sh` no zsh.
- O script é suficiente para preparar o ambiente local (venv + dependências + `.env` inicial).
- Ainda é necessário editar o `.env` com credenciais reais.
- Ao abrir um terminal novo, ative novamente o venv com `source .venv/bin/activate`.

### Como obter o token de API do Jira (Atlassian)

Para conseguir um token de API do Jira (Atlassian), acesse a página oficial de Gerenciamento de Tokens da Atlassian:

- https://id.atlassian.com/manage/api-tokens

Clique em **Criar token de API**, dê um nome para ele e salve o valor imediatamente.

Passo a passo:

1. Acesse o portal da sua conta Atlassian pela página de gerenciamento de conta e entre na aba **Segurança**.
2. Procure a opção **Criar e gerenciar tokens de API** e clique nela.
3. Clique no botão **Criar token de API**.
4. Insira um rótulo para identificar o uso do token (ex.: `Script-Integracao-Jira`) e clique em **Criar**.
5. Copie o token exibido e guarde em local seguro (cofre de senhas), pois ele não poderá ser visualizado novamente após fechar a janela.

Depois disso, preencha no `.env`:

- `JIRA_USER_EMAIL` com o e-mail da conta Atlassian
- `JIRA_API_TOKEN` com o token recém-criado

### Como obter a API key do Google AI Studio (Gemini)

1. Acesse o [Google AI Studio](https://aistudio.google.com/?hl=pt-br).
2. Se necessário, aceite os termos de serviço e faça login na sua conta Google.
3. No menu lateral esquerdo, clique em **Get API key**.
4. Clique em **Create API key**.

Depois disso, preencha no `.env`:

- `LLM_PROVIDER=gemini`
- `LLM_API_KEY` com a chave criada

### Opção manual (equivalente)

```bash
# 1. cria o venv (só na primeira vez)
python3 -m venv .venv

# 2. ativa o venv — IMPRESCINDÍVEL toda vez que abrir um terminal novo
source .venv/bin/activate

# 3. instala dependências (só na primeira vez)
pip install -r requirements.txt

# 4. configura credenciais
cp .env.example .env
# edite o .env com seus valores
```

> ⚠️ **No macOS, `python` sem o `3` não existe por padrão.** Se você vê
> `zsh: command not found: python`, esqueceu de ativar o venv. Rode
> `source .venv/bin/activate` (aparecerá `(.venv)` no prompt) e tente de novo.
>
> Alternativa sem ativar: `.venv/bin/python -m kiro run` (1 linha, mesmo efeito).

Validação rápida da configuração (não chama Jira/LLM):

```bash
python -m kiro config-check
```

---

## Rodando

| Comando | Efeito |
|---|---|
| `python -m kiro run` | Pipeline completo, respeitando flags do `.env` |
| `python -m kiro run --dry-run` | Roda tudo localmente, **não** publica nada |
| `python -m kiro run --stage fetch` | Só busca tickets do Jira |
| `python -m kiro run --stage cluster` | Busca + clusteriza |
| `python -m kiro run --stage generate` | Busca + clusteriza + gera artigos locais |
| `python -m kiro run --stage publish` | Tudo acima + publica no Confluence (se habilitado) |
| `python -m kiro run --publish-confluence` | Força publicação no Confluence nessa execução |
| `python -m kiro run --notify-slack` | Força notificação no Slack nessa execução |
| `python -m kiro run --output-dir custom/` | Muda diretório de saída |

Flags têm precedência sobre o `.env`. `--dry-run` desliga toda publicação externa,
mesmo se as flags `--publish-confluence` ou `--notify-slack` forem passadas.

---

## Demo local (sem Confluence, sem Slack)

```bash
export ENABLE_CONFLUENCE_PUBLISH=false
export ENABLE_SLACK_NOTIFY=false
python -m kiro run --dry-run
```

Ao final, você vai encontrar:

```
output/
├── tickets.json        # tickets normalizados (auditoria)
├── clusters.json       # clusters detectados
├── articles.json       # drafts em estrutura completa
├── drafts/             # cada draft como Markdown legível
│   ├── SUPP-123_como_resolver_login.md
│   └── ...
├── errors.json         # falhas por estágio (vazio se tudo OK)
└── report.md           # relatório executivo da rodada
```

Esses arquivos são exatamente o que você mostra na gravação. Nenhuma integração
externa precisa funcionar para a demo gerar resultado visível.

---

## Habilitando Confluence

1. Garanta que sua service account tem **Create Page** no espaço.
2. No `.env`:

```dotenv
ENABLE_CONFLUENCE_PUBLISH=true
CONFLUENCE_BASE_URL=https://sua-empresa.atlassian.net/wiki
CONFLUENCE_SPACE_KEY=DOC
# CONFLUENCE_PARENT_ID=123456789   # opcional
```

3. Rode:

```bash
python -m kiro run --publish-confluence
```

Se o Confluence falhar (rede, permissão, 5xx), o draft local sobrevive e o erro
fica em `output/errors.json`. O pipeline **não** aborta.

---

## Habilitando Slack

1. Crie um Incoming Webhook em https://api.slack.com/messaging/webhooks.
2. No `.env`:

```dotenv
ENABLE_SLACK_NOTIFY=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/AAA/BBB/CCC
```

3. Rode:

```bash
python -m kiro run --notify-slack
```

---

## Configurando o LLM

KIRO suporta **dois provedores** plugáveis via `LLM_PROVIDER` — Google Gemini (default) e Anthropic Claude. A escolha é feita 100% por configuração, sem alterar código.

### Google Gemini (default)

```dotenv
LLM_PROVIDER=gemini
LLM_API_KEY=sua-chave-gemini
LLM_MODEL=gemini-2.5-flash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
LLM_MAX_TOKENS=1500
LLM_TEMPERATURE=0.3
LLM_TIMEOUT_SECONDS=60
```

**Modelos disponíveis:**

| Modelo | Qualidade | Custo | Latência | Recomendado para |
|---|---|---|---|---|
| `gemini-2.5-pro` | Máxima | Alto | Mais lento | Análises críticas, baixo volume |
| `gemini-2.5-flash` | Alta | Médio | Rápido | **Default — geração de drafts em volume** |
| `gemini-2.5-flash-lite` | Boa | Mínimo | Muito rápido | Alto volume, validações |
| `gemini-2.0-flash` | Alta | Médio | Rápido | Compatibilidade com geração anterior |

A KIRO força `responseMimeType: application/json` no `generationConfig` do Gemini — a resposta vem como JSON estruturado e é validada pelo schema Pydantic `ArticleDraft`. Bloqueios de segurança (`finishReason=SAFETY|RECITATION|PROHIBITED_CONTENT`) e prompts rejeitados (`promptFeedback.blockReason`) viram `LLMResponseError` — o cluster vai para `errors.json` e o pipeline segue.

### Anthropic Claude (alternativa)

```dotenv
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-20250514
LLM_BASE_URL=https://api.anthropic.com/v1
```

### Trocando provedor

Basta alterar `LLM_PROVIDER` no `.env` e reiniciar. `LLM_MODEL` e `LLM_BASE_URL` vazios fazem o KIRO aplicar o default do provedor escolhido — você nunca precisa hardcodar nada.

### Modo dry-run (sem custo)

`python -m kiro run --dry-run` (ou `DRY_RUN=true`) usa um **`MockLLMProvider`** que gera drafts determinísticos a partir dos dados do cluster, **sem chamar a API real** — quota zero, ideal para demo e CI.

### Adicionando um novo provedor

1. Implemente `LLMProvider` em `kiro/application/generation/<provider>_provider.py`.
2. Acrescente o caso na `build_llm_provider` em `factory.py`.
3. Adicione `"<provider>"` no `Literal` de `Settings.llm_provider` e os defaults em `_PROVIDER_DEFAULT_MODEL` / `_PROVIDER_DEFAULT_BASE_URL`.
4. O domínio (`ArticleDraft`) e o pipeline não mudam.

---

## Configurando clustering

| Variável | Default | Significado |
|---|---|---|
| `CLUSTER_MIN_SIZE` | 3 | Mínimo de tickets para um cluster virar artigo |
| `CLUSTER_TOP_N` | 10 | Máximo de clusters processados por rodada |
| `CLUSTER_OVERLAP_THRESHOLD` | 3 | Quantos termos raros precisam ser compartilhados |

Para trocar a estratégia (ex.: embeddings + HDBSCAN), implemente
`ClusteringStrategy` em `kiro/application/clustering/` e plugue no CLI.

---

## Agendamento via GitHub Actions

O workflow em `.github/workflows/monthly.yml` roda no **dia 1 do mês às 08:00 BRT**
(11:00 UTC) e também via `workflow_dispatch` com input `dry_run`.

Configure os Secrets em **Settings → Secrets and variables → Actions**:

| Secret | Obrigatório | Exemplo |
|---|---|---|
| `JIRA_BASE_URL` | sim | `https://empresa.atlassian.net` |
| `JIRA_USER_EMAIL` | sim | service account email |
| `JIRA_API_TOKEN` | sim | token Atlassian |
| `JIRA_PROJECT_KEY` | sim | `SUPP` |
| `LLM_PROVIDER` | opcional | `gemini` (default) ou `anthropic` |
| `LLM_API_KEY` | sim | chave do provedor escolhido |
| `LLM_MODEL` | opcional | default por provedor (ex.: `gemini-2.5-flash`) |
| `LLM_BASE_URL` | opcional | default por provedor (URL raiz da API) |
| `CONFLUENCE_BASE_URL` | só se publicar | `https://empresa.atlassian.net/wiki` |
| `CONFLUENCE_SPACE_KEY` | só se publicar | `DOC` |
| `CONFLUENCE_PARENT_ID` | opcional | id da página pai |
| `SLACK_WEBHOOK_URL` | só se notificar | URL do webhook |

Para mudar o horário, edite o cron (UTC):

```yaml
- cron: "0 11 1 * *"   # min hora dia mês dia-da-semana
```

Referência: https://crontab.guru

---

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Cobre: parser ADF, normalização/tokenização, clusterização heurística, validação
de schema do LLM, settings (incluindo redação de segredos) e persistência.

---

## Arquitetura

```
kiro/
├── domain/          # Modelos imutáveis + exceções
├── application/     # Pipeline + estratégias plugáveis
│   ├── clustering/  # ClusteringStrategy (heuristic)
│   └── generation/  # LLMProvider (anthropic)
├── infrastructure/  # Clientes HTTP + persistência
├── interfaces/      # CLI
├── config/          # Pydantic Settings
└── utils/           # ADF + logging com redação
```

Princípio: dependências apontam para dentro. Trocar estratégia de cluster ou
provedor de LLM não toca em `domain/` nem em `application/pipeline.py`.

---

## Segurança

- Nenhum segredo hardcoded; tudo via env ou `.env` (ignorado pelo git).
- Tokens são `SecretStr` (Pydantic) — nunca aparecem em `repr()`.
- Logging tem filtro que substitui tokens/webhooks por `[REDACTED]`.
- Confluence Storage Format faz escape HTML de tudo que vem do LLM.
- Retries com backoff em todas as chamadas externas.
- `--dry-run` desativa toda escrita externa, garantia em runtime.

---

## Solução de problemas

| Erro | Causa provável | Solução |
|---|---|---|
| `ENABLE_CONFLUENCE_PUBLISH=true exige…` | flag habilitada sem URL/space | Preencha `CONFLUENCE_BASE_URL` e `CONFLUENCE_SPACE_KEY` |
| `busca no Jira falhou: 401` | token inválido ou conta sem permissão | Gere um novo token em https://id.atlassian.com/manage-profile/security/api-tokens |
| `Confluence rejeitou publicação: 400` | space inexistente ou parent_id inválido | Verifique `CONFLUENCE_SPACE_KEY` e `CONFLUENCE_PARENT_ID` |
| `resposta Gemini não é JSON válido` / `resposta Anthropic não é JSON válido` | LLM devolveu prosa em vez de JSON | Reduza `LLM_TEMPERATURE` ou troque `LLM_MODEL`; o cluster vai para `errors.json` mas o pipeline continua |
| `Gemini bloqueou a resposta: finishReason=SAFETY` | filtros de segurança do Gemini bloquearam o conteúdo | Revise os summaries dos tickets do cluster (PII, conteúdo sensível); troque para um modelo mais permissivo se aplicável |
| `Gemini sem candidates (blockReason=...)` | prompt foi bloqueado antes da geração | Mesma orientação acima; verifique também tamanho do prompt vs. `LLM_MAX_TOKENS` |
| `Slack retornou 404` | webhook revogado | Recrie o webhook e atualize `SLACK_WEBHOOK_URL` |
| Nenhum cluster gerado | janela curta ou `MIN_CLUSTER_SIZE` alto | Aumente `LOOKBACK_DAYS` ou baixe `CLUSTER_MIN_SIZE`/`CLUSTER_OVERLAP_THRESHOLD` |
| `configuração inválida` com campos obrigatórios ausentes | `.env` não foi encontrado/carregado ou está incompleto | Rode `bash kiro/setup_env.sh`, confira se `.env` existe na raiz e valide com `python -m kiro config-check` |
| `0 tickets coletados` no fetch | projeto/status/JQL/permissão não retornaram itens na janela | Verifique `JIRA_PROJECT_KEY`, `JIRA_CLOSED_STATUSES`, `LOOKBACK_DAYS`, `JIRA_EXTRA_JQL` e teste no Jira com a mesma JQL |

---

## MVP original

O arquivo `analyzer.py` é o MVP original e foi preservado apenas como referência
funcional. O código de produção vive em `kiro/`.

---

## Licença

Proprietário. Uso interno.

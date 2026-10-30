# 🛡️ Agente de Busca de Vagas — TI e Cibersegurança

Agente em Python que monitora vagas de **estágio e nível iniciante em TI e
cibersegurança** no Brasil e envia alertas automáticos via **Telegram** quando
aparecem vagas novas.

Projeto pessoal desenvolvido para automatizar minha própria busca de estágio
durante o curso de Defesa Cibernética (FATEC).

## O que ele faz

- Consulta a **JSearch API** (agrega LinkedIn, Indeed, Glassdoor, Google Jobs e outros)
- Filtra por **localização** (estado de São Paulo ou remoto)
- Descarta resultados que **não são vagas reais** (notícias/artigos indexados por engano)
- Descarta vagas de **nível sênior/pleno/liderança** e de **áreas não relacionadas**
  (ex.: "Segurança do Trabalho", engenharia civil)
- **Deduplica** vagas já notificadas (usando o link da vaga como identificador estável)
- Envia as vagas novas por **Telegram**, com reenvio automático das que falharem
- Espera a internet ficar disponível e tenta novamente em falhas de rede
- Roda sozinho todos os dias via agendador de tarefas

## Stack

Python 3 · `requests` · `python-dotenv` · JSearch API (OpenWeb Ninja) ·
Telegram Bot API · Windows Task Scheduler

---

## 1. Instalação

```bash
git clone https://github.com/gabrielw1000001-droid/agente-vagas-ciberseguranca.git
cd agente-vagas-ciberseguranca
pip install -r requirements.txt
```

Requer Python 3.8+.

## 2. Configuração

Copie o arquivo de exemplo e preencha com suas credenciais:

```bash
cp .env.example .env
```

> ⚠️ O arquivo `.env` contém chaves pessoais e **nunca deve ser versionado**.
> Ele já está no `.gitignore`.

### 2.1 JSearch API (OpenWeb Ninja)

1. Crie uma conta em https://app.openwebninja.com/signup
2. Assine o plano **Free** da JSearch em https://www.openwebninja.com/api/jsearch
   (200 requisições/mês)
3. Copie sua API Key para `OPENWEBNINJA_API_KEY` no `.env`

### 2.2 Telegram

1. No Telegram, converse com **@BotFather** e envie `/newbot`
2. Escolha um nome e um username (terminado em `bot`)
3. Copie o token recebido para `TELEGRAM_BOT_TOKEN`
4. Envie qualquer mensagem para o **seu bot** (não para o BotFather)
5. Abra `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` no navegador
6. Copie o número de `"chat":{"id": ...}` para `TELEGRAM_CHAT_ID`

### 2.3 Outras opções (`.env`)

| Variável | Descrição |
|---|---|
| `SEARCH_KEYWORDS` | Termos de busca separados por vírgula |
| `SEARCH_COUNTRY` | País (código ISO, ex.: `br`) |
| `DATE_POSTED` | `today`, `3days`, `week` ou `month` |
| `MAX_JOBS_PER_RUN` | Máximo de notificações por execução |
| `FILTER_SAO_PAULO` | `true` para só aceitar vagas de SP ou remotas |

## 3. Executar

```bash
python job_search_agent.py
```

O histórico fica em `agent.log`. Na primeira execução todas as vagas
encontradas contam como "novas".

## 4. Automatizar (Windows)

```powershell
schtasks /create /tn "AgenteVagas" /tr "'C:\caminho\python.exe' 'C:\caminho\job_search_agent.py'" /sc daily /st 08:00
```

No Agendador de Tarefas, marque também *"Executar a tarefa o mais cedo possível
após uma inicialização agendada ter sido perdida"* para rodar mesmo que o PC
esteja desligado às 08:00.

Em Linux/macOS, use `cron`:

```
0 8 * * * cd /caminho/do/projeto && python3 job_search_agent.py
```

## 5. Como funciona

1. Para cada palavra-chave, consulta a API (com retry em falhas de rede)
2. Aplica os filtros: vaga real → nível/área adequados → localização
3. Compara com `seen_jobs.json` para identificar só as vagas novas
4. Envia as novas por Telegram; só marca como "vista" a que foi entregue com sucesso
5. Registra tudo em `agent.log`

## 6. Limitações

- O plano gratuito da API tem 200 requisições/mês (3 palavras-chave × 1 execução/dia cabe com folga)
- Os filtros por título são heurísticos (listas de termos) e podem precisar de ajuste
- Dados de localização vêm das fontes originais e às vezes estão incorretos

## Segurança

- Credenciais ficam apenas no `.env` (fora do Git)
- Arquivos de estado local (`seen_jobs.json`, `agent.log`, `last_run.json`) também são ignorados

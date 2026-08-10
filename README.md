# 🛡️ Agente de Busca de Emprego para Cibersegurança

Busca automaticamente vagas de cibersegurança no Brasil (remoto e presencial)
agregando LinkedIn, Indeed, Glassdoor e Google Jobs através da **JSearch API**,
e te avisa por **WhatsApp** quando encontrar vagas novas.

---

## 1. Instalação

```bash
# Extraia os arquivos em uma pasta, depois:
cd job-agent
pip install -r requirements.txt
```

Requer Python 3.8+.

---

## 2. Configuração

Copie `.env.example` para `.env`:

```bash
cp .env.example .env
```

### 2.1 Chave da JSearch API (gratuita)

1. Crie uma conta em https://rapidapi.com
2. Acesse https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
3. Clique em **Subscribe** e escolha o plano **Basic (gratuito)** — dá direito
   a um número limitado de buscas por mês, suficiente para rodar o agente
   algumas vezes ao dia.
4. Copie sua **X-RapidAPI-Key** (aparece na aba "Endpoints" à direita) e
   cole no `.env` em `RAPIDAPI_KEY`.

### 2.2 WhatsApp via CallMeBot (gratuito)

1. Salve o número **+34 644 59 71 67** nos seus contatos do WhatsApp.
2. Envie para esse número a mensagem exata:
   `I allow callmebot to send me messages`
3. Você vai receber de volta uma mensagem com sua **API Key**.
4. No `.env`, preencha:
   - `WHATSAPP_PHONE`: seu número com código do país, só números
     (ex: `5511999999999`)
   - `CALLMEBOT_APIKEY`: a chave recebida

> ⚠️ CallMeBot é um serviço gratuito mantido por terceiros, ideal para uso
> pessoal. Se ele ficar instável, alternativas são Twilio WhatsApp API
> (paga) ou trocar o alerta para e-mail/Telegram — posso te ajudar a adaptar
> o código se quiser.

### 2.3 Ajustar palavras-chave (opcional)

No `.env`, edite `SEARCH_KEYWORDS` com os termos que fazem sentido para
você, por exemplo:

```
SEARCH_KEYWORDS=cibersegurança,pentester,SOC analyst,red team,blue team,GRC,analista de segurança da informação
```

---

## 3. Testar manualmente

```bash
python job_search_agent.py
```

Verifique o arquivo `agent.log` gerado na pasta para ver o que aconteceu.
Na primeira execução, **todas** as vagas encontradas serão consideradas
"novas" (não há histórico ainda) — isso é esperado.

---

## 4. Automatizar (rodar sozinho periodicamente)

### Linux / macOS (cron)

```bash
crontab -e
```

Adicione uma linha para rodar a cada 6 horas, por exemplo:

```
0 */6 * * * cd /caminho/completo/para/job-agent && /usr/bin/python3 job_search_agent.py >> cron.log 2>&1
```

### Windows (Agendador de Tarefas)

1. Abra o **Agendador de Tarefas** → **Criar Tarefa Básica**
2. Gatilho: recorrência a cada 6 horas (ou o intervalo que preferir)
3. Ação: **Iniciar um programa**
   - Programa: `python.exe` (ou o caminho completo do seu Python)
   - Argumentos: `job_search_agent.py`
   - Iniciar em: caminho completo da pasta `job-agent`

---

## 5. Como funciona por baixo dos panos

1. Para cada palavra-chave em `SEARCH_KEYWORDS`, consulta a JSearch API
   filtrando por país (`SEARCH_COUNTRY=br`) e data de postagem
   (`DATE_POSTED`).
2. Deduplica os resultados por `job_id`.
3. Compara com `seen_jobs.json` (histórico local) para achar só vagas novas.
4. Envia uma mensagem de WhatsApp por vaga nova (limitado por
   `MAX_JOBS_PER_RUN` para não te encher de mensagens de uma vez).
5. Atualiza `seen_jobs.json` para não notificar a mesma vaga de novo.

---

## 6. Limitações e próximos passos possíveis

- **Plano gratuito do RapidAPI** tem limite mensal de requisições — se
  esgotar, o agente vai logar erro até o mês virar (ou você pode fazer
  upgrade do plano).
- **CallMeBot** é pessoal e informal, não é o WhatsApp Business oficial da
  Meta — não use para volume alto de mensagens.
- Posso evoluir isso para: filtrar por nível de senioridade, ranquear vagas
  por match com seu currículo (usando IA), gerar um dashboard web, ou trocar
  o canal de alerta para e-mail/Telegram/Discord. É só pedir.

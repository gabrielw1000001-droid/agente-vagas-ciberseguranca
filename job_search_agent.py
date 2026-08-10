#!/usr/bin/env python3
"""
Agente de Busca de Emprego para Cibersegurança
================================================
Busca vagas de cibersegurança no Brasil (remoto + presencial) usando a
JSearch API (que agrega LinkedIn, Indeed, Glassdoor, Google Jobs, etc.)
e envia alertas de vagas novas via WhatsApp (CallMeBot).

Uso:
    python job_search_agent.py

Para rodar periodicamente, agende via cron (Linux/Mac) ou
Agendador de Tarefas (Windows) — veja o README.md.
"""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "jsearch.p.rapidapi.com"

WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE", "")
CALLMEBOT_APIKEY = os.getenv("CALLMEBOT_APIKEY", "")

SEARCH_KEYWORDS = [
    k.strip() for k in os.getenv("SEARCH_KEYWORDS", "cibersegurança").split(",") if k.strip()
]
SEARCH_COUNTRY = os.getenv("SEARCH_COUNTRY", "br")
DATE_POSTED = os.getenv("DATE_POSTED", "week")
MAX_JOBS_PER_RUN = int(os.getenv("MAX_JOBS_PER_RUN", "8"))
FILTER_SAO_PAULO = os.getenv("FILTER_SAO_PAULO", "true").strip().lower() in ("1", "true", "yes", "sim")

# Intervalo mínimo entre execuções, para não estourar a cota mensal da API caso
# o agendador (cron/Agendador de Tarefas) dispare o script com mais frequência
# do que o pretendido. Com 4 keywords e 1 execução/dia isso consome ~120
# requisições/mês, dentro da cota gratuita da maioria dos planos BASIC.
MIN_HOURS_BETWEEN_RUNS = float(os.getenv("MIN_HOURS_BETWEEN_RUNS", "20"))

SEEN_JOBS_FILE = BASE_DIR / "seen_jobs.json"
LAST_RUN_FILE = BASE_DIR / "last_run.json"
LOG_FILE = BASE_DIR / "agent.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Persistência de vagas já vistas (evita alertas duplicados)
# --------------------------------------------------------------------------

def load_seen_jobs() -> set:
    if SEEN_JOBS_FILE.exists():
        try:
            with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Não foi possível ler seen_jobs.json ({e}), começando do zero.")
    return set()


def save_seen_jobs(seen_ids: set) -> None:
    with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Controle de frequência de execução (protege a cota mensal da API)
# --------------------------------------------------------------------------

def should_skip_due_to_frequency() -> bool:
    """Impede que o agente rode com mais frequência do que MIN_HOURS_BETWEEN_RUNS,
    mesmo que o agendador dispare o script várias vezes. Isso evita estourar a
    cota mensal gratuita da JSearch API por engano."""
    if not LAST_RUN_FILE.exists():
        return False
    try:
        with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        last_run = datetime.fromisoformat(data["last_run"])
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return False

    hours_since = (datetime.now() - last_run).total_seconds() / 3600
    if hours_since < MIN_HOURS_BETWEEN_RUNS:
        log.info(
            f"Última execução foi há {hours_since:.1f}h (mínimo configurado: "
            f"{MIN_HOURS_BETWEEN_RUNS}h). Pulando para economizar cota da API."
        )
        return True
    return False


def mark_run_timestamp() -> None:
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_run": datetime.now().isoformat()}, f)


# --------------------------------------------------------------------------
# Busca de vagas (JSearch API)
# --------------------------------------------------------------------------

class QuotaExceededError(Exception):
    """Levantado quando a JSearch API retorna 429 por cota mensal excedida.
    Usado para interromper o restante das buscas imediatamente, em vez de
    continuar tentando (o que não gasta cota, mas não faz sentido insistir)."""
    pass


def search_jobs(keyword: str, max_retries: int = 3) -> list:
    """Busca vagas para uma palavra-chave usando a JSearch API.
    Tenta novamente automaticamente em caso de erro de conexão/rede."""
    if not RAPIDAPI_KEY or RAPIDAPI_KEY == "coloque_sua_chave_aqui":
        raise RuntimeError(
            "RAPIDAPI_KEY não configurada. Edite o arquivo .env com sua chave do RapidAPI."
        )

    url = "https://jsearch.p.rapidapi.com/search-v2"
    query = f"{keyword} no Brasil"
    params = {
        "query": query,
        "num_pages": "1",
        "date_posted": DATE_POSTED,
        "country": SEARCH_COUNTRY,
    }
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            if resp.status_code == 429 and "monthly quota" in resp.text.lower():
                # Cota mensal do plano estourou. Não adianta tentar de novo nem
                # continuar com as próximas keywords nesta execução.
                log.error(
                    f"Cota mensal da JSearch API excedida ao buscar '{keyword}'. "
                    f"Interrompendo buscas até o próximo ciclo de faturamento do RapidAPI "
                    f"(ou upgrade de plano em rapidapi.com)."
                )
                raise QuotaExceededError(resp.text[:300])
            if resp.status_code != 200:
                # Loga o corpo da resposta de erro para diagnóstico (RapidAPI costuma
                # devolver uma mensagem explicando o motivo, ex: "not subscribed")
                log.error(
                    f"Erro ao buscar vagas para '{keyword}': "
                    f"HTTP {resp.status_code} - {resp.text[:300]}"
                )
                return []
            data = resp.json()
            # A resposta do /search-v2 vem como {"data": {"jobs": [...], "cursor": ...}}
            payload = data.get("data", [])
            if isinstance(payload, dict):
                return payload.get("jobs", [])
            return payload  # fallback, caso a API volte ao formato antigo (lista direta)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < max_retries:
                wait = 10 * attempt  # 10s, depois 20s
                log.warning(
                    f"Falha de conexão ao buscar '{keyword}' (tentativa {attempt}/{max_retries}). "
                    f"Tentando de novo em {wait}s... ({e})"
                )
                time.sleep(wait)
            else:
                log.error(f"Erro ao buscar vagas para '{keyword}' após {max_retries} tentativas: {e}")
                return []
        except requests.exceptions.RequestException as e:
            log.error(f"Erro ao buscar vagas para '{keyword}': {e}")
            return []
    return []


def stable_job_key(job: dict) -> str:
    """Gera uma chave estável para a vaga. O job_id do /search-v2 pode mudar
    entre execuções (é um token de cursor/paginação, não um ID fixo da vaga),
    então usamos o link de candidatura como identificador único e persistente.
    Se não houver link, cai para um hash de título+empresa+cidade."""
    link = (job.get("job_apply_link") or "").strip()
    if link:
        return link
    title = (job.get("job_title") or "").strip().lower()
    employer = (job.get("employer_name") or "").strip().lower()
    city = (job.get("job_city") or "").strip().lower()
    return f"{title}|{employer}|{city}"


# Palavras que aparecem tipicamente em notícias/artigos, não em títulos de vaga real
_NEWS_LIKE_PATTERNS = [
    "r$", "us$", "%", "bilhão", "bilhões", "milhão", "milhões",
    "amplia", "projeta", "cresce", "crescimento", "receita", "lucro",
    "faturamento", "mercado de", "ranking", "pesquisa aponta",
    "estudo mostra", "segundo dados", "relatório", "índice",
]


def is_probably_real_job(job: dict) -> bool:
    """Filtro de qualidade: descarta resultados que a API às vezes retorna por
    engano (notícias, artigos de opinião) misturados com vagas de verdade.
    Vagas reais quase sempre têm um 'job_employment_type' preenchido
    (FULLTIME, INTERN, PARTTIME, etc.) — notícias/artigos não têm."""
    title = (job.get("job_title") or "").lower()
    employment_type = job.get("job_employment_type")

    if not employment_type:
        return False

    if any(pattern in title for pattern in _NEWS_LIKE_PATTERNS):
        return False

    # Títulos de notícia tendem a ser frases longas e descritivas;
    # títulos de vaga raramente passam de ~90 caracteres
    if len(title) > 90:
        return False

    return True


def collect_all_jobs() -> list:
    """Roda a busca para todas as palavras-chave configuradas e deduplica por chave estável.
    Para imediatamente se a cota mensal da API estourar, em vez de continuar
    tentando as keywords restantes."""
    all_jobs = {}
    for kw in SEARCH_KEYWORDS:
        log.info(f"Buscando vagas para: '{kw}'")
        try:
            jobs_raw = search_jobs(kw)
        except QuotaExceededError:
            log.warning(
                f"Interrompendo busca das keywords restantes "
                f"({SEARCH_KEYWORDS.index(kw) + 1}/{len(SEARCH_KEYWORDS)} tentada(s))."
            )
            raise
        log.info(f"  -> {len(jobs_raw)} resultado(s)")
        jobs = [j for j in jobs_raw if is_probably_real_job(j)]
        removed = len(jobs_raw) - len(jobs)
        if removed:
            log.info(f"  -> {removed} descartado(s) por não parecerem vaga real (notícia/artigo)")
        for job in jobs:
            key = stable_job_key(job)
            if key:
                all_jobs[key] = job
        time.sleep(1)  # gentileza com o rate limit da API
    return list(all_jobs.values())


def filter_by_location(jobs: list) -> list:
    """Mantém apenas vagas em São Paulo (estado) ou remotas, se FILTER_LOCATION estiver ativo."""
    if not FILTER_SAO_PAULO:
        return jobs

    filtered = []
    for job in jobs:
        is_remote = bool(job.get("job_is_remote"))
        state = (job.get("job_state") or "").strip().lower()
        city = (job.get("job_city") or "").strip().lower()
        country = (job.get("job_country") or "").strip().lower()

        is_sp = state in ("sp", "são paulo", "sao paulo") or "são paulo" in city or "sao paulo" in city

        if is_remote or is_sp:
            filtered.append(job)
    removed = len(jobs) - len(filtered)
    if removed:
        log.info(f"Filtro de localização removeu {removed} vaga(s) fora de SP/remoto")
    return filtered


# --------------------------------------------------------------------------
# Formatação e envio de notificações (WhatsApp via CallMeBot)
# --------------------------------------------------------------------------

def format_job_message(job: dict) -> str:
    title = job.get("job_title", "Vaga sem título")
    company = job.get("employer_name", "Empresa não informada")
    city = job.get("job_city") or ""
    state = job.get("job_state") or ""
    is_remote = job.get("job_is_remote", False)
    location = "Remoto" if is_remote else ", ".join(filter(None, [city, state])) or "Local não informado"
    link = job.get("job_apply_link", "")

    return (
        f"🛡️ *{title}*\n"
        f"🏢 {company}\n"
        f"📍 {location}\n"
        f"🔗 {link}"
    )


def send_whatsapp(text: str) -> bool:
    if not WHATSAPP_PHONE or not CALLMEBOT_APIKEY:
        log.warning("WHATSAPP_PHONE ou CALLMEBOT_APIKEY não configurados. Pulando envio.")
        return False

    url = "https://api.callmebot.com/whatsapp.php"
    params = {"phone": WHATSAPP_PHONE, "text": text, "apikey": CALLMEBOT_APIKEY}
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Erro ao enviar WhatsApp: {e}")
        return False


# --------------------------------------------------------------------------
# Execução principal
# --------------------------------------------------------------------------

def wait_for_internet(max_wait_minutes: int = 5, check_interval_seconds: int = 15) -> bool:
    """Espera a internet ficar disponível, útil quando a tarefa dispara logo após
    o Windows ligar, antes do Wi-Fi/rede terminar de conectar."""
    deadline = time.time() + (max_wait_minutes * 60)
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            requests.get("https://jsearch.p.rapidapi.com", timeout=5)
            if attempt > 1:
                log.info("Conexão com a internet OK.")
            return True
        except requests.exceptions.RequestException:
            log.warning(
                f"Sem conexão com a internet ainda (tentativa {attempt}). "
                f"Aguardando {check_interval_seconds}s..."
            )
            time.sleep(check_interval_seconds)
    log.error(f"Sem internet após esperar {max_wait_minutes} minuto(s). Abortando execução.")
    return False


def run():
    log.info("=== Iniciando execução do Agente de Busca de Emprego ===")

    if should_skip_due_to_frequency():
        return

    if not wait_for_internet():
        return

    seen_ids = load_seen_jobs()

    try:
        all_jobs = collect_all_jobs()
    except QuotaExceededError:
        # Avisa uma única vez por execução (não fica em silêncio, mas também
        # não spamma o WhatsApp) e encerra sem marcar vagas como vistas.
        send_whatsapp(
            "⚠️ Cota mensal da JSearch API (RapidAPI) esgotada. "
            "As buscas de vaga vão ficar paradas até o próximo ciclo de faturamento "
            "ou até você fazer upgrade do plano em rapidapi.com."
        )
        mark_run_timestamp()
        return

    all_jobs = filter_by_location(all_jobs)
    new_jobs = [j for j in all_jobs if stable_job_key(j) not in seen_ids]

    log.info(f"Total de vagas encontradas: {len(all_jobs)} | Novas: {len(new_jobs)}")

    if not new_jobs:
        log.info("Nenhuma vaga nova. Encerrando.")
        mark_run_timestamp()
        return

    # Limita quantidade por execução para não spammar o WhatsApp
    jobs_to_notify = new_jobs[:MAX_JOBS_PER_RUN]

    header = (
        f"🔔 *{len(new_jobs)} nova(s) vaga(s) de cibersegurança encontradas!*\n"
        f"({datetime.now().strftime('%d/%m/%Y %H:%M')})\n"
    )
    send_whatsapp(header)
    time.sleep(2)

    successfully_notified_ids = set()
    for job in jobs_to_notify:
        msg = format_job_message(job)
        sent = send_whatsapp(msg)
        log.info(f"Notificação {'enviada' if sent else 'FALHOU'}: {job.get('job_title')}")
        if sent:
            successfully_notified_ids.add(stable_job_key(job))
        time.sleep(2)  # evita rate limit do CallMeBot

    if len(new_jobs) > MAX_JOBS_PER_RUN:
        extra = len(new_jobs) - MAX_JOBS_PER_RUN
        send_whatsapp(f"➕ Há mais {extra} vaga(s) nova(s). Rode o log completo para ver todas.")

    # Vagas fora da janela de notificação (acima do limite) também contam como "vistas",
    # para não acumular um backlog gigante. Só as que FALHARAM no envio ficam de fora,
    # para serem re-tentadas na próxima execução.
    ids_beyond_limit = {stable_job_key(j) for j in new_jobs[MAX_JOBS_PER_RUN:]}
    ids_not_new = {stable_job_key(j) for j in all_jobs} - {stable_job_key(j) for j in new_jobs}

    seen_ids.update(successfully_notified_ids)
    seen_ids.update(ids_beyond_limit)
    seen_ids.update(ids_not_new)
    save_seen_jobs(seen_ids)

    failed_count = len(jobs_to_notify) - len(successfully_notified_ids)
    if failed_count:
        log.info(f"{failed_count} vaga(s) falharam no envio e serão re-tentadas na próxima execução.")

    mark_run_timestamp()
    log.info("=== Execução finalizada ===")


if __name__ == "__main__":
    run()

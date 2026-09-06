#!/usr/bin/env python3
"""
Agente de Busca de Emprego para Cibersegurança
================================================
Busca vagas de cibersegurança no Brasil (remoto + presencial) usando a
JSearch API (via OpenWeb Ninja, endpoint direto — agrega LinkedIn, Indeed,
Glassdoor, Google Jobs, etc.) e envia alertas de vagas novas via Telegram
(CallMeBot).

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

OPENWEBNINJA_API_KEY = os.getenv("OPENWEBNINJA_API_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SEARCH_KEYWORDS = [
    k.strip() for k in os.getenv("SEARCH_KEYWORDS", "cibersegurança").split(",") if k.strip()
]
SEARCH_COUNTRY = os.getenv("SEARCH_COUNTRY", "br")
DATE_POSTED = os.getenv("DATE_POSTED", "week")
MAX_JOBS_PER_RUN = int(os.getenv("MAX_JOBS_PER_RUN", "8"))
FILTER_SAO_PAULO = os.getenv("FILTER_SAO_PAULO", "true").strip().lower() in ("1", "true", "yes", "sim")

SEEN_JOBS_FILE = BASE_DIR / "seen_jobs.json"
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
# Busca de vagas (JSearch API)
# --------------------------------------------------------------------------

def search_jobs(keyword: str, max_retries: int = 3) -> list:
    """Busca vagas para uma palavra-chave usando a JSearch API (via OpenWeb Ninja,
    endpoint direto — sem passar pelo RapidAPI). Tenta novamente automaticamente
    em caso de erro de conexão/rede."""
    if not OPENWEBNINJA_API_KEY or OPENWEBNINJA_API_KEY == "coloque_sua_chave_aqui":
        raise RuntimeError(
            "OPENWEBNINJA_API_KEY não configurada. Edite o arquivo .env com sua chave "
            "do OpenWeb Ninja (app.openwebninja.com)."
        )

    url = "https://api.openwebninja.com/jsearch/search-v2"
    query = f"{keyword} no Brasil"
    params = {
        "query": query,
        "num_pages": "1",
        "date_posted": DATE_POSTED,
        "country": SEARCH_COUNTRY,
        "language": "pt",
    }
    headers = {
        "x-api-key": OPENWEBNINJA_API_KEY,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
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
    """Roda a busca para todas as palavras-chave configuradas e deduplica por chave estável."""
    all_jobs = {}
    for kw in SEARCH_KEYWORDS:
        log.info(f"Buscando vagas para: '{kw}'")
        jobs_raw = search_jobs(kw)
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
# Formatação e envio de notificações (Telegram)
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
        f"🛡️ {title}\n"
        f"🏢 {company}\n"
        f"📍 {location}\n"
        f"🔗 {link}"
    )


def send_notification(text: str) -> bool:
    """Envia a notificação via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados. Pulando envio.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        # Sem parse_mode: títulos/empresas vêm de fontes externas e podem conter
        # caracteres como * ou _ que quebrariam o parser de Markdown do Telegram
        # (ex: "entity starting at byte offset X" quando o par não fecha).
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code != 200:
            log.error(f"Erro ao enviar Telegram: HTTP {resp.status_code} - {resp.text[:300]}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Erro ao enviar Telegram: {e}")
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
            requests.get("https://api.openwebninja.com", timeout=5)
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

    if not wait_for_internet():
        return

    seen_ids = load_seen_jobs()

    all_jobs = collect_all_jobs()
    all_jobs = filter_by_location(all_jobs)
    new_jobs = [j for j in all_jobs if stable_job_key(j) not in seen_ids]

    log.info(f"Total de vagas encontradas: {len(all_jobs)} | Novas: {len(new_jobs)}")

    if not new_jobs:
        log.info("Nenhuma vaga nova. Encerrando.")
        return

    # Limita quantidade por execução para não spammar o Telegram
    jobs_to_notify = new_jobs[:MAX_JOBS_PER_RUN]

    header = (
        f"🔔 {len(new_jobs)} nova(s) vaga(s) de cibersegurança encontradas!\n"
        f"({datetime.now().strftime('%d/%m/%Y %H:%M')})\n"
    )
    send_notification(header)
    time.sleep(2)

    successfully_notified_ids = set()
    for job in jobs_to_notify:
        msg = format_job_message(job)
        sent = send_notification(msg)
        log.info(f"Notificação {'enviada' if sent else 'FALHOU'}: {job.get('job_title')}")
        if sent:
            successfully_notified_ids.add(stable_job_key(job))
        time.sleep(2)  # evita rate limit do CallMeBot

    if len(new_jobs) > MAX_JOBS_PER_RUN:
        extra = len(new_jobs) - MAX_JOBS_PER_RUN
        send_notification(f"➕ Há mais {extra} vaga(s) nova(s). Rode o log completo para ver todas.")

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

    log.info("=== Execução finalizada ===")


if __name__ == "__main__":
    run()

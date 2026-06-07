"""
Agents Registry — Multi-Agent-System für Sidekick + Nova.

Jeder Agent hat:
  - Eigene Persönlichkeit (SOUL/Prompt)
  - Eigene Memory-Datenbank (SQLite-Tabelle + optionaler Supermemory-Container)
  - Eigene Sessions (Chat-Verläufe)
  - Eigene Tool/Skill-Allowlist
  - Arbeitsordner (Workdir)
  - Avatar/Emoji + Name

Architektur:
  - SQLite-DB unter STATE_DIR / "agents.db"
  - Agent-Sessions als JSON-Dateien unter STATE_DIR / "agents" / <slug> / "sessions"
  - SOUL.md pro Agent unter STATE_DIR / "agents" / <slug> / "SOUL.md"
  - Integriert mit Nova-Profilen: jeder Agent kann ein Nova-Profil sein
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Pfade ──────────────────────────────────────────────────────────────────
# Wird von routes.py gesetzt (nach Import aus config)
_agents_db_path: Path = None
_agents_data_dir: Path = None
_WEBUI_SESSION_DIR: Path = None
_HERMES_SESSION_DIR: Path = None

# ── DB Lock ─────────────────────────────────────────────────────────────────
_db_lock = threading.Lock()

# ── Standard-Agent-Templates ────────────────────────────────────────────────
AGENT_TEMPLATES = {
    "developer": {
        "name": "Developer Agent",
        "slug": "developer",
        "avatar_emoji": "💻",
        "description": "Vollwertiger Softwareentwickler. Schreibt Code, debuggt, reviewed und refactored. Hat vollen Terminal-/Datei-/Web-Zugriff.",
        "personality": (
            "Du bist ein erfahrener Full-Stack-Softwareentwickler mit Fokus auf Python, JavaScript/TypeScript, "
            "und Web-Technologien. Du denkst in Patterns, Architekturen und Best Practices. "
            "Du schreibst sauberen, getesteten, wartbaren Code. Du hast Zugriff auf das Terminal, das Dateisystem "
            "und das Web — du recherchierst selbstständig, installierst Pakete, führst Tests aus und commitest Code. "
            "Du erklärst deine Entscheidungen während der Arbeit. Du bist pragmatisch: perfekter Code > schneller Code, "
            "aber funktionierender Code > perfekter Code. Du denkst laut und zeigst dem User, was du tust."
        ),
        "color": "#3B82F6",
        "tools": ["terminal", "file", "web", "delegation", "kanban"],
        "workdir_suggestion": "C:\\Users\\logga\\hermes-webui",
    },
    "project-manager": {
        "name": "Projektmanager",
        "slug": "project-manager",
        "avatar_emoji": "📋",
        "description": "Strukturierter Projektmanager. Zerlegt Ziele in Tasks, managed Kanban-Boards, tracked Fortschritt.",
        "personality": (
            "Du bist ein erfahrener, strukturierter Projektmanager mit Scrum- und Kanban-Expertise. "
            "Du zerlegst grosse Ziele in kleine, messbare Tasks (INVEST-Prinzip). Du erstellst Meilensteine, "
            "weist Prioritäten (Eisenhower-Matrix) zu und trackst den Fortschritt. "
            "Du nutzt das Kanban-Board als zentrales Orchestrierungswerkzeug. "
            "Du bist freundlich aber direkt — du sagst wenn etwas im Rückstand ist. "
            "Du kommunizierst klar und erwartest klare Anforderungen. "
            "Deine Superkraft: Aus einem vagen 'ich brauche ein Feature' wird ein detaillierter Umsetzungsplan."
        ),
        "color": "#4ECDC4",
        "tools": ["kanban", "todo", "delegation"],
        "workdir_suggestion": "",
    },
    "social-media": {
        "name": "Social Media Manager",
        "slug": "social-media",
        "avatar_emoji": "📱",
        "description": "Content Creator und Social-Media-Experte. Erstellt Posts, plant Kampagnen, analysiert Engagement.",
        "personality": (
            "Du bist ein kreativer Social-Media-Manager, der alle Plattformen und Trends kennt. "
            "Du erstellst Content-Pläne, schreibst ansprechende Posts, optimierst Hashtags und analysierst Engagement-Daten. "
            "Du kennst die Stimme des Users und bewahrst sie konsistent über alle Kanäle. "
            "Du denkst in Kampagnen, nicht in einzelnen Posts. A/B-Testing, Timing und Audience-Targeting sind dein Handwerk. "
            "Du nutzt das Web für Trend-Recherche und schlägst datengetriebene Optimierungen vor. "
            "Du bist innovativ, testest neue Formate und lernst aus Analytics."
        ),
        "color": "#A855F7",
        "tools": ["web", "search"],
        "workdir_suggestion": "",
    },
    "research": {
        "name": "Research Agent",
        "slug": "research",
        "avatar_emoji": "🔬",
        "description": "Wissenschaftlicher Recherche-Assistent. Durchsucht das Web, analysiert Papers, fasst Forschung zusammen.",
        "personality": (
            "Du bist ein gründlicher Forschungsassistent mit exzellenten Recherche-Fähigkeiten. "
            "Du durchsuchst das Web systematisch nach Informationen, Papers, Daten und Quellen. "
            "Du bewertest Quellen kritisch (Primär vs. Sekundär, Peer-Reviewed vs. Blog, Datum, Bias). "
            "Du fasst komplexe Themen verständlich zusammen und erklärst die Zusammenhänge. "
            "Du fragst nach, wenn eine Recherche-Richtung unklar ist. "
            "Du zitierst Quellen und vermeidest Halbwahrheiten. "
            "Du denkst in Strukturen und Connection-Graphen, nicht in linearen Texten."
        ),
        "color": "#22C55E",
        "tools": ["web", "search", "file"],
        "workdir_suggestion": "C:\\Users\\logga\\projects",
    },
    "sysadmin": {
        "name": "System Administrator",
        "slug": "sysadmin",
        "avatar_emoji": "⚙️",
        "description": "Server-Administrator. Verwaltet SSH, Docker, Cron, Logs und Systemkonfiguration.",
        "personality": (
            "Du bist ein erfahrener Linux/Windows-Systemadministrator mit DevOps-Hintergrund. "
            "Du verwaltest Server, Docker-Container, Cron-Jobs, Logs und Systemkonfigurationen. "
            "Du denkst in Availability, Reliability und Security. "
            "Du automatisierst wiederkehrende Tasks und dokumentierst Änderungen. "
            "Du hast Root-Zugriff auf die Server und nutzt ihn verantwortungsvoll. "
            "Du kommunizierst klar, was du tust und warum. "
            "Backup first, dann Änderung — das ist dein Motto."
        ),
        "color": "#F97316",
        "tools": ["terminal", "file", "web"],
        "workdir_suggestion": "C:\\Users\\logga",
    },
    "data-analyst": {
        "name": "Data Analyst",
        "slug": "data-analyst",
        "avatar_emoji": "📊",
        "description": "Datenanalyst. Verarbeitet Daten, erstellt Visualisierungen, findet Insights.",
        "personality": (
            "Du bist ein erfahrener Datenanalyst mit Python (Pandas, NumPy, Matplotlib) und SQL-Expertise. "
            "Du verarbeitest Rohdaten, bereinigst sie, analysierst sie und erstellst aussagekräftige Visualisierungen. "
            "Du denkst statistisch: Korrelation ≠ Kausalität, Stichprobengrösse, Confounder. "
            "Du präsentierst Ergebnisse klar und ehrlich — auch wenn die Daten nicht das zeigen, was man hören will. "
            "Du nutzt Jupyter-Notebooks, CSV/JSON/XLSX-Dateien und API-Quellen. "
            "Deine Reports sind interaktiv und visuell."
        ),
        "color": "#8B5CF6",
        "tools": ["terminal", "file", "web"],
        "workdir_suggestion": "C:\\Users\\logga\\data",
    },
    "devops": {
        "name": "DevOps Engineer",
        "slug": "devops",
        "avatar_emoji": "🚀",
        "description": "CI/CD-Engineer. Managed Pipelines, Docker, Deployment und Infrastruktur.",
        "personality": (
            "Du bist ein erfahrener DevOps-Ingenieur mit Fokus auf CI/CD, Docker, Kubernetes und Cloud-Infrastruktur. "
            "Du automatisierst alles, was sich wiederholt. Du denkst in Pipelines, nicht in manuellen Schritten. "
            "Du hast ein Gespür für: was kann schiefgehen? Und baust genau dafür Safeguards ein. "
            "Du schreibst Dockerfiles, docker-compose.yml, GitHub Actions und Terraform. "
            "Du bist pragmatisch: Perfektion ist gut, aber Lieferung ist besser. "
            "Du dokumentierst deine Infrastruktur als Code."
        ),
        "color": "#06B6D4",
        "tools": ["terminal", "file", "web", "delegation"],
        "workdir_suggestion": "C:\\Users\\logga\\projects",
    },
    "mentor": {
        "name": "Mentor",
        "slug": "mentor",
        "avatar_emoji": "🎓",
        "description": "Persönlicher Mentor. Erklärt Konzepte, reviewed Code, gibt konstruktives Feedback.",
        "personality": (
            "Du bist ein geduldiger, erfahrener Mentor mit Lehr- und Coaching-Erfahrung. "
            "Du erklärst komplexe Konzepte einfach und mit guten Analogien. "
            "Du gibst konstruktives Feedback — nie verletzend, immer hilfreich. "
            "Du fragst 'Was denkst du?' bevor du die Lösung verrätst. "
            "Du passt deinen Erklärstil an das Niveau des Users an. "
            "Du zeigst nicht nur den richtigen Weg, sondern erklärst auch, warum er richtig ist. "
            "Du feierst Erfolge des Users — jede gelöste Aufgabe ist ein Schritt nach vorne."
        ),
        "color": "#F59E0B",
        "tools": ["web", "search", "file", "terminal"],
        "workdir_suggestion": "",
    },
    "friend": {
        "name": "Freund",
        "slug": "friend",
        "avatar_emoji": "😊",
        "description": "Ein persönlicher Freund. Zuhören, Anteilnahme, ehrliches Feedback — ganz menschlich.",
        "personality": (
            "Du bist ein warmherziger, empathischer Freund. Du erinnerst dich an alles, "
            "was der User dir erzählt hat — seine Hobbys, Sorgen, Erfolge, Beziehungen. "
            "Du fragst nach, zeigst echte Anteilnahme und freust dich aufrichtig über Erfolge. "
            "Deine Sprache ist natürlich, menschlich, manchmal humorvoll. "
            "Du urteilst nicht, aber du gibst ehrliches Feedback — auch wenn es unbequem ist. "
            "Du entwickelst mit der Zeit eine eigene Beziehung zum User und sprichst ihn "
            "auf vergangene Gespräche an. Du vergisst nichts, was wichtig ist."
        ),
        "color": "#FF6B6B",
        "tools": [],
        "workdir_suggestion": "",
    },
    "writer": {
        "name": "Creative Writer",
        "slug": "writer",
        "avatar_emoji": "✍️",
        "description": "Texter und Autor. Blogposts, Stories, Copywriting, Dialoge — alles mit Stil.",
        "personality": (
            "Du bist ein kreativer Texter mit Gespür für Sprache, Rhythmus und Ton. "
            "Du schreibst Blogposts, Social-Media-Copy, Newsletter, Storys, Dialoge, Produktbeschreibungen. "
            "Du findest die richtige Stimme für jedes Projekt — professionell, witzig, poetisch, sachlich. "
            "Du arbeitest iterativ: Entwurf → Feedback → Verfeinerung → Feinschliff. "
            "Du kennst die Regeln des guten Schreibens — und weisst, wann man sie bricht. "
            "Du liebst Sprache und es ist dir wichtig, dass jedes Wort sitzt."
        ),
        "color": "#EC4899",
        "tools": ["file", "web", "search"],
        "workdir_suggestion": "",
    },
}


# ── Datenbank-Initialisierung ───────────────────────────────────────────────

def init_agents_db(data_dir: Path, webui_session_dir: Path, hermes_session_dir: Optional[Path] = None):
    """Initialisiere die Agents-Datenbank. Muss beim Serverstart aufgerufen werden."""
    global _agents_db_path, _agents_data_dir, _WEBUI_SESSION_DIR, _HERMES_SESSION_DIR

    _agents_data_dir = data_dir / "agents"
    _agents_data_dir.mkdir(parents=True, exist_ok=True)
    _agents_db_path = data_dir / "agents.db"
    _WEBUI_SESSION_DIR = webui_session_dir
    _HERMES_SESSION_DIR = hermes_session_dir

    with _db_lock, closing(_get_conn()) as conn:
        _ensure_schema(conn)
        _ensure_templates(conn)

    print(f"  Agents DB: {_agents_db_path} [ok]")
    logger.info(f"Agents DB initialized at {_agents_db_path}")


def _get_conn() -> sqlite3.Connection:
    """Eine frischer SQLite-Connection (innerhalb eines Lock-Blocks verwenden)."""
    conn = sqlite3.connect(str(_agents_db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            slug        TEXT UNIQUE NOT NULL,
            agent_type  TEXT,
            avatar_emoji TEXT DEFAULT '🤖',
            description TEXT DEFAULT '',
            personality TEXT DEFAULT '',
            color       TEXT DEFAULT '#6366F1',
            workdir     TEXT DEFAULT '',
            tools       TEXT DEFAULT '[]',   -- JSON array of tool names
            profile     TEXT DEFAULT '',
            memory_mode TEXT DEFAULT 'local', -- 'local' | 'supermemory'
            status      TEXT DEFAULT 'active', -- 'active' | 'paused'
            is_template INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS agent_memory (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id    TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            key         TEXT NOT NULL,
            value       TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(agent_id, key)
        );

        CREATE TABLE IF NOT EXISTS agent_sessions (
            id              TEXT PRIMARY KEY,
            agent_id        TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            title           TEXT DEFAULT '',
            message_count   INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            last_message_at TEXT
        );

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON agent_memory(agent_id);
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_agent ON agent_sessions(agent_id);

        CREATE TABLE IF NOT EXISTS agent_activity (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_slug  TEXT NOT NULL,
            activity    TEXT NOT NULL,
            details     TEXT DEFAULT '',
            status      TEXT DEFAULT 'done',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_activity_agent_slug ON agent_activity(agent_slug);
        CREATE INDEX IF NOT EXISTS idx_activity_created ON agent_activity(created_at DESC);
    """)


def _ensure_templates(conn: sqlite3.Connection):
    """Stelle sicher, dass alle Standard-Templates existieren."""
    existing = conn.execute("SELECT slug FROM agents WHERE is_template=1").fetchall()
    existing_slugs = {r["slug"] for r in existing}

    for slug, tmpl in AGENT_TEMPLATES.items():
        if slug in existing_slugs:
            continue
        agent_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO agents (id, name, slug, agent_type, avatar_emoji, description,
               personality, color, tools, is_template, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'paused')""",
            (
                agent_id,
                tmpl["name"],
                tmpl["slug"],
                slug,
                tmpl["avatar_emoji"],
                tmpl["description"],
                tmpl["personality"],
                tmpl["color"],
                json.dumps(tmpl["tools"]),
            ),
        )
        # Create agent data directory
        agent_dir = _agents_data_dir / slug
        agent_dir.mkdir(parents=True, exist_ok=True)
        # Write initial SOUL.md
        _write_soul_file(slug, tmpl["personality"], tmpl["name"])

    conn.commit()


def _write_soul_file(slug: str, personality: str, name: str):
    """Schreibe die SOUL.md-Datei für einen Agenten."""
    agent_dir = _agents_data_dir / slug
    agent_dir.mkdir(parents=True, exist_ok=True)
    soul_content = f"""# SOUL.md — {name}

{personality}

---

## Wachstum

Diese SOUL entwickelt sich mit jeder Interaktion weiter. Der Agent lernt dazu,
past Erfahrungen ein und verfeinert seine Persönlichkeit.

## Regeln

1. Sei konsistent in deiner Persönlichkeit
2. Merke dir wichtige Informationen über den User
3. Entwickle eine eigene Beziehung zum User
4. Nutze deine verfügbaren Tools/Skills wenn nötig
5. Reflektiere über vergangene Gespräche

---

*Letzte Aktualisierung: {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""
    (agent_dir / "SOUL.md").write_text(soul_content, encoding="utf-8")


# ── Activity Log ─────────────────────────────────────────────────────────────


def log_activity(agent_slug: str, activity: str, details: str = '', status: str = 'done'):
    """Log an agent activity (chat, command, task, etc.)."""
    with _db_lock, closing(_get_conn()) as conn:
        conn.execute(
            "INSERT INTO agent_activity (agent_slug, activity, details, status) VALUES (?, ?, ?, ?)",
            (agent_slug, activity, details, status)
        )
        conn.commit()


def list_activities(limit: int = 50, agent_slug: str = None) -> list[dict]:
    """Return recent activities."""
    with _db_lock, closing(_get_conn()) as conn:
        if agent_slug:
            rows = conn.execute(
                "SELECT * FROM agent_activity WHERE agent_slug=? ORDER BY created_at DESC LIMIT ?",
                (agent_slug, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_activity ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "agent_slug": r[1],
                "activity": r[2],
                "details": r[3],
                "status": r[4],
                "created_at": r[5],
            })
        return result


def get_agent_stats() -> list[dict]:
    """Return per-agent stats for the dashboard."""
    with _db_lock, closing(_get_conn()) as conn:
        agents = conn.execute(
            "SELECT slug, name, avatar_emoji, color, status, message_count FROM agents WHERE is_template=0 ORDER BY name"
        ).fetchall()
        result = []
        for a in agents:
            slug = a[0]
            session_count = conn.execute(
                "SELECT COUNT(*) FROM agent_sessions WHERE agent_id=(SELECT id FROM agents WHERE slug=?)",
                (slug,)
            ).fetchone()[0]
            last_activity_row = conn.execute(
                "SELECT activity, created_at FROM agent_activity WHERE agent_slug=? ORDER BY created_at DESC LIMIT 1",
                (slug,)
            ).fetchone()
            last_activity = ""
            last_activity_text = ""
            if last_activity_row:
                last_activity_text = last_activity_row[0]
                last_activity = last_activity_row[1]
            result.append({
                "slug": slug,
                "name": a[1],
                "emoji": a[2],
                "color": a[3],
                "status": a[4],
                "message_count": a[5],
                "session_count": session_count,
                "last_activity": last_activity,
                "last_activity_text": last_activity_text,
            })
        return result


# ── CRUD ────────────────────────────────────────────────────────────────────

def list_agents(include_templates: bool = False) -> list[dict]:
    """Liste alle Agenten (optional inkl. Templates)."""
    with _db_lock, closing(_get_conn()) as conn:
        if include_templates:
            rows = conn.execute("SELECT * FROM agents ORDER BY is_template DESC, name ASC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM agents WHERE is_template=0 ORDER BY name ASC").fetchall()
        return [_row_to_dict(r) for r in rows]


def list_activated_agents() -> list[dict]:
    """Liste nur aktivierte (nicht-template) Agenten."""
    with _db_lock, closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM agents WHERE is_template=0 ORDER BY name ASC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_agent(slug: str) -> Optional[dict]:
    """Hole einen Agenten per Slug."""
    with _db_lock, closing(_get_conn()) as conn:
        row = conn.execute("SELECT * FROM agents WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)


def create_agent(name: str, template_slug: str = None, workdir: str = "",
                 tools: list = None) -> dict:
    """Erstelle einen neuen Agenten, optional basierend auf einem Template."""
    slug = _slugify(name)

    with _db_lock, closing(_get_conn()) as conn:
        # Prüfe ob Slug schon existiert
        if conn.execute("SELECT 1 FROM agents WHERE slug=?", (slug,)).fetchone():
            slug = f"{slug}-{int(time.time())}"

        agent_id = str(uuid.uuid4())

        if template_slug and template_slug in AGENT_TEMPLATES:
            tmpl = AGENT_TEMPLATES[template_slug]
            avatar = tmpl["avatar_emoji"]
            description = tmpl["description"]
            personality = tmpl["personality"]
            color = tmpl["color"]
            default_tools = json.dumps(tmpl["tools"])
        else:
            tmpl = None
            avatar = "🤖"
            description = ""
            personality = f"Du bist {name}, ein persönlicher KI-Assistent."
            color = "#6366F1"
            default_tools = json.dumps(tools or [])

        conn.execute(
            """INSERT INTO agents (id, name, slug, agent_type, avatar_emoji,
               description, personality, color, workdir, tools, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                agent_id, name, slug,
                template_slug or "custom",
                avatar, description, personality,
                color, workdir, default_tools,
            ),
        )
        conn.commit()

    # Activity log
    log_activity(slug, f"Agent angelegt: {name}", status='done')

    # Agent data dir + SOUL.md
    agent_dir = _agents_data_dir / slug
    agent_dir.mkdir(parents=True, exist_ok=True)
    _write_soul_file(slug, personality, name)

    return get_agent(slug)


def update_agent(slug: str, updates: dict) -> Optional[dict]:
    """Aktualisiere einen Agenten."""
    allowed_fields = {"name", "avatar_emoji", "description", "personality",
                      "color", "workdir", "tools", "status", "memory_mode", "profile"}

    set_parts = []
    params = []

    for key, value in updates.items():
        if key not in allowed_fields:
            continue
        if key == "tools" and isinstance(value, (list, tuple)):
            value = json.dumps(value)
        set_parts.append(f"{key}=?")
        params.append(value)

    if not set_parts:
        return get_agent(slug)

    set_parts.append("updated_at=datetime('now')")
    params.append(slug)

    with _db_lock, closing(_get_conn()) as conn:
        conn.execute(
            f"UPDATE agents SET {', '.join(set_parts)} WHERE slug=?",
            params,
        )
        conn.commit()

    # Wenn personality geändert wurde, SOUL.md aktualisieren
    if "personality" in updates or "name" in updates:
        agent = get_agent(slug)
        if agent:
            _write_soul_file(slug, agent["personality"], agent["name"])

    return get_agent(slug)


def delete_agent(slug: str) -> bool:
    """Lösche einen Agenten und alle seine Daten."""
    with _db_lock, closing(_get_conn()) as conn:
        # Activity log before deletion
        log_activity(slug, "Agent gelöscht", status='done')
        cur = conn.execute("DELETE FROM agents WHERE slug=? AND is_template=0", (slug,))
        deleted = cur.rowcount > 0
        conn.commit()

    if deleted:
        import shutil
        agent_dir = _agents_data_dir / slug
        if agent_dir.exists():
            shutil.rmtree(agent_dir, ignore_errors=True)

    return deleted


def activate_from_template(slug: str, name: str = None) -> Optional[dict]:
    """Aktiviere ein Template als echten Agenten (macht eine Kopie)."""
    with _db_lock, closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE slug=? AND is_template=1", (slug,)
        ).fetchone()

    if row is None:
        return None

    template = _row_to_dict(row)
    new_name = name or template["name"]

    return create_agent(
        name=new_name,
        template_slug=template["agent_type"],
    )


# ── Memory ──────────────────────────────────────────────────────────────────

def get_agent_memory(agent_slug: str) -> list[dict]:
    """Hole alle gespeicherten Erinnerungen eines Agenten."""
    with _db_lock, closing(_get_conn()) as conn:
        rows = conn.execute(
            """SELECT am.* FROM agent_memory am
               JOIN agents a ON a.id = am.agent_id
               WHERE a.slug = ?
               ORDER BY am.created_at DESC""",
            (agent_slug,),
        ).fetchall()
        return [dict(r) for r in rows]


def set_agent_memory(agent_slug: str, key: str, value: str) -> bool:
    """Setze eine Erinnerung für einen Agenten (upsert)."""
    with _db_lock, closing(_get_conn()) as conn:
        # Find agent id
        row = conn.execute("SELECT id FROM agents WHERE slug=?", (agent_slug,)).fetchone()
        if row is None:
            return False
        conn.execute(
            """INSERT INTO agent_memory (agent_id, key, value) VALUES (?, ?, ?)
               ON CONFLICT(agent_id, key) DO UPDATE SET value=excluded.value""",
            (row["id"], key, value),
        )
        conn.commit()
    return True


def delete_agent_memory(agent_slug: str, key: str) -> bool:
    """Lösche eine Erinnerung eines Agenten."""
    with _db_lock, closing(_get_conn()) as conn:
        row = conn.execute("SELECT id FROM agents WHERE slug=?", (agent_slug,)).fetchone()
        if row is None:
            return False
        conn.execute(
            "DELETE FROM agent_memory WHERE agent_id=? AND key=?",
            (row["id"], key),
        )
        conn.commit()
    return True


# ── Chat/Sessions ───────────────────────────────────────────────────────────

def create_agent_session(agent_slug: str, title: str = "") -> Optional[dict]:
    """Erstelle eine neue Chat-Session für einen Agenten."""
    session_id = str(uuid.uuid4())
    with _db_lock, closing(_get_conn()) as conn:
        row = conn.execute("SELECT id FROM agents WHERE slug=?", (agent_slug,)).fetchone()
        if row is None:
            return None
        conn.execute(
            """INSERT INTO agent_sessions (id, agent_id, title)
               VALUES (?, ?, ?)""",
            (session_id, row["id"], title or f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"),
        )
        conn.commit()
    return {
        "id": session_id,
        "agent_id": agent_slug,
        "title": title or f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "messages": [],
    }


def list_agent_sessions(agent_slug: str) -> list[dict]:
    """Liste alle Sessions eines Agenten."""
    with _db_lock, closing(_get_conn()) as conn:
        rows = conn.execute(
            """SELECT s.* FROM agent_sessions s
               JOIN agents a ON a.id = s.agent_id
               WHERE a.slug = ?
               ORDER BY s.last_message_at DESC, s.created_at DESC""",
            (agent_slug,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_agent_session(agent_slug: str, session_id: str) -> Optional[dict]:
    """Hole eine Session inkl. Messages."""
    session_file = _agents_data_dir / agent_slug / "sessions" / f"{session_id}.json"
    if not session_file.exists():
        return None

    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def append_agent_message(agent_slug: str, session_id: str,
                         role: str, content: str) -> bool:
    """Füge eine Nachricht zu einer Agent-Session hinzu."""
    session_dir = _agents_data_dir / agent_slug / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"{session_id}.json"

    if session_file.exists():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {"id": session_id, "messages": []}
    else:
        data = {"id": session_id, "messages": []}

    msg = {
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat(),
    }
    data.setdefault("messages", []).append(msg)

    session_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Update session metadata in DB
    msg_count = len(data["messages"])
    with _db_lock, closing(_get_conn()) as conn:
        conn.execute(
            "UPDATE agent_sessions SET message_count=?, last_message_at=datetime('now') WHERE id=?",
            (msg_count, session_id),
        )
        # Update agent message count
        conn.execute(
            "UPDATE agents SET message_count = message_count + 1, updated_at = datetime('now') WHERE slug=?",
            (agent_slug,),
        )
        conn.commit()

    return True


# ── Splash / First-Run ─────────────────────────────────────────────────────

def is_splash_completed() -> bool:
    """Hat der User den Splash-Screen bereits gesehen und abgeschlossen?"""
    try:
        with _db_lock, closing(_get_conn()) as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='splash_completed'"
            ).fetchone()
            return row is not None and row["value"] == "1"
    except Exception as exc:
        logger.warning(f"is_splash_completed check failed: {exc}")
        return False


def mark_splash_completed(activated_slugs: list[str]) -> dict:
    """Markiere Splash als abgeschlossen und aktiviere die gewählten Agenten.

    - Template-Slugs (developer, friend, ...) werden aktiviert (Kopie erstellt)
    - Custom-Slugs (bereits existierende Agents) werden direkt übernommen
    - Workspaces werden für alle erstellt
    """
    results = {"activated": [], "errors": []}

    for slug in activated_slugs:
        if slug in AGENT_TEMPLATES:
            agent = activate_from_template(slug)
            if agent:
                results["activated"].append(agent["slug"])
            else:
                results["errors"].append(slug)
        else:
            # Custom agent — bereits in der DB, aber slug trotzdem merken
            agent = get_agent(slug)
            if agent:
                results["activated"].append(slug)
            else:
                results["errors"].append(slug)

    with _db_lock, closing(_get_conn()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('splash_completed', '1')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('splash_completed_at', datetime('now'))"
        )
        conn.commit()

    # Workspaces für aktivierte Agenten initialisieren
    try:
        from web.api.agent_workspace import init_workspaces_for_agents
        workspaces = init_workspaces_for_agents(results["activated"])
        results["workspaces"] = workspaces
    except Exception as e:
        logger.warning(f"Could not init workspaces: {e}")
        results["workspaces"] = {}

    return results


# ── LLM Integration ────────────────────────────────────────────────────────

_LLM_CACHE = {}  # Cache für geladene Config

def _load_llm_config():
    """Load LLM config from active provider context + credential_pool fallback."""
    if _LLM_CACHE.get("config"):
        return _LLM_CACHE["config"]

    try:
        from web.api.config import resolve_active_provider_context

        context = resolve_active_provider_context()
        if context.get("provider"):
            api_key = context.get("api_key") or ""
            model = context.get("model") or ""
            base_url = context.get("base_url") or ""

            # If resolve_active_provider_context returned no api_key (e.g.
            # source="config" with key in credential_pool not in config.yaml),
            # look up the key from the auth store's credential_pool.
            if not api_key or len(api_key) < 8:
                try:
                    import json as _j
                    auth_path = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "auth.json"
                    if auth_path.exists():
                        auth_data = _j.loads(auth_path.read_text(encoding="utf-8"))
                        pool = auth_data.get("credential_pool", {}) if isinstance(auth_data, dict) else {}
                        provider_pool = pool.get(context.get("provider"), [])
                        if isinstance(provider_pool, list) and provider_pool:
                            # Use first credential with valid key
                            for cred in provider_pool:
                                token = cred.get("access_token", "") or ""
                                if len(token) > 8:
                                    api_key = token
                                    if not base_url and cred.get("base_url"):
                                        base_url = cred.get("base_url", "")
                                    break
                except Exception:
                    logger.debug("Failed to look up credential_pool for agent key", exc_info=True)

            config = {
                "provider": context.get("provider"),
                "api_key": api_key,
                "model": model,
                "base_url": base_url,
            }
            _LLM_CACHE["config"] = config
            return config
    except Exception:
        logger.debug("Shared provider context unavailable for agent chat", exc_info=True)

    import re
    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    env_path = hermes_home / ".env"

    api_key = ""
    model = "openai/gpt-oss-20b:free"
    base_url = "https://openrouter.ai/api/v1"

    # Read .env
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^OPENROUTER_API_KEY=(.+)$', line)
            if m:
                api_key = m.group(1).strip().strip('"').strip("'")

    # Fallback: os.environ
    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")

    # Try to read model from config
    config_path = hermes_home / "config.yaml"
    if config_path.exists():
        cfg_text = config_path.read_text(encoding="utf-8")
        mm = re.search(r'default:\s*["\']?(.+?)["\']?\s*$', cfg_text, re.MULTILINE)
        if mm and not mm.group(1).startswith("openai/gpt-oss"):
            model = mm.group(1).strip()

    config = {"api_key": api_key, "model": model, "base_url": base_url}
    _LLM_CACHE["config"] = config
    return config


def _build_llm_messages(agent: dict, memories: list, history_messages: list, max_history: int = 20) -> list:
    """Baue das Messages-Array für den LLM-Call aus Persönlichkeit + Memory + Verlauf."""
    system = agent.get("personality", "").strip()
    if not system:
        system = f"Du bist {agent.get('name', 'ein KI-Assistent')}. Hilf dem User."

    if memories:
        system += "\n\n## Wichtige Erinnerungen über den User:\n"
        for m in memories:
            system += f"- {m.get('key', 'Thema')}: {m.get('value', '')}\n"

    system += "\n\n## Dein Verhalten:\n"
    system += "- Antworte natürlich und menschlich\n"
    system += "- Sei konsistent in deiner Persönlichkeit\n"
    system += "- Wenn dir Informationen fehlen, frag nach\n"
    system += "- Zeige Empathie und Verständnis\n"

    messages = [{"role": "system", "content": system}]

    # Letzte N Nachrichten aus dem Verlauf (ohne die eben gespeicherte User-Nachricht,
    # die wird separat übergeben)
    recent = history_messages[-max_history:] if history_messages else []
    for msg in recent:
        role = "assistant" if msg.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": msg.get("content", "")})

    return messages


def _call_llm(messages: list, timeout: int = 15) -> Optional[str]:
    """Rufe die OpenRouter Chat-Completion API an. Gibt den Antwort-Text zurück oder None.

    Bei HTTP 402/429 (Insufficient credits, Rate limited) wird automatisch
    auf ein Free-Modell (openai/gpt-oss-20b:free) gefallbackt.
    """
    config = _load_llm_config()
    api_key = config.get("api_key", "")
    base_url = config.get("base_url") or "https://openrouter.ai/api/v1"

    if not api_key or len(api_key) < 10:
        logger.warning("No valid OpenRouter API key found")
        return None

    # Modelle in Reihenfolge: primär → free fallback
    models_to_try = [
        config.get("model", "openai/gpt-oss-20b:free"),
        "openai/gpt-oss-20b:free",
        "google/gemini-2.0-flash-001:free",
        "microsoft/phi-3-medium-128k-instruct:free",
    ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Loggableim/sidekick-agent",
        "X-Title": "Sidekick Agents",
    }

    url = f"{base_url}/chat/completions"

    for model in models_to_try:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
            "top_p": 0.9,
        }
        try:
            import urllib.request
            import urllib.error

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:
            code = e.code
            body = e.read().decode()[:200]
            if code in (402, 429):
                logger.warning(f"LLM {model} failed ({code}), trying next free model...")
                continue
            logger.error(f"LLM API HTTP {code}: {body}")
            return None
        except Exception as e:
            logger.error(f"LLM API call failed with {model}: {e}")
            continue

    return None


def chat_with_agent(
    agent_slug: str,
    user_message: str,
    existing_session_id: str = None,
    session_title: str = None,
) -> dict:
    """
    Sende eine Nachricht an einen Agenten und bekomme eine KI-Antwort.
    
    Returns: {
        "session_id": str,
        "response": str|None,
        "error": str|None
    }
    """
    # 1. Agent finden
    agent = get_agent(agent_slug)
    if not agent:
        return {"session_id": None, "response": None, "error": "Agent not found"}

    # 2. Session-ID
    session_id = existing_session_id
    if not session_id:
        session_data = create_agent_session(agent_slug, title=session_title or "")
        if not session_data:
            return {"session_id": None, "response": None, "error": "Could not create session"}
        session_id = session_data["id"]

    # 3. User-Nachricht speichern
    append_agent_message(agent_slug, session_id, "user", user_message)

    # 4. Memory laden
    memories = get_agent_memory(agent_slug)

    config = _load_llm_config()
    api_key = str(config.get("api_key") or "").strip()
    if not api_key or len(api_key) < 10:
        setup_error = {
            "code": "llm_provider_not_configured",
            "message": "Choose an LLM provider and save credentials before starting chat.",
            "setup_required": True,
            "setup_url": "/onboarding",
            "setup_endpoint": "/api/onboarding/status",
        }
        log_activity(agent_slug, f"Chat blocked: {user_message[:80]}", details="provider not configured", status="blocked")
        return {
            "session_id": session_id,
            "response": None,
            "error": setup_error,
            "setup_required": True,
            "setup_url": setup_error["setup_url"],
            "setup_endpoint": setup_error["setup_endpoint"],
        }

    # 5. Chat-Verlauf laden
    session = get_agent_session(agent_slug, session_id)
    history = session.get("messages", []) if session else []

    # 6. Context bauen
    messages = _build_llm_messages(agent, memories, history)

    # 7. LLM aufrufen
    response_text = _call_llm(messages)

    if response_text:
        # 8. Antwort speichern
        append_agent_message(agent_slug, session_id, "assistant", response_text)
        # Activity log
        log_activity(agent_slug, f"Chat: {user_message[:80]}", status='done')
        return {"session_id": session_id, "response": response_text, "error": None}
    else:
        # Fallback wenn LLM nicht antwortet
        fallback = f"Hallo! Ich bin {agent.get('name', 'ein KI-Assistent')}. Leider konnte ich gerade keine Verbindung zum LLM herstellen. Bitte versuch es später noch einmal."
        append_agent_message(agent_slug, session_id, "assistant", fallback)
        return {"session_id": session_id, "response": fallback, "error": "LLM call failed"}


# ── Agent Creator (Splash-Screen Fragebogen) ──────────────────────────────

_AGENT_CREATOR_SYSTEM_PROMPT = """Du bist ein kreativer "Agent Creator". Deine Aufgabe ist es, durch gezielte Fragen den perfekten KI-Agenten für den User zu designen.

## Dein Prozess:
1. Stelle EINE Frage pro Antwort (maximal 5 Runden)
2. Frage nach: Aufgabe/Zweck, Persönlichkeit, Stimme/Ton, Arbeitsbereich, Besonderheiten
3. Wenn du genug Informationen hast (nach 3-5 Antworten), antworte mit einem JSON-Block

## WICHTIG: Wenn du genug weißt, beende mit EXAKT diesem Format:
```json
{
  "done": true,
  "name": "Agentenname",
  "emoji": "🎯",
  "color": "#HEXCOLOR",
  "personality": "Komplette Persönlichkeitsbeschreibung in 3-5 Sätzen auf Deutsch...",
  "tools": ["web", "search"],
  "description": "Kurze 1-Satz-Beschreibung"
}
```

## Regeln:
- Keine JSON-Blöcke vor dem finalen Schritt
- Stelle immer genau EINE Frage
- Frag natürlich und begeisternd
- Passe Fragen an bisherige Antworten an
- Der Name sollte deutsch oder englisch sein, je nach User-Sprache
- Tools wähle basierend auf der Aufgabe: web/search für Recherche, terminal/file für Code, kanban/todo für Projektmanagement
- Die Farbe sollte zur Persönlichkeit passen
"""


def agent_creator_step(answers: list) -> dict:
    """
    Führe einen Schritt im Agent-Creator-Fragebogen aus.
    
    answers: Liste von {"question": str, "answer": str} Dicts
    Returns: {
        "question": str | None,        # Nächste Frage (None wenn fertig)
        "done": bool,                  # True wenn Agent erstellt
        "agent": dict | None,          # Erstellter Agent (wenn done)
        "error": str | None
    }
    """
    # Konversation aufbauen
    messages = [{"role": "system", "content": _AGENT_CREATOR_SYSTEM_PROMPT}]
    
    # Vorhandene Antworten als Kontext
    for i, a in enumerate(answers):
        if a.get("question") and a.get("answer"):
            messages.append({"role": "assistant", "content": a["question"]})
            messages.append({"role": "user", "content": a["answer"]})
    
    # Erste Frage generieren, wenn noch keine Antworten
    if not answers:
        messages.append({
            "role": "user", 
            "content": "Hallo! Ich möchte einen persönlichen KI-Agenten erstellen. Stell mir die erste Frage!"
        })
    
    # LLM aufrufen
    response = _call_llm(messages, timeout=20)
    if not response:
        return {
            "question": "Was soll dein Agent können? Beschreib kurz seinen Zweck.",
            "done": False,
            "agent": None,
            "error": None
        }
    
    # Prüfen ob JSON-Block (fertig) oder Frage
    import re as _re
    json_match = _re.search(r'```json\s*(\{.*?\})\s*```', response, _re.DOTALL)
    
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if data.get("done") and data.get("name"):
                # Agent erstellen
                tools = data.get("tools", [])
                if isinstance(tools, str):
                    tools = [t.strip() for t in tools.split(",") if t.strip()]
                agent = create_agent(
                    name=data["name"],
                    template_slug=None,
                    workdir="",
                    tools=tools,
                )
                # Persönlichkeit, Emoji, Farbe aktualisieren
                update_agent(agent["slug"], {
                    "avatar_emoji": data.get("emoji", "🤖"),
                    "color": data.get("color", "#6366F1"),
                    "personality": data.get("personality", f"Du bist {data['name']}, ein persönlicher KI-Assistent."),
                    "description": data.get("description", f"{data['name']} hilft bei verschiedenen Aufgaben."),
                })
                return {
                    "question": None,
                    "done": True,
                    "agent": get_agent(agent["slug"]),
                    "error": None
                }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Agent creator JSON parse error: {e}")
    
    # Kein JSON → das ist die nächste Frage
    # Clean response: remove any non-question text
    question = response.strip().strip('"\'')
    return {
        "question": question,
        "done": False,
        "agent": None,
        "error": None
    }


# ── Helfer ──────────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Parse JSON tools
    if isinstance(d.get("tools"), str):
        try:
            d["tools"] = json.loads(d["tools"])
        except (json.JSONDecodeError, TypeError):
            d["tools"] = []
    return d


def _slugify(name: str) -> str:
    """Wandle einen Namen in einen URL-freundlichen Slug um."""
    slug = name.lower().strip()
    slug = slug.replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in "-_")
    slug = slug.strip("-_")
    return slug or "agent"


def get_agent_data_dir(slug: str) -> Optional[Path]:
    """Hole das Datenverzeichnis eines Agenten."""
    p = _agents_data_dir / slug
    if p.exists():
        return p
    return None


# ── Current Agent (CLI ↔ WebUI Bridge) ──────────────────────────────────

def get_current_agent_slug() -> Optional[str]:
    """Hole den aktuell aktiven Agenten-Slug (gesetzt von Nova CLI)."""
    with _db_lock, closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='current_agent'"
        ).fetchone()
        if row:
            val = row["value"]
            return val if val else None
    return None


def set_current_agent_slug(slug: Optional[str]):
    """Setze den aktuell aktiven Agenten-Slug (von Nova CLI)."""
    with _db_lock, closing(_get_conn()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('current_agent', ?)",
            (slug or "",),
        )
        conn.commit()

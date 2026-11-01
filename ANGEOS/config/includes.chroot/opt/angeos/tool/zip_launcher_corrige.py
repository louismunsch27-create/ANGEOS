#!/usr/bin/env python3
"""
ZIP Launcher v3
===============
Dépose un ZIP, une archive (tar, 7z, rar), un dossier, un ou plusieurs fichiers
isolés (.py, .js, .html...) ou colle un lien GitHub, puis clique sur Lancer.

- Projets Python  : crée un venv, installe les dépendances, installe
                    automatiquement les modules manquants, lance le bon fichier
                    (gère Streamlit, Flask, FastAPI)
- Projets Node.js : npm / pnpm / yarn install puis start (ou dev)
- Go, Rust (mode direct), sites HTML, scripts .js .sh .jar .rb .php

Par défaut, le programme est lancé dans une MACHINE VIRTUELLE ISOLÉE (conteneur Docker) :
il ne voit que le dossier du projet, pas le reste de ton ordinateur.

Lancement : python zip_launcher.py
"""
import codecs
import collections
import hashlib
import importlib
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
import urllib.request
import uuid
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlparse

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
except ImportError:  # tkinter absent
    tk = None

APPS_DIR = Path.home() / "ZipLauncher"
CONFIG_NAME = ".ziplauncher.json"
ENV_MARK = "# généré par ZIP Launcher"
IS_WIN = sys.platform.startswith("win")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PY_ENTRYPOINTS = ["main.py", "app.py", "bot.py", "run.py", "start.py", "index.py", "server.py"]
NODE_ENTRYPOINTS = ["index.js", "main.js", "app.js", "server.js", "bot.js"]
TAR_EXTS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
ARCHIVE_EXTS = (".zip", ".7z", ".rar") + TAR_EXTS
# fichiers isolés (hors Python) : extension -> commande de lancement
SCRIPT_RUNNERS = {
    ".js": ["node"], ".mjs": ["node"], ".sh": ["bash"], ".jar": ["java", "-jar"],
    ".go": ["go", "run"], ".rb": ["ruby"], ".php": ["php"],
    ".bat": ["cmd", "/c"], ".cmd": ["cmd", "/c"],
    ".pl": ["perl"], ".lua": ["lua"], ".ps1": ["pwsh", "-File"],
    ".r": ["Rscript"], ".swift": ["swift"], ".exs": ["elixir"],
    ".ex": ["elixir"], ".dart": ["dart", "run"], ".ts": ["npx", "--yes", "tsx"],
    ".mjs": ["node"], ".cjs": ["node"], ".jsx": ["npx", "--yes", "tsx"],
    ".kt": ["kotlin"], ".kts": ["kotlin"], ".scala": ["scala"],
    ".pl6": ["raku"], ".raku": ["raku"], ".clj": ["clojure", "-M"],
    ".groovy": ["groovy"], ".jl": ["julia"], ".m": ["octave", "--quiet"],
    ".tcl": ["tclsh"], ".awk": ["awk", "-f"],
    ".fish": ["fish"], ".zsh": ["zsh"],
}

# --- machine virtuelle (Docker) ---
DOCKER_IMAGES = {"python": "python:3.12-slim", "node": "node:20-slim", "go": "golang:1.22"}
SANDBOX_SCRIPTS = {
    ".js": ("node:20-slim", ["node"]), ".mjs": ("node:20-slim", ["node"]),
    ".sh": ("debian:stable-slim", ["bash"]), ".jar": ("eclipse-temurin:21-jre", ["java", "-jar"]),
    ".go": ("golang:1.22", ["go", "run"]), ".rb": ("ruby:3-slim", ["ruby"]), ".php": ("php:8-cli", ["php"]),
}
GO_ENV = {"GOPATH": "/app/.gopath", "GOCACHE": "/app/.gocache", "GOFLAGS": "-buildvcs=false"}
DEFAULT_PORTS = "8000,8080,5000,3000,5173,8501,7860"

# nom d'import -> nom du paquet pip (quand ils diffèrent)
PIP_NAMES = {
    "cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml", "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4", "sklearn": "scikit-learn", "discord": "discord.py",
    "telegram": "python-telegram-bot", "dateutil": "python-dateutil", "serial": "pyserial",
    "Crypto": "pycryptodome", "jwt": "pyjwt", "magic": "python-magic", "git": "gitpython",
    "attr": "attrs", "docx": "python-docx", "pptx": "python-pptx", "fitz": "pymupdf",
    "skimage": "scikit-image", "OpenSSL": "pyopenssl", "nacl": "pynacl", "socks": "pysocks",
    "psycopg2": "psycopg2-binary", "flask_cors": "flask-cors", "flask_sqlalchemy": "flask-sqlalchemy",
    "zmq": "pyzmq", "usb": "pyusb", "win32api": "pywin32", "pkg_resources": "setuptools",
    "speech_recognition": "SpeechRecognition", "MySQLdb": "mysqlclient",
}

URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?[^\s'\"<>)\]]*")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
GITHUB_RE = re.compile(
    r"github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?(?:/tree/([^\s#?]+?))?/?(?:[#?].*)?$"
)
IGNORE_COPY = shutil.ignore_patterns(".venv", ".venv-docker", "node_modules", "__pycache__", ".git")


class StopRequested(Exception):
    """L'utilisateur a cliqué sur Arrêter."""


# =====================================================================
#  Petits utilitaires
# =====================================================================
def python_exe():
    """python.exe plutôt que pythonw.exe (qui n'a pas de console)."""
    exe = Path(sys.executable)
    if IS_WIN and exe.name.lower().startswith("pythonw"):
        alt = exe.with_name("python.exe")
        if alt.exists():
            return str(alt)
    return str(exe)


def safe_name(name):
    """Nom de dossier sûr : jamais vide, jamais '.' ou '..'."""
    name = re.sub(r"[^\w.\-+]+", "_", name).strip("._ ")
    return name[:80] or "projet"


def rmtree_force(path):
    """Supprime un dossier même avec des fichiers en lecture seule (Windows)."""
    def handler(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except Exception:  # noqa: BLE001
            pass
    try:
        shutil.rmtree(path, onexc=handler)  # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=handler)
    if Path(path).exists():
        raise RuntimeError(f"Impossible de supprimer {path} (un fichier est peut-être utilisé).")


# =====================================================================
#  Moteur (sans interface graphique)
# =====================================================================
class Launcher:
    def __init__(self, log, on_url=None):
        self.log = log  # log(texte, end="\n")
        self.on_url = on_url
        self.process = None
        self.stopped = False
        self.tail = collections.deque(maxlen=200)
        self.seen_urls = set()
        self.container = None
        self.published = None  # ports ouverts vers le PC (mode machine virtuelle)
        self.debug_log = APPS_DIR / "debug.log"
        try:
            APPS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def debug(self, message):
        """Ajoute une entrée au journal de diagnostic sans interrompre le programme."""
        try:
            from datetime import datetime
            with self.debug_log.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\\n")
        except OSError:
            pass

    # ---------- Import ----------
    @staticmethod
    def resolve_root(path):
        """GitHub met tout dans un dossier racine unique : on y descend."""
        entries = [p for p in path.iterdir() if not p.name.startswith(("__MACOSX", "."))]
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return path

    @staticmethod
    def fetch(url, dest):
        req = urllib.request.Request(url, headers={"User-Agent": "ZipLauncher"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)

    def download(self, url):
        """Télécharge un lien GitHub ou un lien direct .zip. Retourne (zip_temporaire, nom)."""
        fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="ziplauncher-")
        os.close(fd)
        tmp = Path(tmp)
        try:
            path = urlparse(url).path
            if path.lower().endswith(".zip"):  # lien direct (y compris /archive/...zip de GitHub)
                self.log("⬇️  Téléchargement...")
                self.fetch(url, tmp)
                return tmp, Path(path).stem
            m = GITHUB_RE.search(url)
            if not m:
                raise RuntimeError("Lien non reconnu. Exemple : https://github.com/utilisateur/projet "
                                   "(ou un lien direct vers un .zip)")
            user, repo, branch = m.groups()
            if branch:
                refs = [f"refs/heads/{branch}", f"refs/tags/{branch}", branch]
                if "/" in branch:
                    refs.append(f"refs/heads/{branch.split('/')[0]}")
            else:
                refs = ["HEAD", "refs/heads/main", "refs/heads/master"]
            self.log(f"⬇️  Téléchargement de {user}/{repo}...")
            last = None
            for ref in refs:
                try:
                    self.fetch(f"https://codeload.github.com/{user}/{repo}/zip/{ref}", tmp)
                    return tmp, repo
                except Exception as e:  # noqa: BLE001
                    last = e
            raise RuntimeError(f"Téléchargement impossible (dépôt privé, introuvable ou pas d'Internet ?) : {last}")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def staging_dir(self):
        APPS_DIR.mkdir(parents=True, exist_ok=True)
        d = APPS_DIR / f".tmp-{uuid.uuid4().hex[:8]}"
        d.mkdir()
        return d

    @staticmethod
    def commit(staging, name):
        """Remplace l'ancien projet du même nom par le nouveau (préparé à côté)."""
        target = APPS_DIR / safe_name(name)
        if target.resolve().parent != APPS_DIR.resolve():
            raise RuntimeError("Nom de projet invalide.")
        if target.exists():
            rmtree_force(target)
        staging.rename(target)
        return target

    def extract_archive(self, path, target):
        low = path.name.lower()
        if low.endswith(".zip"):
            base = target.resolve()
            with zipfile.ZipFile(path) as z:
                for member in z.infolist():
                    dest = (target / member.filename).resolve()
                    if dest != base and base not in dest.parents:
                        raise RuntimeError(f"Archive ZIP suspecte (chemin interdit) : {member.filename}")
                    mode = (member.external_attr >> 16) & 0o170000
                    if mode == 0o120000:
                        raise RuntimeError(f"Archive ZIP suspecte (lien symbolique refusé) : {member.filename}")
                z.extractall(target)
        elif low.endswith(TAR_EXTS):
            with tarfile.open(path) as t:
                try:
                    t.extractall(target, filter="data")
                except TypeError:  # anciennes versions de Python : on vérifie à la main
                    base = target.resolve()
                    for m in t.getmembers():
                        dest = (target / m.name).resolve()
                        if dest != base and base not in dest.parents:
                            raise RuntimeError("Archive suspecte (chemin interdit).")
                        if m.issym() or m.islnk():
                            ref = dest.parent / m.linkname if m.issym() else target / m.linkname
                            ref = ref.resolve()
                            if ref != base and base not in ref.parents:
                                raise RuntimeError("Archive suspecte (lien vers l'extérieur).")
                    t.extractall(target)
        else:  # .7z / .rar : on passe par un outil installé sur le PC
            args_7z = ["x", "-y", f"-o{target}", str(path)]
            tools = [("7z", args_7z), ("7za", args_7z), ("7zz", args_7z),
                     ("unrar", ["x", "-y", str(path), str(target) + os.sep]),
                     ("tar", ["-xf", str(path), "-C", str(target)])]
            for tool, args in tools:
                exe = shutil.which(tool)
                if exe and subprocess.run([exe] + args, capture_output=True,
                                          creationflags=NO_WINDOW).returncode == 0:
                    return
            raise RuntimeError("Impossible d'extraire cette archive. Installe 7-Zip "
                               "(https://www.7-zip.org) ou convertis-la en .zip.")

    def import_source(self, source):
        """Lien, archive, dossier ou fichier(s) isolé(s). Retourne le dossier du projet."""
        items = source if isinstance(source, (list, tuple)) else [source]
        items = [str(i).strip().strip('"') for i in items if str(i).strip()]
        if not items:
            raise RuntimeError("Aucune source indiquée.")
        staging = self.staging_dir()  # tout est préparé à côté, puis échangé : rien n'est perdu en cas d'échec
        tmp_zip = None
        try:
            if len(items) == 1 and items[0].lower().startswith(("http://", "https://")):
                tmp_zip, name = self.download(items[0])
                self.log("📂 Extraction...")
                self.extract_archive(tmp_zip, staging)
            else:
                srcs = [Path(i).expanduser() for i in items]
                for s in srcs:
                    if not s.exists():
                        raise RuntimeError(f"Introuvable : {s}")
                one = srcs[0]
                if len(srcs) == 1 and one.is_file() and one.name.lower().endswith(ARCHIVE_EXTS):
                    name = re.sub(r"(\.tar)?\.[^.]+$", "", one.name)
                    self.log(f"📂 Extraction de {one.name}...")
                    self.extract_archive(one, staging)
                elif len(srcs) == 1 and one.is_dir():
                    src, apps = one.resolve(), APPS_DIR.resolve()
                    if src == apps or src in apps.parents:
                        raise RuntimeError("Ce dossier contient ZipLauncher lui-même : "
                                           "choisis le dossier précis du projet.")
                    if apps in src.parents:  # déjà installé : on le lance tel quel
                        self.log(f"📁 Projet déjà présent : {src}")
                        return self.resolve_root(src)
                    name = src.name
                    self.log(f"📁 Copie du dossier {name}...")
                    shutil.copytree(src, staging, dirs_exist_ok=True, ignore=IGNORE_COPY,
                                    symlinks=not IS_WIN, ignore_dangling_symlinks=True)
                else:
                    name = one.stem + (f"+{len(srcs) - 1}" if len(srcs) > 1 else "")
                    self.log(f"📄 Copie de {len(srcs)} fichier(s)...")
                    for s in srcs:
                        if s.is_dir():
                            shutil.copytree(s, staging / s.name, dirs_exist_ok=True, ignore=IGNORE_COPY,
                                            symlinks=not IS_WIN, ignore_dangling_symlinks=True)
                        else:
                            shutil.copy2(s, staging / s.name)
            project = self.resolve_root(self.commit(staging, name))
        finally:
            if staging.exists():
                try:
                    rmtree_force(staging)
                except RuntimeError:
                    pass
            if tmp_zip:
                tmp_zip.unlink(missing_ok=True)
        self.log(f"✅ Prêt dans {project}")
        return project

    # ---------- Config ----------
    @staticmethod
    def load_config(project):
        try:
            return json.loads((project / CONFIG_NAME).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def save_config(project, cfg):
        try:
            (project / CONFIG_NAME).write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def env_template(project):
        """Si le projet a un .env.example, retourne son contenu utile."""
        for name in (".env.example", ".env.sample", "example.env"):
            f = project / name
            if f.is_file():
                lines = []
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        lines.append(line.replace("export ", "", 1))
                return "\n".join(lines)
        return ""

    @staticmethod
    def parse_env_text(env_text):
        out = {}
        for line in env_text.splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out

    @staticmethod
    def build_env(env_text):
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"})
        env.update(Launcher.parse_env_text(env_text))
        return env

    @staticmethod
    def sync_env_file(project, env_text):
        """Écrit le .env du projet sans écraser celui d'origine (sauvegardé en .env.original)."""
        f = project / ".env"
        ours = f.exists() and ENV_MARK in f.read_text(encoding="utf-8", errors="ignore")
        if env_text.strip():
            if f.exists() and not ours and not (project / ".env.original").exists():
                shutil.copy2(f, project / ".env.original")
            f.write_text(ENV_MARK + "\n" + env_text.strip() + "\n", encoding="utf-8")
        elif ours:
            f.unlink()

    @staticmethod
    def file_hash(files):
        h = hashlib.sha256()
        for f in files:
            if f.exists():
                h.update(f.read_bytes())
        return h.hexdigest()

    # ---------- Exécution de processus ----------
    def scan_urls(self, text):
        for m in URL_RE.finditer(text):
            url = m.group(0).rstrip(".,;:!?'\"").replace("0.0.0.0", "localhost")
            if url in self.seen_urls:
                continue
            self.seen_urls.add(url)
            try:
                port = urlparse(url).port
            except ValueError:
                port = None
            if self.published is not None and port is not None and port not in self.published:
                self.log(f"💡 Le port {port} n'est pas accessible depuis ton PC (port non ouvert ou Internet coupé). "
                         "Ajoute-le dans « Ports à ouvrir » puis relance.")
                continue
            if self.on_url:
                self.on_url(url)

    def handle_line(self, line, shown=0):
        clean = ANSI_RE.sub("", line)
        self.tail.append(clean)
        self.log(ANSI_RE.sub("", line[shown:]))
        self.scan_urls(clean)

    def run_cmd(self, cmd, cwd, env, shell=False, interactive=False):
        if self.stopped:
            raise StopRequested()
        self.tail = collections.deque(maxlen=200)
        if shell:
            self.log(f"$ {cmd}")
        else:
            cmd = [str(c) for c in cmd]
            exe = shutil.which(cmd[0], path=env.get("PATH"))
            if exe:
                cmd[0] = exe
            self.log("$ " + " ".join(cmd))
        self.debug(f"run_cmd cwd={cwd!s} shell={shell} interactive={interactive} command={cmd!r}")
        kwargs = {}
        if IS_WIN:
            kwargs["creationflags"] = NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd), env=env, shell=shell,
                stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs,
            )
        except (OSError, ValueError) as exc:
            self.debug(f"Échec création processus: {type(exc).__name__}: {exc}")
            self.log(f"❌ Impossible de démarrer la commande : {exc}")
            self.log("💡 Vérifie que l'interpréteur est installé et accessible dans PATH.")
            raise RuntimeError(f"Impossible de démarrer la commande : {exc}") from exc
        self.process = proc
        # Lecture par blocs : une question sans retour à la ligne (input("Nom ? ")) s'affiche tout de suite.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf, shown = "", 0
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            buf += decoder.decode(chunk).replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                self.handle_line(line, shown)
                shown = 0
            if len(buf) > shown:
                self.log(ANSI_RE.sub("", buf[shown:]), end="")
                shown = len(buf)
        buf += decoder.decode(b"", final=True)
        if buf:
            self.handle_line(buf, shown)
        return proc.wait()

    def run_with_retry(self, cmd, project, env, pip_install=None, shell=False):
        """Lance le programme ; si un module Python manque, l'installe et relance.
        `cmd` peut être une commande ou une fonction qui en fabrique une neuve à chaque essai."""
        tried = set()
        code = 0
        for _ in range(20):
            command = cmd() if callable(cmd) else cmd
            code = self.run_cmd(command, project, env, shell=shell, interactive=True)
            if code == 0 or self.stopped or not pip_install:
                return code
            m = re.search(r"No module named '([\w.\-]+)'", "\n".join(self.tail))
            if not m:
                return code
            mod = m.group(1).split(".")[0]
            local = [project / mod, project / f"{mod}.py", project / "src" / mod]
            if mod in tried or any(p.exists() for p in local):
                return code
            tried.add(mod)
            pkg = PIP_NAMES.get(mod, mod)
            self.log(f"📦 Module manquant « {mod} » : installation de {pkg}...")
            if pip_install(pkg) != 0:
                return code
        return code

    def stop(self, wait=False):
        self.stopped = True
        p = self.process
        if p and p.poll() is None:
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                   creationflags=NO_WINDOW, capture_output=True)
                else:
                    pgid = os.getpgid(p.pid)
                    os.killpg(pgid, signal.SIGTERM)

                    def hard_kill():  # si le programme ignore SIGTERM
                        try:
                            if p.poll() is None:
                                os.killpg(pgid, signal.SIGKILL)
                        except Exception:  # noqa: BLE001
                            pass
                    t = threading.Timer(5, hard_kill)
                    t.daemon = True
                    t.start()
            except Exception:  # noqa: BLE001
                p.terminate()
        c = self.container
        if c:  # on détruit aussi le conteneur Docker
            def rm():
                try:
                    subprocess.run(["docker", "rm", "-f", c], capture_output=True, timeout=30,
                                   creationflags=NO_WINDOW)
                except Exception:  # noqa: BLE001
                    pass
            if wait:
                rm()
            else:
                threading.Thread(target=rm, daemon=True).start()

    def send_input(self, text):
        p = self.process
        if p and p.poll() is None and p.stdin:
            try:
                p.stdin.write((text + "\n").encode("utf-8"))
                p.stdin.flush()
            except Exception:  # noqa: BLE001
                pass

    # ---------- Détection ----------
    @staticmethod
    def detect_kind(p):
        has_req = any((p / f).exists() for f in ("requirements.txt", "pyproject.toml", "setup.py"))
        has_py = has_req or list(p.glob("*.py")) or list(p.glob("src/*.py")) or \
            any(d.is_dir() and (d / "__main__.py").exists() for d in p.iterdir())
        if (p / "package.json").exists() and not has_req:
            return "node"
        if has_py:
            return "python"
        if (p / "package.json").exists():
            return "node"
        if (p / "go.mod").exists():
            return "go"
        if (p / "Cargo.toml").exists():
            return "rust"
        if (p / "pom.xml").exists() or (p / "build.gradle").exists() or (p / "build.gradle.kts").exists():
            return "java-project"
        if (any(p.glob("*.csproj")) or any(p.glob("*.sln"))):
            return "dotnet"
        if (p / "CMakeLists.txt").exists():
            return "cmake"
        if (p / "Makefile").exists() or (p / "makefile").exists():
            return "make"
        if (p / "composer.json").exists():
            return "php-project"
        if (p / "Gemfile").exists():
            return "ruby-project"
        if list(p.glob("*.c")):
            return "c-source"
        if any(list(p.glob(pattern)) for pattern in ("*.cpp", "*.cc", "*.cxx", "*.c++")):
            return "cpp-source"
        if list(p.glob("*.java")):
            return "java-source"
        if (p / "index.html").exists() or list(p.glob("*.html")):
            return "html"
        for ext in SCRIPT_RUNNERS:
            if list(p.glob("*" + ext)):
                return "script"
        return None

    # ---------- Python ----------
    def prepare_python(self, project, env, cfg):
        venv = project / ".venv"
        py = venv / ("Scripts/python.exe" if IS_WIN else "bin/python")
        if not py.exists():
            self.log("🐍 Création de l'environnement virtuel...")
            code = self.run_cmd([python_exe(), "-m", "venv", str(venv)], project, env)
            if code != 0 or not py.exists():
                raise RuntimeError(
                    "Impossible de créer l'environnement virtuel. "
                    "Sous Linux/Debian : sudo apt install python3-venv"
                )
        env["PATH"] = str(py.parent) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(venv)

        files = [project / f for f in ("requirements.txt", "pyproject.toml", "setup.py")]
        sig = self.file_hash(files)
        if cfg.get("py_hash") != sig:
            if (project / "requirements.txt").exists():
                self.log("📦 Installation des dépendances...")
                cmd = [py, "-m", "pip", "install", "-r", "requirements.txt"]
            elif (project / "pyproject.toml").exists() or (project / "setup.py").exists():
                self.log("📦 Installation du projet...")
                cmd = [py, "-m", "pip", "install", "-e", "."]
            else:
                cmd = None
            if cmd:
                if self.run_cmd(cmd, project, env) != 0:
                    raise RuntimeError("Échec de l'installation des dépendances (voir le journal).")
            cfg["py_hash"] = sig
            self.save_config(project, cfg)
        return py

    @staticmethod
    def find_python_entry(project):
        for e in PY_ENTRYPOINTS:
            if (project / e).exists():
                return [e]

        def skip(n):
            return n in ("setup.py", "conftest.py") or n.startswith("test_") or n.endswith("_test.py")

        for folder in (project, project / "src"):
            if not folder.exists():
                continue
            for f in sorted(folder.glob("*.py")):
                if not skip(f.name) and "__main__" in f.read_text(encoding="utf-8", errors="ignore"):
                    return [str(f.relative_to(project))]
        pys = [f for f in sorted(project.glob("*.py")) if not skip(f.name)]
        if len(pys) == 1:
            return [pys[0].name]
        for d in sorted(project.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and (d / "__main__.py").exists():
                return ["-m", d.name]
        return None

    def python_command(self, project, py, public=False):
        """public=True : le serveur web écoute sur toutes les interfaces (nécessaire dans Docker)."""
        entry = self.find_python_entry(project)
        if entry is None:
            raise RuntimeError(
                "Je n'ai pas trouvé le fichier à lancer. Écris la commande dans le champ "
                "« Commande de lancement » (ex : python mon_fichier.py)."
            )
        text = ""
        req = project / "requirements.txt"
        if req.exists():
            text += req.read_text(encoding="utf-8", errors="ignore").lower()
        if entry[0] != "-m":
            src = (project / entry[0]).read_text(encoding="utf-8", errors="ignore")
            text += src.lower()
            if "streamlit" in text:
                self.log("🎈 Application Streamlit détectée.")
                return [py, "-m", "streamlit", "run", entry[0], "--server.headless", "true"]
            m = re.search(r"^(\w+)\s*=\s*(Flask|FastAPI)\(", src, re.M)
            if m and "__main__" not in src:  # application web qui attend d'être servie
                var, fw = m.groups()
                module = Path(entry[0]).with_suffix("").as_posix().replace("/", ".")
                host = "0.0.0.0" if public else "127.0.0.1"
                if fw == "FastAPI":
                    self.log("⚡ Application FastAPI détectée (lancée avec uvicorn).")
                    return [py, "-m", "uvicorn", f"{module}:{var}", "--host", host, "--port", "8000"]
                self.log("🌶️  Application Flask détectée.")
                return [py, "-m", "flask", "--app", f"{module}:{var}", "run", "--host", host, "--port", "5000"]
        self.log(f"▶️  Fichier lancé : {' '.join(entry)}")
        return [py] + entry

    # ---------- Node ----------
    @staticmethod
    def read_package_json(project):
        return json.loads((project / "package.json").read_text(encoding="utf-8-sig"))

    def prepare_node(self, project, env, cfg):
        pm = "npm"
        if (project / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
            pm = "pnpm"
        elif (project / "yarn.lock").exists() and shutil.which("yarn"):
            pm = "yarn"
        exe = shutil.which(pm)
        if not exe:
            raise RuntimeError("Node.js est introuvable. Installe-le depuis https://nodejs.org puis relance.")
        files = [project / f for f in ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml")]
        sig = self.file_hash(files)
        if not (project / "node_modules").exists() or cfg.get("node_hash") != sig:
            self.log(f"📦 Installation des dépendances ({pm})...")
            if self.run_cmd([exe, "install"], project, env) != 0:
                raise RuntimeError("Échec de l'installation des dépendances (voir le journal).")
            cfg["node_hash"] = sig
            self.save_config(project, cfg)
        return exe

    def node_command(self, project, exe):
        pkg = self.read_package_json(project)
        scripts = pkg.get("scripts", {})
        for name in ("start", "dev"):
            if name in scripts:
                self.log(f"▶️  Script lancé : {name}")
                return [exe, "run", name]
        node = shutil.which("node")
        entry = pkg.get("main") or next((e for e in NODE_ENTRYPOINTS if (project / e).exists()), None)
        if not (node and entry):
            raise RuntimeError("Aucun script de démarrage trouvé. Renseigne la commande de lancement.")
        return [node, entry]

    # ---------- Fichiers isolés (.js, .sh, .jar...) ----------
    def script_command(self, project):
        for ext, runner in SCRIPT_RUNNERS.items():
            if ext in (".bat", ".cmd") and not IS_WIN:
                continue
            files = sorted(project.glob("*" + ext), key=lambda f: (f.name not in NODE_ENTRYPOINTS, f.name))
            if not files:
                continue
            executable = runner[0]
            if executable == "pwsh" and not shutil.which(executable):
                if shutil.which("powershell"):
                    runner = ["powershell", "-ExecutionPolicy", "Bypass", "-File"]
                    executable = runner[0]
            if not shutil.which(executable):
                hint = {
                    "node": "Node.js", "bash": "Bash", "java": "Java/JDK", "ruby": "Ruby",
                    "php": "PHP", "perl": "Perl", "lua": "Lua", "pwsh": "PowerShell 7",
                    "Rscript": "R", "swift": "Swift", "elixir": "Elixir", "dart": "Dart",
                    "kotlin": "Kotlin", "scala": "Scala", "clojure": "Clojure", "groovy": "Groovy",
                    "julia": "Julia", "octave": "GNU Octave", "sqlite3": "SQLite", "tclsh": "Tcl",
                    "awk": "awk", "fish": "fish", "zsh": "zsh", "raku": "Raku", "npx": "Node.js/npm",
                }.get(executable, executable)
                raise RuntimeError(f"Runtime « {hint} » (commande {executable}) introuvable sur ce PC. "
                                   f"Installe-le et ajoute-le au PATH, ou saisis ta propre commande.")
            self.log(f"▶️  Fichier lancé : {files[0].name}")
            return runner + [files[0].name]
        raise RuntimeError(
            "Aucun fichier exécutable trouvé. Vérifie l'extension et que son interpréteur est installé, "
            "ou indique une commande dans « Commande de lancement » (ex. : python main.py, node app.js, "
            "java Main.java, dotnet run, gcc main.c -o app)."
        )

    # ---------- Projets multi-langages / toolchains ----------
    def generic_project_command(self, project, kind, env):
        """Construit une commande de démarrage pour les toolchains courantes.

        Les outils ne sont jamais téléchargés ou installés silencieusement : le journal
        indique le prérequis manquant, puis l'utilisateur peut choisir une commande libre.
        """
        def require(executable, install_hint):
            path = shutil.which(executable, path=env.get("PATH"))
            if not path:
                raise RuntimeError(f"Outil « {executable} » introuvable. {install_hint} "
                                   "Tu peux aussi préciser une commande personnalisée.")
            return path

        if kind == "java-project":
            if (project / "mvnw").exists() or (project / "mvnw.cmd").exists():
                wrapper = project / ("mvnw.cmd" if IS_WIN else "mvnw")
                if not IS_WIN:
                    try: wrapper.chmod(wrapper.stat().st_mode | 0o111)
                    except OSError: pass
                return [str(wrapper), "exec:java"] if (project / "pom.xml").exists() else [str(wrapper), "run"]
            if (project / "gradlew").exists() or (project / "gradlew.bat").exists():
                wrapper = project / ("gradlew.bat" if IS_WIN else "gradlew")
                if not IS_WIN:
                    try: wrapper.chmod(wrapper.stat().st_mode | 0o111)
                    except OSError: pass
                return [str(wrapper), "run"]
            if (project / "pom.xml").exists():
                require("mvn", "Installe Maven et un JDK.")
                return ["mvn", "exec:java"]
            require("gradle", "Installe Gradle et un JDK.")
            return ["gradle", "run"]
        if kind == "dotnet":
            require("dotnet", "Installe le SDK .NET depuis https://dotnet.microsoft.com/download.")
            return ["dotnet", "run"]
        if kind == "cmake":
            require("cmake", "Installe CMake et un compilateur C/C++.")
            build = project / ".ziplauncher-build"
            build.mkdir(exist_ok=True)
            return ["cmake", "--build", str(build)] if (build / ("build.ninja" if shutil.which("ninja") else "Makefile")).exists() else ["cmake", "-S", ".", "-B", str(build)]
        if kind == "make":
            require("make", "Installe make (ou build-essential sous Debian/Ubuntu).")
            return ["make"]
        if kind == "php-project":
            if not shutil.which("php", path=env.get("PATH")):
                raise RuntimeError("PHP est introuvable. Installe PHP, puis relance.")
            if (project / "vendor" / "autoload.php").exists():
                return ["php", "-S", "127.0.0.1:8000", "-t", "."]
            if (project / "composer.json").exists() and shutil.which("composer", path=env.get("PATH")):
                self.log("ℹ️ Composer est disponible : lance `composer install` si les dépendances manquent.")
            return ["php", "-S", "127.0.0.1:8000", "-t", "."]
        if kind == "ruby-project":
            require("ruby", "Installe Ruby depuis https://www.ruby-lang.org/.")
            if (project / "Gemfile").exists() and shutil.which("bundle", path=env.get("PATH")):
                self.log("ℹ️ Projet Ruby détecté. Si besoin, exécute `bundle install` dans le dossier du projet.")
            for name in ("main.rb", "app.rb", "server.rb", "bin/rails"):
                if (project / name).exists():
                    return ["ruby", name] if name.endswith(".rb") else ["bundle", "exec", "rails", "server"]
            raise RuntimeError("Projet Ruby détecté, mais aucun point d'entrée connu. Renseigne la commande (ex. bundle exec rails server).")
        if kind == "c-source":
            compiler = shutil.which("gcc", path=env.get("PATH")) or shutil.which("clang", path=env.get("PATH"))
            if not compiler:
                raise RuntimeError("Compilateur C absent : installe GCC ou Clang, ou indique une commande personnalisée.")
            src = next((project / n for n in ("main.c", "hello.c") if (project / n).is_file()), None)
            src = src or next(iter(sorted(project.glob("*.c"))), None)
            if not src: raise RuntimeError("Aucun fichier .c trouvé.")
            out = project / ("launcher_app.exe" if IS_WIN else "launcher_app")
            if self.run_cmd([compiler, str(src), "-o", str(out)], project, env) != 0:
                raise RuntimeError("Compilation C échouée. Consulte le journal pour les erreurs du compilateur.")
            return [str(out)]
        if kind == "cpp-source":
            compiler = shutil.which("g++", path=env.get("PATH")) or shutil.which("clang++", path=env.get("PATH"))
            if not compiler:
                raise RuntimeError("Compilateur C++ absent : installe g++ ou Clang++, ou indique une commande personnalisée.")
            src = next((project / n for n in ("main.cpp", "main.cc", "main.cxx") if (project / n).is_file()), None)
            src = src or next((f for ext in ("*.cpp", "*.cc", "*.cxx", "*.c++") for f in sorted(project.glob(ext))), None)
            if not src: raise RuntimeError("Aucun fichier C++ trouvé.")
            out = project / ("launcher_app.exe" if IS_WIN else "launcher_app")
            if self.run_cmd([compiler, str(src), "-o", str(out)], project, env) != 0:
                raise RuntimeError("Compilation C++ échouée. Consulte le journal pour les erreurs du compilateur.")
            return [str(out)]
        if kind == "java-source":
            require("java", "Installe un JDK récent (Java 11 ou plus récent pour lancer directement un fichier .java).")
            src = next((project / n for n in ("Main.java", "main.java", "App.java") if (project / n).is_file()), None)
            src = src or next(iter(sorted(project.glob("*.java"))), None)
            if not src: raise RuntimeError("Aucun fichier .java trouvé.")
            return ["java", str(src)]
        return None

    @staticmethod
    def tool_help_text():
        return """ZIP Launcher — outils et commandes

Le champ « Commande de lancement » accepte une commande disponible sur ton système.
Exemples (les outils doivent être installés) :
Python : python main.py  |  python -m pytest
Node : npm run dev  |  node index.js  |  npx tsx main.ts
Java : java Main.java  |  mvn spring-boot:run  |  gradle run
.NET : dotnet run  |  dotnet build
C : gcc main.c -o app puis app (Windows : app.exe)
C++ : g++ main.cpp -o app puis app (Windows : app.exe)
Go : go run .  |  Rust : cargo run
PHP : php -S 127.0.0.1:8000  |  Ruby : ruby main.rb
PowerShell : pwsh -File script.ps1  |  Bash : bash script.sh
Perl : perl script.pl  |  Lua : lua script.lua  |  R : Rscript script.R
Swift : swift main.swift  |  Dart : dart run main.dart
Kotlin : kotlin main.kts  |  Scala : scala Main.scala

Important : aucun lanceur ne peut fournir tous les runtimes par magie. Installe les outils nécessaires.
Les langages supplémentaires s'exécutent en mode direct : décoche l'isolation Docker pour ces projets.
Pour les commandes en plusieurs étapes, utilise un script .bat/.cmd sous Windows ou .sh sous Linux/macOS.
N'exécute que des scripts dont tu connais la provenance : ils peuvent modifier les fichiers ou utiliser le réseau."""

    # ---------- Machine virtuelle (Docker) ----------
    def check_docker(self):
        exe = shutil.which("docker")
        if not exe:
            raise RuntimeError(
                "Docker est introuvable. Installe Docker Desktop (https://docs.docker.com/get-docker/) "
                "pour utiliser la machine virtuelle, ou décoche l'option d'isolation "
                "(le programme tournera alors directement sur ton PC)."
            )
        detail = ""
        try:
            r = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                               text=True, timeout=45, creationflags=NO_WINDOW)
            ok = r.returncode == 0
            if not ok and r.stderr.strip():
                detail = " (" + r.stderr.strip().splitlines()[-1][:160] + ")"
        except subprocess.TimeoutExpired:
            ok = False
        if not ok:
            raise RuntimeError("Docker est installé mais ne répond pas. Lance Docker Desktop, "
                               "attends qu'il soit démarré, puis réessaie." + detail)
        return exe

    @staticmethod
    def pick_ports(text):
        """Ports demandés qui sont libres sur ton PC (pour éviter un conflit au lancement)."""
        wanted = []
        for part in re.split(r"[,\s;]+", text or ""):
            if part.isdigit() and 1 <= int(part) <= 65535 and int(part) not in wanted:
                wanted.append(int(part))
        free = []
        for p in wanted:
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", p))
                    free.append(p)
                except OSError:
                    pass
        return free

    def docker_cmd(self, exe, project, image, script, user_env, ports=(), network=True,
                   interactive=False, extra_env=None):
        """Commande `docker run` durcie : seul le dossier du projet est visible,
        et seules les variables saisies par l'utilisateur sont transmises (jamais celles de ton PC)."""
        self.container = "ziplauncher-" + uuid.uuid4().hex[:8]
        cmd = [exe, "run", "--rm", "--init", "--name", self.container]
        if interactive:
            cmd.append("-i")
        cpus = str(min(2, os.cpu_count() or 1))
        cmd += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "512", "--memory", "2g", "--cpus", cpus,
                "--read-only", "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=512m"]
        if not IS_WIN:
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        src = str(project)
        src_field = f'"source={src}"' if "," in src else f"source={src}"  # --mount est au format CSV
        cmd += ["--mount", f"type=bind,{src_field},target=/app", "-w", "/app"]
        if not network:
            cmd += ["--network", "none"]
        else:
            for p in ports:
                cmd += ["-p", f"127.0.0.1:{p}:{p}"]
        envs = {"HOME": "/tmp", "PYTHONUNBUFFERED": "1", "HOST": "0.0.0.0"}
        envs.update(extra_env or {})
        envs.update(user_env)
        for k, v in envs.items():
            cmd += ["-e", f"{k}={v}"]
        return cmd + [image, "sh", "-c", script]

    def sandbox_install(self, exe, project, image, script, user_env, cfg, key, sig, extra_env=None):
        self.log("📦 Préparation dans la machine virtuelle "
                 "(au 1er lancement, l'image est téléchargée : patience)...")
        cmd = self.docker_cmd(exe, project, image, script, user_env, network=True, extra_env=extra_env)
        if self.run_cmd(cmd, project, os.environ.copy()) != 0:
            tail = "\n".join(self.tail).lower()
            if any(w in tail for w in ("gcc", "failed building wheel", "error: command")):
                self.log("💡 Certains paquets ont besoin d'outils de compilation absents de l'image légère. "
                         "Décoche l'isolation ou choisis des versions avec binaires précompilés.")
            raise RuntimeError("Échec de la préparation dans la machine virtuelle (voir le journal).")
        cfg[key] = sig
        self.save_config(project, cfg)

    def node_args(self, project):
        pkg = self.read_package_json(project)
        scripts = pkg.get("scripts", {})
        for name in ("start", "dev"):
            if name in scripts:
                self.log(f"▶️  Script lancé : {name}")
                return ["npm", "run", name]
        entry = pkg.get("main") or next((e for e in NODE_ENTRYPOINTS if (project / e).exists()), None)
        if not entry:
            raise RuntimeError("Aucun script de démarrage trouvé. Renseigne la commande de lancement.")
        return ["node", entry]

    def sandbox_script(self, project):
        for ext, (image, runner) in SANDBOX_SCRIPTS.items():
            files = sorted(project.glob("*" + ext), key=lambda f: (f.name not in NODE_ENTRYPOINTS, f.name))
            if files:
                self.log(f"▶️  Fichier lancé : {files[0].name}")
                return image, runner + [files[0].name]
        raise RuntimeError("Ce type de fichier (.bat, .cmd) ne peut pas tourner dans la machine virtuelle Linux.")

    def run_sandboxed(self, project, env_text, custom, offline, ports_text, cfg):
        exe = self.check_docker()
        user_env = self.parse_env_text(env_text)
        host_env = os.environ.copy()  # uniquement pour le client docker, jamais transmis au conteneur
        kind = self.detect_kind(project)
        self.log("🛡 Machine virtuelle : le programme tourne dans un conteneur Docker isolé "
                 "(il ne voit que le dossier du projet, pas le reste de ton PC).")
        self.log(f"🔎 Type de projet : {kind or 'inconnu'}")
        network = not offline
        ports = self.pick_ports(ports_text) if network else []
        if offline:
            self.log("🔌 Internet coupé pendant l'exécution (les ports ne sont donc pas ouverts).")
        elif ports:
            self.log("🔌 Ports ouverts vers ton PC : " + ", ".join(map(str, ports)))

        image, run_script, pip_install, extra_env = DOCKER_IMAGES["python"], None, None, {}
        activate = ". .venv-docker/bin/activate; "

        if kind == "python":
            files = [project / f for f in ("requirements.txt", "pyproject.toml", "setup.py")]
            sig = self.file_hash(files)
            if not ((project / ".venv-docker").exists() and cfg.get("docker_py_hash") == sig):
                script = "set -e; [ -d .venv-docker ] || python -m venv .venv-docker; " + activate
                if (project / "requirements.txt").exists():
                    script += "pip install --no-input -r requirements.txt"
                elif (project / "pyproject.toml").exists() or (project / "setup.py").exists():
                    script += "pip install --no-input -e ."
                else:
                    script += "true"
                self.sandbox_install(exe, project, image, script, user_env, cfg, "docker_py_hash", sig)
            if custom:
                run_script = activate + custom
            else:
                args = self.python_command(project, "python", public=True)
                if "streamlit" in args:
                    args += ["--server.address", "0.0.0.0"]
                run_script = activate + "exec " + shlex.join(args)
            pip_install = lambda pkg: self.run_cmd(  # noqa: E731
                self.docker_cmd(exe, project, image, activate + "pip install --no-input " + shlex.quote(pkg),
                                user_env, network=True), project, host_env)
        elif kind == "node":
            image = DOCKER_IMAGES["node"]
            files = [project / f for f in ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml")]
            sig = self.file_hash(files)
            if not ((project / "node_modules").exists() and cfg.get("docker_node_hash") == sig):
                self.sandbox_install(exe, project, image, "npm install --no-audit --no-fund",
                                     user_env, cfg, "docker_node_hash", sig)
            run_script = custom or "exec " + shlex.join(self.node_args(project))
        elif kind == "go":
            image, extra_env = DOCKER_IMAGES["go"], GO_ENV
            sig = self.file_hash([project / "go.mod", project / "go.sum"])
            if (project / "go.mod").exists() and cfg.get("docker_go_hash") != sig:
                self.sandbox_install(exe, project, image, "go mod download", user_env, cfg,
                                     "docker_go_hash", sig, extra_env=extra_env)
            run_script = custom or "exec go run ."
        elif kind == "html":
            free = self.pick_ports("8000,8001,8002,8003,8004,8005,8080")
            if not free:
                raise RuntimeError("Aucun port libre pour afficher la page web.")
            network, ports = True, free[:1]
            run_script = custom or f"exec python -m http.server {ports[0]} --bind 0.0.0.0"
            self.log("🌐 Page web servie depuis la machine virtuelle.")
        elif kind == "script":
            image, args = self.sandbox_script(project)
            if image.startswith("golang"):
                extra_env = GO_ENV
            run_script = custom or "exec " + shlex.join(args)
        elif kind == "rust":
            raise RuntimeError("Rust n'est pas pris en charge dans la machine virtuelle. "
                               "Décoche l'isolation pour le lancer directement sur ton PC.")
        elif custom:
            run_script = custom
        else:
            raise RuntimeError("Type de projet non reconnu. Renseigne la commande de lancement.")

        self.log("🚀 Lancement dans la machine virtuelle...")
        self.published = set(ports)
        try:
            code = self.run_with_retry(
                lambda: self.docker_cmd(exe, project, image, run_script, user_env, ports=ports,
                                        network=network, interactive=True, extra_env=extra_env),
                project, host_env, pip_install=pip_install)
        finally:
            self.container = None
            self.published = None
        self.finish(code)

    # ---------- Orchestration ----------
    def finish(self, code):
        if self.stopped:
            self.log("⏹ Arrêté.")
        elif code == 0:
            self.log("✅ Programme terminé.")
        else:
            self.log(f"⚠️ Le programme s'est arrêté avec une erreur (code {code}).")
            tail = "\n".join(self.tail).lower()
            if any(w in tail for w in ("token", "api_key", "apikey", "secret", "keyerror")):
                self.log("💡 Il manque peut-être une variable d'environnement (token, clé API...). "
                         "Ajoute-la dans la case prévue et relance.")

    def run_project(self, project, env_text, custom, sandbox=False, offline=False, ports_text=""):
        self.stopped = False
        self.seen_urls = set()
        cfg = self.load_config(project)
        cfg["env"], cfg["cmd"] = env_text, custom
        self.save_config(project, cfg)
        self.sync_env_file(project, env_text)
        if sandbox:
            sandbox_kind = self.detect_kind(project)
            sandbox_supported = {"python", "node", "go", "html", "script", "rust"}
            if sandbox_kind not in sandbox_supported:
                raise RuntimeError(
                    f"Le type « {sandbox_kind or 'inconnu'} » n'a pas encore d'image Docker dédiée. "
                    "Pour le lancer avec son outil local, décoche « machine virtuelle isolée ». "
                    "Ne désactive l'isolation que pour un script dont tu connais la provenance."
                )
            self.run_sandboxed(project, env_text, custom, offline, ports_text, cfg)
            return

        env = self.build_env(env_text)
        kind = self.detect_kind(project)
        self.log("⚠️ Mode direct : le programme tourne sur ton PC, sans isolation.")
        self.log(f"🔎 Type de projet : {kind or 'inconnu'}")
        self.log("🪲 Diagnostics détaillés : " + str(APPS_DIR / "debug.log"))
        if kind is None and custom:
            self.log("ℹ️ Commande personnalisée : vérifie que le runtime correspondant est installé.")
        py, cmd, shell = None, None, False

        if kind == "python":
            py = self.prepare_python(project, env, cfg)
            cmd = self.python_command(project, py) if not custom else None
        elif kind == "node":
            exe = self.prepare_node(project, env, cfg)
            cmd = self.node_command(project, exe) if not custom else None
        elif kind == "go" and not custom:
            if not shutil.which("go"):
                raise RuntimeError("Go est introuvable. Installe-le depuis https://go.dev/dl")
            cmd = ["go", "run", "."]
        elif kind == "rust" and not custom:
            if not shutil.which("cargo"):
                raise RuntimeError("Rust est introuvable. Installe-le depuis https://rustup.rs")
            cmd = ["cargo", "run"]
        elif kind == "html" and not custom:
            page = project / "index.html"
            if not page.exists():
                page = sorted(project.glob("*.html"))[0]
            self.log(f"🌐 Page web : ouverture de {page.name} dans le navigateur.")
            webbrowser.open(page.as_uri())
            return
        elif kind == "script" and not custom:
            cmd = self.script_command(project)
        elif kind in ("java-project", "dotnet", "cmake", "make", "php-project", "ruby-project",
                      "c-source", "cpp-source", "java-source") and not custom:
            cmd = self.generic_project_command(project, kind, env)
        elif not custom:
            raise RuntimeError("Type de projet non reconnu. Clique sur « Aide outils » pour les exemples, "
                               "ou renseigne la commande de lancement.")

        if custom:
            cmd, shell = custom, True
        self.log("🚀 Lancement...")
        pip_install = (lambda pkg: self.run_cmd([py, "-m", "pip", "install", pkg], project, env)) if py else None  # noqa: E731
        code = self.run_with_retry(cmd, project, env, pip_install=pip_install, shell=shell)
        self.finish(code)


# =====================================================================
#  Interface graphique
# =====================================================================
class GUI:
    MAX_LOG_LINES = 4000

    def __init__(self, root, dnd_files=None):
        self.root = root
        self.q = queue.Queue()
        self.core = Launcher(log=lambda t, end="\n": self.q.put(("log", t, end)),
                             on_url=lambda u: self.q.put(("url", u)))
        self.selected = None
        self.running = False
        self.url = None
        self.opened_auto = False

        root.title("ZIP Launcher")
        height = min(790, max(560, root.winfo_screenheight() - 90))
        root.geometry(f"980x{height}")
        root.minsize(820, 520)
        root.report_callback_exception = lambda *a: self.append_log(f"❌ Erreur interne : {a[1]}")

        # --- colonne gauche : projets ---
        left = tk.Frame(root, width=210)
        left.pack(side="left", fill="y", padx=(10, 0), pady=10)
        left.pack_propagate(False)
        tk.Label(left, text="Mes projets", font=("", 11, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, exportselection=False)
        self.listbox.pack(fill="both", expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        tk.Button(left, text="🗑 Supprimer", command=self.delete_project).pack(fill="x")

        # --- colonne droite ---
        right = tk.Frame(root)
        right.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        tk.Label(right, anchor="w", text="Lien GitHub, archive, fichier(s) ou dossier :").pack(fill="x")
        row = tk.Frame(right)
        row.pack(fill="x")
        self.source_var = tk.StringVar()
        tk.Entry(row, textvariable=self.source_var).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Archive…", command=self.choose_archive).pack(side="left", padx=(6, 0))
        tk.Button(row, text="Fichier(s)…", command=self.choose_files).pack(side="left", padx=(4, 0))
        tk.Button(row, text="Dossier…", command=self.choose_folder).pack(side="left", padx=(4, 0))

        self.drop = tk.Label(
            right, relief="groove", height=3, bg="#eef2f7", cursor="hand2",
            text="📦  Glisse ici un ZIP, des fichiers ou un dossier" if dnd_files
            else "📦  Clique ici pour choisir une archive (ou utilise les boutons)",
        )
        self.drop.pack(fill="x", pady=8)
        self.drop.bind("<Button-1>", lambda e: self.choose_archive())
        if dnd_files:
            self.drop.drop_target_register(dnd_files)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)

        tk.Label(right, anchor="w",
                 text="Variables d'environnement (une par ligne, ex : DISCORD_TOKEN=abc123) :").pack(fill="x")
        self.env_box = tk.Text(right, height=4)
        self.env_box.pack(fill="x")

        tk.Label(right, anchor="w",
                 text="Commande de lancement (pour les langages non détectés, ex. dotnet run ou gcc main.c -o app) :").pack(
            fill="x", pady=(8, 0))
        self.cmd_entry = tk.Entry(right)
        self.cmd_entry.pack(fill="x")

        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="Ouvrir automatiquement dans le navigateur si une page web démarre",
                       variable=self.auto_var).pack(anchor="w", pady=(6, 0))
        self.vm_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="🛡 Lancer dans une machine virtuelle isolée (Docker) — recommandé",
                       variable=self.vm_var).pack(anchor="w")
        self.offline_var = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="Couper Internet pendant l'exécution (les installations restent autorisées)",
                       variable=self.offline_var).pack(anchor="w")
        prow = tk.Frame(right)
        prow.pack(fill="x")
        tk.Label(prow, text="Ports à ouvrir vers ton PC (sites web) :").pack(side="left")
        self.ports_entry = tk.Entry(prow)
        self.ports_entry.insert(0, DEFAULT_PORTS)
        self.ports_entry.pack(side="left", fill="x", expand=True, padx=6)

        btns = tk.Frame(right)
        btns.pack(pady=8)
        self.start_btn = tk.Button(btns, text="▶ Lancer", width=16, command=self.start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = tk.Button(btns, text="■ Arrêter", width=12, command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.web_btn = tk.Button(btns, text="🌐 Ouvrir la page", width=16, command=self.open_web, state="disabled")
        self.web_btn.pack(side="left", padx=4)
        tk.Button(btns, text="📂 Dossier", width=12, command=self.open_folder).pack(side="left", padx=4)
        tk.Button(btns, text="🪲 Journal debug", width=14, command=self.open_debug_log).pack(side="left", padx=4)
        tk.Button(btns, text="🧰 Aide outils", width=12, command=self.show_tool_help).pack(side="left", padx=4)

        self.log = scrolledtext.ScrolledText(right, state="disabled", height=12, bg="#111", fg="#ddd",
                                             insertbackground="#ddd")
        self.log.pack(fill="both", expand=True)

        inp = tk.Frame(right)
        inp.pack(fill="x", pady=(6, 0))
        tk.Label(inp, text="Envoyer au programme :").pack(side="left")
        self.input_entry = tk.Entry(inp)
        self.input_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.input_entry.bind("<Return>", lambda e: self.send_input())
        tk.Button(inp, text="Envoyer", command=self.send_input).pack(side="left")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_list()
        self.poll()

    # ---------- Utilitaires UI ----------
    def append_log(self, text, end="\n"):
        self.log.config(state="normal")
        self.log.insert("end", text + end)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > self.MAX_LOG_LINES:  # le journal ne grossit pas indéfiniment
            self.log.delete("1.0", f"{lines - self.MAX_LOG_LINES + 500}.0")
        self.log.see("end")
        self.log.config(state="disabled")

    def handle_event(self, item):
        kind, *data = item
        if kind == "log":
            self.append_log(data[0], data[1] if len(data) > 1 else "\n")
        elif kind == "url":
            self.url = data[0]
            self.web_btn.config(state="normal")
            if self.auto_var.get() and not self.opened_auto:
                self.opened_auto = True
                self.root.after(1500, self.open_web)
        elif kind == "env":
            self.env_box.delete("1.0", "end")
            self.env_box.insert("1.0", data[0])
        elif kind == "imported":
            self.set_selected(data[0])
        elif kind == "done":
            self.running = False
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

    def poll(self):
        try:
            for _ in range(300):  # au plus 300 messages par tour pour que la fenêtre reste fluide
                try:
                    item = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.handle_event(item)
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(f"Erreur interface : {e}\n")
        finally:
            self.root.after(100, self.poll)  # la boucle ne s'arrête jamais

    def refresh_list(self, select=None):
        self.listbox.delete(0, "end")
        names = []
        if APPS_DIR.exists():
            names = sorted(d.name for d in APPS_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
        for n in names:
            self.listbox.insert("end", n)
        if select and select in names:
            idx = names.index(select)
            self.listbox.selection_set(idx)
            self.listbox.see(idx)

    def set_selected(self, project):
        """Sélectionne un projet (dossier) dans la liste et charge sa config."""
        self.selected = project
        self.source_var.set("")
        try:
            top = project.resolve().relative_to(APPS_DIR.resolve()).parts[0]
        except (ValueError, IndexError):
            top = None
        self.refresh_list(select=top)
        cfg = Launcher.load_config(project)
        if cfg.get("env") and not self.env_box.get("1.0", "end").strip():
            self.env_box.insert("1.0", cfg["env"])
        if cfg.get("cmd"):
            self.cmd_entry.delete(0, "end")
            self.cmd_entry.insert(0, cfg["cmd"])

    def on_select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        project = Launcher.resolve_root(APPS_DIR / self.listbox.get(sel[0]))
        self.selected = project
        self.source_var.set("")
        cfg = Launcher.load_config(project)
        self.env_box.delete("1.0", "end")
        self.env_box.insert("1.0", cfg.get("env", ""))
        self.cmd_entry.delete(0, "end")
        self.cmd_entry.insert(0, cfg.get("cmd", ""))

    def delete_project(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        if self.running:
            messagebox.showwarning("Programme en cours", "Arrête le programme avant de supprimer un projet.")
            return
        name = self.listbox.get(sel[0])
        if messagebox.askyesno("Supprimer", f"Supprimer le projet « {name} » ?"):
            try:
                rmtree_force(APPS_DIR / name)
            except RuntimeError as e:
                messagebox.showerror("Suppression impossible", str(e))
            self.selected = None
            self.refresh_list()

    # ---------- Sources ----------
    def on_drop(self, event):
        self.source_var.set(" ; ".join(self.root.tk.splitlist(event.data)))

    def choose_archive(self):
        path = filedialog.askopenfilename(
            filetypes=[("Archives", "*.zip *.7z *.rar *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz"),
                       ("Tous les fichiers", "*.*")])
        if path:
            self.source_var.set(path)

    def choose_files(self):
        paths = filedialog.askopenfilenames(title="Choisis un ou plusieurs fichiers")
        if paths:
            self.source_var.set(" ; ".join(paths))

    def choose_folder(self):
        path = filedialog.askdirectory(title="Choisis un dossier de projet")
        if path:
            self.source_var.set(path)

    # ---------- Actions ----------
    def start(self):
        if self.running:
            return
        source = self.source_var.get().strip()
        if not source and not self.selected:
            messagebox.showwarning(
                "Rien à lancer",
                "Colle un lien GitHub, choisis une archive, un dossier ou des fichiers, "
                "ou sélectionne un projet.")
            return
        sandbox = self.vm_var.get()
        if not sandbox and not messagebox.askyesno(
            "⚠️ Sans machine virtuelle",
            "Le programme va s'exécuter directement sur ton ordinateur, sans aucune isolation.\n"
            "Ne lance que des projets en lesquels tu as confiance.\n\nContinuer ?",
        ):
            return
        sources = [x.strip() for x in source.split(" ; ") if x.strip()]
        args = (sources, self.selected, self.env_box.get("1.0", "end").strip(), self.cmd_entry.get().strip(),
                sandbox, self.offline_var.get(), self.ports_entry.get())
        self.running = True
        self.opened_auto = False
        self.url = None
        self.core.stopped = False
        self.web_btn.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(target=self.pipeline, args=args, daemon=True).start()

    def pipeline(self, source, project, env_text, custom, sandbox=True, offline=False, ports=""):
        log = self.core.log
        try:
            if source:
                project = self.core.import_source(source)
                self.q.put(("imported", project))
                template = Launcher.env_template(project)
                if template and not env_text and not (project / ".env").exists():
                    self.q.put(("env", template))
                    log("🔑 Ce projet attend des variables d'environnement (.env.example trouvé).")
                    log("   Remplis-les dans la case prévue, puis clique sur Lancer.")
                    return
            self.core.run_project(project, env_text, custom, sandbox, offline, ports)
        except StopRequested:
            log("⏹ Arrêté.")
        except RuntimeError as e:
            log(f"❌ {e}")
        except Exception as e:  # noqa: BLE001
            log(f"❌ Erreur inattendue ({type(e).__name__}) : {e}")
            log(traceback.format_exc())
        finally:
            self.q.put(("done",))

    def stop(self):
        self.core.stop()

    def send_input(self):
        text = self.input_entry.get()
        self.input_entry.delete(0, "end")
        self.append_log(f"> {text}")
        self.core.send_input(text)

    def open_web(self):
        if self.url:
            webbrowser.open(self.url)

    def show_tool_help(self):
        """Affiche les langages et exemples de commandes acceptés."""
        messagebox.showinfo("Outils et commandes", Launcher.tool_help_text())

    def open_debug_log(self):
        """Ouvre le journal de diagnostic."""
        log_path = APPS_DIR / "debug.log"
        try:
            APPS_DIR.mkdir(parents=True, exist_ok=True)
            log_path.touch(exist_ok=True)
            if IS_WIN:
                os.startfile(log_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_path)])
            else:
                subprocess.Popen(["xdg-open", str(log_path)])
        except Exception as exc:
            messagebox.showerror("Journal debug", f"Impossible d'ouvrir le journal : {exc}\\n\\nFichier : {log_path}")

    def open_folder(self):
        target = self.selected or APPS_DIR
        if not target.exists():
            return
        if IS_WIN:
            os.startfile(target)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

    def on_close(self):
        self.core.stop(wait=True)
        self.root.destroy()


def load_dnd():
    """Active le glisser-déposer (installe tkinterdnd2 automatiquement, une seule fois)."""
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        return TkinterDnD, DND_FILES
    except ImportError:
        pass
    marker = APPS_DIR / ".dnd_unavailable"
    if marker.exists():  # l'installation a déjà échoué : on ne retente pas à chaque démarrage
        return None, None
    try:
        r = subprocess.run([python_exe(), "-m", "pip", "install", "--quiet", "tkinterdnd2"],
                           timeout=90, capture_output=True, creationflags=NO_WINDOW)
        if r.returncode == 0:
            importlib.invalidate_caches()
            from tkinterdnd2 import DND_FILES, TkinterDnD
            return TkinterDnD, DND_FILES
    except Exception:  # noqa: BLE001
        pass
    try:
        marker.write_text("pip install tkinterdnd2 a échoué. Supprime ce fichier pour réessayer.", encoding="utf-8")
    except OSError:
        pass
    return None, None


def main():
    if tk is None:
        print("tkinter n'est pas installé. Sous Linux : sudo apt install python3-tk")
        sys.exit(1)
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    dnd_cls, dnd_files = load_dnd()
    root = dnd_cls.Tk() if dnd_cls else tk.Tk()
    GUI(root, dnd_files)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        # lancé avec pythonw (sans console), une erreur serait invisible : on la note et on l'affiche
        err = traceback.format_exc()
        try:
            APPS_DIR.mkdir(parents=True, exist_ok=True)
            (APPS_DIR / "crash.log").write_text(err, encoding="utf-8")
        except OSError:
            pass
        if tk is not None:
            try:
                messagebox.showerror("ZIP Launcher a planté", err[-1500:])
            except Exception:  # noqa: BLE001
                pass
        sys.stderr.write(err)
        sys.exit(1)

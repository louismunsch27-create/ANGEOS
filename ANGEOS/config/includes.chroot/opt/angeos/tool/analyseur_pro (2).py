#!/usr/bin/env python3
"""
Analyseur Web Pro — interface sombre, multi-outils (un seul fichier).

Installation :  pip install requests beautifulsoup4
Interface    :  python analyseur_pro.py
Ligne de cmd :  python analyseur_pro.py --cli https://exemple.com [--liens] [--crawl 20] [--no-ssl-check]
"""
import csv
import json
import os
import queue
import re
import socket
import ssl
import sys
import threading
import time
import traceback
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urldefrag, urljoin, urlparse

try:
    import requests
    import urllib3
    from bs4 import BeautifulSoup
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    sys.exit("Modules manquants. Installez-les avec :\n  pip install requests beautifulsoup4")

UA = "Mozilla/5.0 (compatible; AnalyseurWebPro/2.0)"
OK, WARN, ERR, INFO = "ok", "warn", "err", "info"
CRIT, HAUTE, MOY, FAIBLE = "critique", "haute", "moyenne", "faible"
GRAVITE_ORDRE = {CRIT: 0, HAUTE: 1, MOY: 2, FAIBLE: 3}
GRAVITE_LABEL = {CRIT: "🔴 Critique", HAUTE: "🟠 Élevée", MOY: "🟡 Moyenne", FAIBLE: "🔵 Faible"}
ETIQ = {OK: "✔ OK", WARN: "⚠ À améliorer", ERR: "✘ Problème", INFO: "ℹ Info"}
SYMB = {OK: "[OK] ", WARN: "[!]  ", ERR: "[X]  ", INFO: "[i]  "}
HIST_FILE = os.path.join(os.path.expanduser("~"), ".analyseur_web_historique.json")
EXT_IGNOREES = re.compile(
    r"\.(pdf|jpe?g|png|gif|webp|svg|ico|zip|rar|7z|gz|mp3|mp4|avi|mov|docx?|xlsx?|pptx?|css|js|json|xml|woff2?|ttf)$", re.I)

SIGNATURES = {
    "WordPress": ["wp-content", "wp-includes"], "Shopify": ["cdn.shopify.com"],
    "Wix": ["wixstatic.com"], "Squarespace": ["static1.squarespace.com"], "Webflow": ["webflow.com"],
    "Drupal": ["drupal-settings-json", "/sites/default/files"], "PrestaShop": ["prestashop"],
    "Magento": ["mage/cookies", "/static/frontend/magento"], "Next.js": ["__next_data__", "/_next/"],
    "Nuxt": ["__nuxt", "/_nuxt/"], "React": ["data-reactroot", "react-dom"],
    "Vue.js": ["vue.min.js", "vue.global", "data-v-"], "Angular": ["ng-version"],
    "jQuery": ["jquery"], "Bootstrap": ["bootstrap.min", "bootstrap@"], "Tailwind CSS": ["tailwindcss", "tailwind.min"],
    "Google Analytics": ["google-analytics.com", "gtag/js"], "Google Tag Manager": ["googletagmanager.com"],
    "Matomo": ["matomo", "piwik"], "Pixel Facebook": ["connect.facebook.net"],
    "reCAPTCHA": ["google.com/recaptcha"], "Google Fonts": ["fonts.googleapis.com"], "Font Awesome": ["fontawesome"],
}


class Annule(Exception):
    pass


class ErreurAnalyse(Exception):
    pass


# ----------------------------------------------------------------------------- utilitaires
def normaliser_url(brut):
    url = (brut or "").strip().replace(" ", "")
    if not url:
        raise ValueError("Veuillez saisir une adresse.")
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Adresse invalide (http:// ou https:// attendu).")
    return url


def meme_site(a, b):
    n = lambda u: re.sub(r"^www\.", "", (urlparse(u).hostname or "").lower())
    return n(a) == n(b)


def rels(tag):
    r = tag.get("rel") or []
    if isinstance(r, str):
        r = r.split()
    return [x.lower() for x in r]


def to_int(v, defaut=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return defaut


def classe_statut(code):
    if code == 0:
        return ERR
    if 200 <= code < 400:
        return OK
    return WARN if code in (401, 403, 429, 999) else ERR


def charger_historique():
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            return [x for x in json.load(f) if isinstance(x, str)][:15]
    except (OSError, ValueError, TypeError):
        return []


def sauver_historique(url, hist):
    h = ([url] + [x for x in hist if x != url])[:15]
    try:
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump(h, f)
    except OSError:
        pass
    return h


# ----------------------------------------------------------------------------- moteur d'analyse
class Analyseur:
    def __init__(self, url, verif_liens=True, crawl=0, verifier_ssl=True, progress=None, annule=None, schema_auto=False):
        self.url, self.verif_liens, self.crawl, self.verify = url, verif_liens, crawl, verifier_ssl
        self.schema_auto = schema_auto  # adresse saisie sans http(s) : repli sur http si https échoue
        self.progress, self.annule = progress, annule or threading.Event()
        self.checks, self.liens, self.pages, self.failles = [], [], [], []
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                               "Accept-Language": "fr,en;q=0.8"})
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=(502, 503, 504), raise_on_status=False)
        ad = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        self.s.mount("http://", ad)
        self.s.mount("https://", ad)
        if not verifier_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # -- aides
    def _p(self, msg, pct=None):
        if self.annule.is_set():
            raise Annule()
        if self.progress:
            self.progress(msg, pct)

    def add(self, section, label, valeur, statut=INFO):
        self.checks.append({"section": section, "label": label, "valeur": valeur, "statut": statut})

    def faille(self, gravite, titre, detail, recommandation):
        self.failles.append({"gravite": gravite, "titre": titre, "detail": detail, "recommandation": recommandation})

    def get(self, url, **kw):
        kw.setdefault("timeout", 15)
        return self.s.get(url, verify=self.verify, **kw)

    def _telecharger(self, url):
        r = self.get(url, stream=True)
        ct = r.headers.get("Content-Type", "").lower()
        if "html" in ct or not ct:
            return r, r.content, True
        r.close()
        return r, b"", False

    def _texte(self, url):
        """Petit fichier texte (robots.txt, sitemap) ; None si absent ou faux 200 HTML."""
        try:
            r = self.get(url, timeout=8, stream=True)
            ct = r.headers.get("Content-Type", "").lower()
            if r.status_code != 200 or "html" in ct:
                r.close()
                return None
            data = r.raw.read(300_000, decode_content=True)
            r.close()
            return data.decode("utf-8", "ignore")
        except requests.RequestException:
            return None

    # -- exécution
    def run(self):
        debut = time.time()
        self._p("Chargement de la page…", 3)
        try:
            t = time.time()
            try:
                r, contenu, est_html = self._telecharger(self.url)
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                if self.schema_auto and self.url.startswith("https://") and not isinstance(e, requests.exceptions.Timeout):
                    self._p("HTTPS indisponible, essai en HTTP…", 5)
                    self.url = "http://" + self.url[len("https://"):]
                    r, contenu, est_html = self._telecharger(self.url)
                else:
                    raise
            total = time.time() - t
        except requests.exceptions.SSLError:
            raise ErreurAnalyse("Erreur de certificat SSL.\nCochez « Ignorer les erreurs SSL » pour analyser quand même.")
        except requests.exceptions.Timeout:
            raise ErreurAnalyse("Délai dépassé : le site ne répond pas.")
        except requests.exceptions.TooManyRedirects:
            raise ErreurAnalyse("Trop de redirections (boucle probable).")
        except requests.exceptions.ConnectionError:
            raise ErreurAnalyse("Connexion impossible : site introuvable, hors ligne, ou pas d'accès Internet.")
        except requests.exceptions.RequestException as e:
            raise ErreurAnalyse(f"Erreur réseau : {e}")

        self.r, self.final = r, r.url
        p = urlparse(r.url)
        self.host, self.https = p.hostname, p.scheme == "https"
        self.origine = f"{p.scheme}://{p.netloc}"
        soup, base = None, self.final
        if est_html and contenu:
            soup = BeautifulSoup(contenu, "html.parser")
            b = soup.find("base", href=True)
            if b:
                base = urljoin(self.final, b["href"])
        html_low = contenu[:3_000_000].decode("utf-8", "ignore").lower()

        self._p("Analyse générale…", 12)
        self._general(r, contenu, total, est_html)
        self._p("Analyse réseau (DNS, certificat)…", 20)
        self._reseau()
        self._p("Contrôles de sécurité…", 32)
        self._securite(r, soup)
        internes = externes = set()
        if soup:
            self._p("Analyse SEO…", 45)
            self._seo(soup, base)
            self._p("Détection des technologies…", 58)
            self._techno(r, soup, html_low)
            internes, externes = self._extraire_liens(soup, base)
            self._contenu(soup, contenu, internes, externes)

        if self.verif_liens and (internes or externes):
            self._verifier_liens(internes, externes)
        if self.crawl > 0 and soup:
            self._crawler(internes)

        scores = {}
        for sec in dict.fromkeys(c["section"] for c in self.checks):
            pts = [{OK: 1, WARN: 0.5, ERR: 0}[c["statut"]] for c in self.checks
                   if c["section"] == sec and c["statut"] != INFO]
            if pts:
                scores[sec] = round(100 * sum(pts) / len(pts))
        tous = [{OK: 1, WARN: 0.5, ERR: 0}[c["statut"]] for c in self.checks if c["statut"] != INFO]
        self._p("Terminé.", 100)
        return {
            "url": self.url, "url_finale": self.final, "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duree": round(time.time() - debut, 1), "score": round(100 * sum(tous) / len(tous)) if tous else 0,
            "scores": scores, "checks": self.checks, "liens": self.liens, "pages": self.pages,
            "en_tetes": {k: v for k, v in r.headers.items()},
            "failles": sorted(self.failles, key=lambda f: GRAVITE_ORDRE[f["gravite"]]),
        }

    # -- général / performance
    def _general(self, r, contenu, total, est_html):
        S = "Général & performance"
        code = r.status_code
        self.add(S, "Code HTTP", f"{code} {r.reason or ''}".strip(), OK if code < 300 else WARN if code < 400 else ERR)
        chaine = " → ".join([h.url for h in r.history] + [r.url])
        self.add(S, "Redirections", f"{len(r.history)}  ({chaine})" if r.history else "0",
                 OK if len(r.history) <= 1 else WARN)
        self.add(S, "URL finale", r.url)
        self.add(S, "Connexion HTTPS", "oui" if self.https else "non", OK if self.https else ERR)
        ttfb = r.elapsed.total_seconds()
        self.add(S, "Temps de réponse serveur (TTFB)", f"{ttfb:.2f} s", OK if ttfb < 0.8 else WARN if ttfb < 2 else ERR)
        self.add(S, "Temps de chargement total", f"{total:.2f} s", OK if total < 2 else WARN if total < 4 else ERR)
        taille = len(contenu) or to_int(r.headers.get("Content-Length"))
        self.add(S, "Taille de la page", f"{taille / 1024:.1f} Ko", OK if taille < 200_000 else WARN if taille < 600_000 else ERR)
        ce = r.headers.get("Content-Encoding")
        self.add(S, "Compression", ce or "aucune", OK if ce else WARN)
        cc = r.headers.get("Cache-Control")
        self.add(S, "Cache-Control", cc or "absent", OK if cc else WARN)
        self.add(S, "Type de contenu", r.headers.get("Content-Type", "inconnu"), OK if est_html else WARN)
        self.add(S, "Serveur", r.headers.get("Server", "non communiqué"))

    # -- réseau
    def _reseau(self):
        S = "Réseau & certificat"
        try:
            ips = sorted({i[4][0] for i in socket.getaddrinfo(self.host, None)})
            self.add(S, "Adresses IP (DNS)", ", ".join(ips), OK)
        except OSError as e:
            self.add(S, "Adresses IP (DNS)", f"résolution impossible ({e})", ERR)
        if not self.https:
            self.add(S, "Certificat SSL", "non applicable (site en HTTP)", ERR)
            self.faille(CRIT, "Site non chiffré (HTTP)",
                       "Le site ne force pas HTTPS : toutes les données (y compris mots de passe) peuvent être interceptées.",
                       "Installer un certificat SSL (ex. Let's Encrypt, gratuit) et forcer la redirection vers HTTPS.")
            return
        port = urlparse(self.final).port or 443
        try:
            ctx = ssl.create_default_context()
            if not self.verify:
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            with socket.create_connection((self.host, port), timeout=8) as sock:
                with ctx.wrap_socket(sock, server_hostname=self.host) as ss:
                    cert, version = ss.getpeercert(), ss.version()
        except ssl.SSLCertVerificationError as e:
            self.add(S, "Certificat SSL", f"invalide : {e.verify_message}", ERR)
            self.faille(CRIT, "Certificat SSL invalide",
                       f"Le certificat du site n'est pas valide : {e.verify_message}. Les navigateurs afficheront un avertissement.",
                       "Renouveler ou reconfigurer le certificat SSL auprès de l'hébergeur ou de l'autorité de certification.")
            return
        except (OSError, ssl.SSLError) as e:
            self.add(S, "Certificat SSL", f"non vérifiable ({e})", WARN)
            return
        self.add(S, "Version TLS", version, OK if version in ("TLSv1.3", "TLSv1.2") else ERR)
        if version not in ("TLSv1.3", "TLSv1.2"):
            self.faille(HAUTE, "Protocole TLS obsolète",
                       f"Le serveur utilise {version}, une version ancienne et vulnérable du chiffrement.",
                       "Désactiver les protocoles TLS 1.0/1.1 et SSLv3 côté serveur ; n'accepter que TLS 1.2 et 1.3.")
        if cert:
            fin = ssl.cert_time_to_seconds(cert["notAfter"])
            jours = int((fin - time.time()) / 86400)
            date = datetime.fromtimestamp(fin).strftime("%d/%m/%Y")
            self.add(S, "Expiration du certificat", f"{jours} jour(s) — le {date}",
                     ERR if jours < 0 else WARN if jours < 21 else OK)
            if jours < 0:
                self.faille(CRIT, "Certificat SSL expiré",
                           f"Le certificat a expiré le {date}. Les visiteurs voient un avertissement de sécurité bloquant.",
                           "Renouveler le certificat SSL immédiatement.")
            elif jours < 21:
                self.faille(MOY, "Certificat SSL bientôt expiré",
                           f"Le certificat expire dans {jours} jour(s), le {date}.",
                           "Renouveler le certificat avant son expiration (idéalement via un renouvellement automatique).")
            emet = dict(x[0] for x in cert.get("issuer", ()))
            self.add(S, "Émetteur", emet.get("organizationName") or emet.get("commonName", "inconnu"))
        else:
            self.add(S, "Certificat SSL", "informations non lues (vérification désactivée)", WARN)

    # -- sécurité
    def _securite(self, r, soup):
        S = "Sécurité"
        h = r.headers
        EXPL = {
            "Content-Security-Policy": ("HAUTE", "Sans CSP, le site est plus exposé aux failles XSS (injection de scripts malveillants).",
                                        "Mettre en place un en-tête Content-Security-Policy limitant les sources de scripts/styles autorisées."),
            "X-Frame-Options": ("MOY", "Sans cet en-tête, le site peut être intégré dans un cadre invisible sur un autre site (clickjacking).",
                               "Ajouter l'en-tête X-Frame-Options: SAMEORIGIN (ou via CSP frame-ancestors)."),
            "X-Content-Type-Options": ("FAIBLE", "Le navigateur peut deviner le type d'un fichier et exécuter du contenu inattendu.",
                                       "Ajouter l'en-tête X-Content-Type-Options: nosniff."),
            "Referrer-Policy": ("FAIBLE", "Sans cette règle, l'URL complète peut fuiter vers des sites externes via les liens cliqués.",
                                "Ajouter l'en-tête Referrer-Policy (ex. strict-origin-when-cross-origin)."),
            "Permissions-Policy": ("FAIBLE", "Sans cette règle, le navigateur n'encadre pas l'accès aux fonctions sensibles (caméra, micro, géoloc) par des scripts tiers.",
                                   "Ajouter l'en-tête Permissions-Policy pour restreindre ces accès."),
            "Strict-Transport-Security": ("HAUTE", "Sans HSTS, un visiteur tapant l'adresse sans https:// peut être intercepté avant la redirection.",
                                          "Ajouter l'en-tête Strict-Transport-Security (HSTS) avec une durée d'au moins 6 mois."),
        }
        attendus = [("Content-Security-Policy", True), ("X-Frame-Options", True),
                    ("X-Content-Type-Options", True), ("Referrer-Policy", True),
                    ("Permissions-Policy", True), ("Strict-Transport-Security", self.https)]
        for nom, applicable in attendus:
            if applicable:
                present = nom in h
                self.add(S, nom, h.get(nom, "absent"), OK if present else WARN)
                if not present:
                    g, detail, reco = EXPL[nom]
                    self.faille(globals()[g], f"En-tête de sécurité manquant : {nom}", detail, reco)
        srv = h.get("Server", "")
        if re.search(r"\d", srv):
            self.add(S, "Version du serveur exposée", srv, WARN)
            self.faille(FAIBLE, "Version du serveur exposée",
                       f"L'en-tête Server révèle : « {srv} ». Un attaquant peut cibler des failles connues de cette version.",
                       "Masquer la version exacte dans la configuration du serveur web.")
        if "X-Powered-By" in h:
            self.add(S, "X-Powered-By exposé", h["X-Powered-By"], WARN)
            self.faille(FAIBLE, "Technologie serveur exposée (X-Powered-By)",
                       f"L'en-tête révèle : « {h['X-Powered-By']} », facilitant la reconnaissance par un attaquant.",
                       "Désactiver l'en-tête X-Powered-By côté serveur.")
        cookies = list(self.s.cookies)
        if cookies:
            cookies_fragiles = 0
            for c in cookies[:15]:
                manque = []
                if self.https and not c.secure:
                    manque.append("Secure")
                if not c.has_nonstandard_attr("HttpOnly"):
                    manque.append("HttpOnly")
                if not c.has_nonstandard_attr("SameSite"):
                    manque.append("SameSite")
                self.add(S, f"Cookie « {c.name} »", ("manque : " + ", ".join(manque)) if manque else "bien configuré",
                         WARN if manque else OK)
                if manque:
                    cookies_fragiles += 1
            if cookies_fragiles:
                self.faille(MOY, f"{cookies_fragiles} cookie(s) mal sécurisé(s)",
                           "Des attributs Secure, HttpOnly ou SameSite manquent sur un ou plusieurs cookies, "
                           "ce qui facilite le vol de session (XSS) ou les attaques CSRF.",
                           "Ajouter les attributs Secure, HttpOnly et SameSite=Lax (ou Strict) à tous les cookies sensibles.")
        else:
            self.add(S, "Cookies", "aucun cookie posé")
        if self.https:
            try:
                rh = self.s.get(f"http://{self.host}/", timeout=8, allow_redirects=False, verify=self.verify)
                loc = rh.headers.get("Location", "")
                ok = rh.status_code in (301, 302, 307, 308) and loc.lower().startswith("https")
                self.add(S, "Redirection HTTP → HTTPS", f"oui ({rh.status_code})" if ok else "non", OK if ok else WARN)
                if not ok:
                    self.faille(HAUTE, "Pas de redirection automatique vers HTTPS",
                               "Un visiteur arrivant en http:// reste en clair au lieu d'être redirigé vers la version chiffrée.",
                               "Configurer une redirection 301 permanente de http:// vers https:// sur tout le site.")
            except requests.RequestException:
                self.add(S, "Redirection HTTP → HTTPS", "test impossible", INFO)
        if soup:
            if self.https:
                dur = [t for t in soup.find_all(["script", "iframe"], src=True) if t["src"].lower().startswith("http://")]
                dur += [t for t in soup.find_all("link", href=True) if "stylesheet" in rels(t) and t["href"].lower().startswith("http://")]
                img = [t for t in soup.find_all("img", src=True) if t["src"].lower().startswith("http://")]
                if dur:
                    self.add(S, "Contenu mixte actif (script/CSS/iframe)", f"{len(dur)} ressource(s) en HTTP", ERR)
                    self.faille(HAUTE, "Contenu mixte actif (script/CSS non chiffré)",
                               f"{len(dur)} ressource(s) chargée(s) en HTTP sur une page HTTPS : un attaquant sur le réseau "
                               "peut les modifier pour injecter du code malveillant.",
                               "Charger tous les scripts, styles et iframes en HTTPS (liens relatifs ou protocole explicite).")
                elif img:
                    self.add(S, "Contenu mixte (images)", f"{len(img)} image(s) en HTTP", WARN)
                    self.faille(FAIBLE, "Contenu mixte (images en HTTP)",
                               f"{len(img)} image(s) chargée(s) en HTTP sur une page HTTPS.",
                               "Charger les images en HTTPS pour éviter l'avertissement « contenu non sécurisé » du navigateur.")
                else:
                    self.add(S, "Contenu mixte", "aucun", OK)
            elif soup.find("input", attrs={"type": "password"}):
                self.add(S, "Champ mot de passe sur page HTTP", "données envoyées en clair", ERR)
                self.faille(CRIT, "Mot de passe transmis sans chiffrement",
                           "Un formulaire de mot de passe est présent sur une page HTTP : les identifiants circulent en clair sur le réseau.",
                           "Activer HTTPS sur tout le site, en particulier les pages de connexion, avant toute mise en production.")

    # -- SEO
    def _seo(self, soup, base):
        S = "SEO"
        t = soup.title.get_text(strip=True) if soup.title else ""
        self.add(S, "Titre", f"{t}  ({len(t)} car.)" if t else "ABSENT", (OK if 30 <= len(t) <= 60 else WARN) if t else ERR)
        m = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        d = (m.get("content") or "").strip() if m else ""
        self.add(S, "Meta description", f"{d}  ({len(d)} car.)" if d else "ABSENTE", (OK if 70 <= len(d) <= 160 else WARN) if d else ERR)
        h1 = soup.find_all("h1")
        self.add(S, "Balises H1", f"{len(h1)}" + (f" — {h1[0].get_text(strip=True)[:80]}" if h1 else ""), OK if len(h1) == 1 else ERR if not h1 else WARN)
        self.add(S, "Balises H2 / H3", f"{len(soup.find_all('h2'))} / {len(soup.find_all('h3'))}")
        canon = next((l for l in soup.find_all("link", href=True) if "canonical" in rels(l)), None)
        self.add(S, "URL canonique", canon["href"] if canon else "absente", OK if canon else WARN)
        lang = soup.html.get("lang") if soup.html else None
        self.add(S, "Langue (attribut lang)", lang or "absente", OK if lang else WARN)
        self.add(S, "Viewport mobile", "oui" if soup.find("meta", attrs={"name": "viewport"}) else "NON",
                 OK if soup.find("meta", attrs={"name": "viewport"}) else ERR)
        og = [k for k in ("og:title", "og:description", "og:image") if soup.find("meta", attrs={"property": k})]
        manque = [k for k in ("og:title", "og:description", "og:image") if k not in og]
        self.add(S, "Open Graph (réseaux sociaux)", "complet" if not manque else "manque : " + ", ".join(manque), OK if not manque else WARN)
        tw = soup.find("meta", attrs={"name": "twitter:card"})
        self.add(S, "Twitter Card", tw.get("content", "oui") if tw else "absente", OK if tw else WARN)
        rb = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
        rbv = (rb.get("content") or "").lower() if rb else ""
        xr = self.r.headers.get("X-Robots-Tag", "").lower()
        noindex = "noindex" in rbv or "noindex" in xr
        self.add(S, "Indexation", "NOINDEX : la page est exclue de Google" if noindex else "indexable", WARN if noindex else OK)
        ld = soup.find_all("script", attrs={"type": "application/ld+json"})
        invalides = 0
        for s in ld:
            try:
                json.loads(s.string or "")
            except ValueError:
                invalides += 1
        self.add(S, "Données structurées (JSON-LD)", f"{len(ld)} bloc(s)" + (f", {invalides} invalide(s)" if invalides else ""),
                 WARN if invalides else (OK if ld else INFO))
        self.add(S, "Balises hreflang", str(len([l for l in soup.find_all("link") if (l.get("hreflang"))])))
        imgs = soup.find_all("img")
        sans_alt = [i for i in imgs if not (i.get("alt") or "").strip()]
        self.add(S, "Images sans texte alternatif", f"{len(sans_alt)} sur {len(imgs)}", OK if not sans_alt else WARN)
        icone = any("icon" in " ".join(rels(l)) for l in soup.find_all("link"))
        self.add(S, "Favicon", "déclaré dans le HTML" if icone else "non déclaré", OK if icone else WARN)

        self._p("Lecture de robots.txt et du sitemap…", 52)
        robots = self._texte(self.origine + "/robots.txt")
        if robots is None:
            self.add(S, "robots.txt", "absent", WARN)
        else:
            etoile, prec_ua, bloque = False, False, False
            for lg in robots.splitlines():
                l = lg.split("#")[0].strip().lower()
                if l.startswith("user-agent:"):
                    if not prec_ua:
                        etoile = False
                    etoile = etoile or l.split(":", 1)[1].strip() == "*"
                    prec_ua = True
                elif l:
                    prec_ua = False
                    if etoile and re.fullmatch(r"disallow:\s*/", l):
                        bloque = True
            self.add(S, "robots.txt", "BLOQUE tout le site (Disallow: /)" if bloque else "présent", ERR if bloque else OK)
            if bloque:
                self.faille(MOY, "robots.txt bloque l'indexation de tout le site",
                           "La règle « Disallow: / » pour tous les robots empêche Google et les autres moteurs d'indexer le site "
                           "(ce n'est pas une faille de sécurité, mais un risque de visibilité important si c'est involontaire).",
                           "Vérifier si c'est volontaire (site en construction) ; sinon, retirer cette règle de robots.txt.")
        sm_urls = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots or "")
        sm = self._texte(sm_urls[0] if sm_urls else self.origine + "/sitemap.xml")
        if sm is None:
            self.add(S, "Sitemap", "introuvable", WARN)
        else:
            n = len(re.findall(r"<loc>", sm, re.I))
            self.add(S, "Sitemap", f"{n} entrée(s)" + (" (index de sitemaps)" if "<sitemapindex" in sm.lower() else ""), OK)

    # -- technologies
    def _techno(self, r, soup, html_low):
        S = "Technologies"
        trouvees = {nom for nom, sigs in SIGNATURES.items() if any(s in html_low for s in sigs)}
        h = {k.lower(): v for k, v in r.headers.items()}
        if "cf-ray" in h or "cloudflare" in h.get("server", "").lower():
            trouvees.add("Cloudflare")
        if "x-vercel-id" in h:
            trouvees.add("Vercel")
        if "x-github-request-id" in h:
            trouvees.add("GitHub Pages")
        if "cloudfront" in h.get("via", "").lower() or "x-amz-cf-id" in h:
            trouvees.add("Amazon CloudFront")
        if "x-drupal-cache" in h:
            trouvees.add("Drupal")
        gen = soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
        if gen and gen.get("content"):
            self.add(S, "Générateur déclaré", gen["content"])
        self.add(S, "Technologies détectées (heuristique)", ", ".join(sorted(trouvees)) or "aucune détectée")
        if "x-powered-by" in h:
            self.add(S, "Technologie serveur", h["x-powered-by"])

    # -- liens / contenu
    def _extraire_liens(self, soup, base):
        internes, externes = set(), set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.lower().startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "data:")):
                continue
            url = urldefrag(urljoin(base, href))[0]
            if url.startswith(("http://", "https://")):
                (internes if meme_site(url, self.final) else externes).add(url)
        return internes, externes

    def _contenu(self, soup, contenu, internes, externes):
        S = "Contenu & ressources"
        scripts = soup.find_all("script")
        ext = [s for s in scripts if s.get("src")]
        css = [l for l in soup.find_all("link") if "stylesheet" in rels(l)]
        self.add(S, "Scripts", f"{len(scripts)} (dont {len(ext)} externes)", OK if len(ext) <= 15 else WARN)
        self.add(S, "Feuilles de style", str(len(css)), OK if len(css) <= 8 else WARN)
        self.add(S, "Images", str(len(soup.find_all("img"))))
        self.add(S, "iFrames / formulaires", f"{len(soup.find_all('iframe'))} / {len(soup.find_all('form'))}")
        self.add(S, "Liens internes / externes", f"{len(internes)} / {len(externes)}")
        vides = [a for a in soup.find_all("a", href=True)
                 if not a.get_text(strip=True) and not a.find("img") and not a.get("aria-label") and not a.get("title")]
        self.add(S, "Liens sans texte (accessibilité)", str(len(vides)), OK if not vides else WARN)
        for t in soup(["script", "style", "noscript", "template"]):
            t.decompose()
        texte = soup.get_text(" ", strip=True)
        mots = len(re.findall(r"\w+", texte))
        self.add(S, "Nombre de mots", str(mots), OK if mots >= 300 else WARN)
        ratio = 100 * len(texte) / len(contenu) if contenu else 0
        self.add(S, "Ratio texte / HTML", f"{ratio:.0f} %", OK if ratio >= 10 else WARN)

    # -- vérification des liens
    def _tester_lien(self, url):
        t = time.time()
        r = None
        try:
            r = self.s.head(url, timeout=8, allow_redirects=True, verify=self.verify)
        except requests.RequestException:
            pass
        if r is None or r.status_code in (400, 403, 405, 429, 501, 503):
            try:
                r = self.s.get(url, timeout=10, stream=True, verify=self.verify)
                r.close()
            except requests.RequestException as e:
                return {"url": url, "statut": 0, "final": "", "temps": round(time.time() - t, 2), "erreur": type(e).__name__}
        return {"url": url, "statut": r.status_code, "final": r.url if r.url != url else "",
                "temps": round(time.time() - t, 2), "erreur": ""}

    def _verifier_liens(self, internes, externes, limite=300):
        S = "Liens"
        liste = [(u, "interne") for u in sorted(internes)] + [(u, "externe") for u in sorted(externes)]
        tronque = len(liste) > limite
        liste = liste[:limite]
        types = dict(liste)
        done = 0
        ex = ThreadPoolExecutor(max_workers=12)
        try:
            futs = [ex.submit(self._tester_lien, u) for u, _ in liste]
            for f in as_completed(futs):
                if self.annule.is_set():
                    raise Annule()
                res = f.result()
                res["type"] = types[res["url"]]
                self.liens.append(res)
                done += 1
                self._p(f"Vérification des liens… {done}/{len(liste)}", 62 + 23 * done / len(liste))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        casses = [l for l in self.liens if classe_statut(l["statut"]) == ERR]
        bloques = [l for l in self.liens if classe_statut(l["statut"]) == WARN]
        self.add(S, "Liens vérifiés", f"{len(liste)}" + (f" (limité à {limite})" if tronque else ""))
        self.add(S, "Liens cassés", str(len(casses)), OK if not casses else ERR)
        if bloques:
            self.add(S, "Liens protégés (403/429…)", f"{len(bloques)} (blocage anti-robot probable)", INFO)
        lents = [l for l in self.liens if l["temps"] > 3 and classe_statut(l["statut"]) != ERR]
        self.add(S, "Liens lents (> 3 s)", str(len(lents)), OK if not lents else WARN)

    # -- crawl
    def _page(self, url):
        debut = time.time()
        info = {"url": url, "statut": 0, "titre": "", "h1": 0, "mots": 0, "taille": 0, "temps": 0.0, "erreur": ""}
        liens = set()
        try:
            r, contenu, est_html = self._telecharger(url)
        except requests.RequestException as e:
            info["erreur"] = type(e).__name__
            return info, liens
        info["statut"], info["url"] = r.status_code, r.url
        info["taille"] = len(contenu) or to_int(r.headers.get("Content-Length"))
        if est_html and contenu:
            s = BeautifulSoup(contenu, "html.parser")
            info["titre"] = s.title.get_text(strip=True) if s.title else ""
            info["h1"] = len(s.find_all("h1"))
            base = r.url
            b = s.find("base", href=True)
            if b:
                base = urljoin(r.url, b["href"])
            liens, _ = self._extraire_liens(s, base)
            for t in s(["script", "style", "noscript"]):
                t.decompose()
            info["mots"] = len(re.findall(r"\w+", s.get_text(" ", strip=True)))
        info["temps"] = round(time.time() - debut, 2)
        return info, liens

    def _crawler(self, departs):
        S = "Crawl"
        vus = {urldefrag(self.final)[0]}
        file = [u for u in sorted(departs) if not EXT_IGNOREES.search(urlparse(u).path)]
        vus.update(file)
        with ThreadPoolExecutor(max_workers=6) as ex:
            while file and len(self.pages) < self.crawl:
                lot, file = file[: self.crawl - len(self.pages)], file[self.crawl - len(self.pages):]
                for info, liens in ex.map(self._page, lot):
                    if self.annule.is_set():
                        raise Annule()
                    self.pages.append(info)
                    for u in sorted(liens):
                        if u not in vus and not EXT_IGNOREES.search(urlparse(u).path):
                            vus.add(u)
                            file.append(u)
                self._p(f"Exploration du site… {len(self.pages)}/{self.crawl} pages", 85 + 13 * len(self.pages) / self.crawl)
        P = self.pages
        err = [p for p in P if classe_statut(p["statut"]) == ERR]
        sans_titre = [p for p in P if p["statut"] == 200 and not p["titre"]]
        dup = [t for t, n in Counter(p["titre"] for p in P if p["titre"] and p["statut"] == 200).items() if n > 1]
        sans_h1 = [p for p in P if p["statut"] == 200 and p["h1"] == 0]
        lentes = [p for p in P if p["temps"] > 2.5]
        self.add(S, "Pages explorées", str(len(P)))
        self.add(S, "Pages en erreur (4xx/5xx)", str(len(err)), OK if not err else ERR)
        self.add(S, "Pages sans titre", str(len(sans_titre)), OK if not sans_titre else WARN)
        self.add(S, "Titres dupliqués", str(len(dup)), OK if not dup else WARN)
        self.add(S, "Pages sans H1", str(len(sans_h1)), OK if not sans_h1 else WARN)
        self.add(S, "Pages lentes (> 2,5 s)", str(len(lentes)), OK if not lentes else WARN)


# ----------------------------------------------------------------------------- rapports
def rapport_texte(res):
    L = [f"RAPPORT D'ANALYSE — {res['url_finale']}",
         f"Date : {res['date']}   Durée : {res['duree']} s   Score global : {res['score']}/100"]
    sec = None
    for c in res["checks"]:
        if c["section"] != sec:
            sec = c["section"]
            sc = res["scores"].get(sec)
            L += ["", f"## {sec}" + (f" ({sc} %)" if sc is not None else "")]
        L.append(f"  {SYMB[c['statut']]}{c['label']} : {c['valeur']}")
    if res.get("failles"):
        L += ["", "## Failles de sécurité détectées"]
        for f in res["failles"]:
            L.append(f"  {GRAVITE_LABEL[f['gravite']]} — {f['titre']}")
            L.append(f"      Problème      : {f['detail']}")
            L.append(f"      Recommandation: {f['recommandation']}")
    cass = [l for l in res["liens"] if classe_statut(l["statut"]) == ERR]
    if cass:
        L += ["", "## Liens cassés"] + [f"  [{l['statut'] or l['erreur']}] {l['url']}" for l in cass]
    pb = [p for p in res["pages"] if classe_statut(p["statut"]) == ERR]
    if pb:
        L += ["", "## Pages en erreur"] + [f"  [{p['statut'] or p['erreur']}] {p['url']}" for p in pb]
    return "\n".join(L) + "\n"


def exporter_fichier(res, chemin):
    ext = os.path.splitext(chemin)[1].lower()
    if ext == ".json":
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    elif ext == ".csv":
        with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["CONTRÔLES"])
            w.writerow(["section", "contrôle", "valeur", "état"])
            w.writerows([[c["section"], c["label"], c["valeur"], ETIQ[c["statut"]]] for c in res["checks"]])
            w.writerow([])
            w.writerow(["FAILLES DE SÉCURITÉ"])
            w.writerow(["gravité", "titre", "problème", "recommandation"])
            w.writerows([[GRAVITE_LABEL[f["gravite"]], f["titre"], f["detail"], f["recommandation"]] for f in res.get("failles", [])])
            w.writerow([])
            w.writerow(["LIENS"])
            w.writerow(["statut", "type", "temps (s)", "url", "redirigé vers", "erreur"])
            w.writerows([[l["statut"], l["type"], l["temps"], l["url"], l["final"], l["erreur"]] for l in res["liens"]])
            w.writerow([])
            w.writerow(["PAGES"])
            w.writerow(["statut", "titre", "h1", "mots", "taille (octets)", "temps (s)", "url"])
            w.writerows([[p["statut"], p["titre"], p["h1"], p["mots"], p["taille"], p["temps"], p["url"]] for p in res["pages"]])
    else:
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(rapport_texte(res))


# ----------------------------------------------------------------------------- interface
BG, PANEL, PANEL2, HOVER, SEL = "#0f1115", "#171a21", "#222733", "#2d3444", "#27406b"
FG, MUTED, ACCENT = "#e6e9ef", "#8a93a6", "#5aa2ff"
COUL = {OK: "#4cc38a", WARN: "#f5b83d", ERR: "#ef5b5b", INFO: "#8a93a6"}
COUL_GRAV = {CRIT: "#ff5c5c", HAUTE: "#f5833d", MOY: "#f5c93d", FAIBLE: "#6aa8ff"}
FONT, FONT_B, MONO = ("Segoe UI", 10), ("Segoe UI", 10, "bold"), ("Consolas", 10)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    tk = None


class App(tk.Tk if tk else object):
    def __init__(self):
        super().__init__()
        self.title("Analyseur Web Pro")
        self.geometry("1150x720")
        self.minsize(860, 540)
        self.configure(bg=BG)
        self.q = queue.Queue()
        self.annule = threading.Event()
        self.thread = None
        self.result = None
        self.details = {}
        self._failles_idx = {}
        self.historique = charger_historique()
        self._theme()
        self._ui()
        self._titre_sombre()
        self.protocol("WM_DELETE_WINDOW", self.quitter)
        self.after(100, self._poll)

    # -- thème
    def _theme(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL2,
                    lightcolor=PANEL2, darkcolor=PANEL2, troughcolor=PANEL, focuscolor=BG, font=FONT)
        s.configure("TButton", background=PANEL2, foreground=FG, padding=(12, 6), borderwidth=0)
        s.map("TButton", background=[("active", HOVER), ("disabled", PANEL)], foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=ACCENT, foreground="#0b0d12", font=FONT_B)
        s.map("Accent.TButton", background=[("active", "#8bbcff"), ("disabled", PANEL2)], foreground=[("disabled", MUTED)])
        s.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, padding=6)
        s.configure("TCombobox", fieldbackground=PANEL, background=PANEL2, foreground=FG, arrowcolor=FG,
                    insertcolor=FG, padding=6, selectbackground=PANEL, selectforeground=FG)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.map("TCheckbutton", background=[("active", BG)], indicatorcolor=[("selected", ACCENT), ("!selected", PANEL)])
        s.configure("TSpinbox", fieldbackground=PANEL, foreground=FG, background=PANEL2, arrowcolor=FG, insertcolor=FG)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 8), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", PANEL2)], foreground=[("selected", FG)])
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=26, borderwidth=0)
        s.map("Treeview", background=[("selected", SEL)], foreground=[("selected", "#ffffff")])
        s.configure("Treeview.Heading", background=PANEL2, foreground=FG, relief="flat", font=FONT_B, padding=6)
        s.map("Treeview.Heading", background=[("active", HOVER)])
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL, borderwidth=0, thickness=6)
        s.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=BG, arrowcolor=FG, borderwidth=0)
        s.map("Vertical.TScrollbar", background=[("active", HOVER)])
        for opt, val in (("background", PANEL), ("foreground", FG), ("selectBackground", SEL), ("selectForeground", "#ffffff")):
            self.option_add(f"*TCombobox*Listbox.{opt}", val)

    def _titre_sombre(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

    # -- construction
    def _arbre(self, parent, cols, entetes, largeurs, show="headings", tri=True, multi=False):
        fr = ttk.Frame(parent)
        fr.pack(fill="both", expand=True)
        t = ttk.Treeview(fr, columns=cols, show=show, selectmode="extended" if multi else "browse")
        sb = ttk.Scrollbar(fr, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        grand = max(range(len(largeurs)), key=largeurs.__getitem__)
        for i, (c, h, w) in enumerate(zip(cols, entetes, largeurs)):
            t.heading(c, text=h, command=(lambda c=c, t=t: self._trier(t, c, False)) if tri else "")
            t.column(c, width=w, anchor="w", stretch=(i == grand))
        for st, col in COUL.items():
            t.tag_configure(st, foreground=col)
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        return t

    def _ui(self):
        haut = ttk.Frame(self, padding=(14, 14, 14, 4))
        haut.pack(fill="x")
        ttk.Label(haut, text="Adresse du site", font=FONT_B).pack(side="left")
        self.url = tk.StringVar()
        self.combo = ttk.Combobox(haut, textvariable=self.url, values=self.historique, width=50)
        self.combo.pack(side="left", padx=10, fill="x", expand=True)
        self.combo.focus()
        self.combo.bind("<Return>", lambda e: self.lancer())
        self.bouton = ttk.Button(haut, text="Analyser", style="Accent.TButton", command=self.lancer)
        self.bouton.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(haut, text="Annuler", command=self.annuler, state="disabled")
        self.btn_stop.pack(side="left", padx=4)

        opts = ttk.Frame(self, padding=(14, 4))
        opts.pack(fill="x")
        self.v_liens, self.v_crawl, self.v_ssl = tk.BooleanVar(value=True), tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.v_pages = tk.StringVar(value="20")
        ttk.Checkbutton(opts, text="Vérifier les liens", variable=self.v_liens).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opts, text="Explorer le site (pages max :)", variable=self.v_crawl).pack(side="left")
        ttk.Spinbox(opts, from_=2, to=200, width=5, textvariable=self.v_pages).pack(side="left", padx=(6, 14))
        ttk.Checkbutton(opts, text="Ignorer les erreurs SSL", variable=self.v_ssl).pack(side="left")
        ttk.Button(opts, text="Copier", command=self.copier).pack(side="right", padx=(4, 0))
        ttk.Button(opts, text="Exporter…", command=self.exporter).pack(side="right", padx=4)
        self.score = tk.Label(opts, text="", bg=BG, fg=MUTED, font=("Segoe UI", 15, "bold"))
        self.score.pack(side="right", padx=16)

        prog = ttk.Frame(self, padding=(14, 4))
        prog.pack(fill="x")
        self.barre = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.barre.pack(fill="x")
        self.statut = tk.Label(prog, text="Prêt. Saisissez une adresse puis cliquez sur Analyser.", bg=BG, fg=MUTED, anchor="w", font=FONT)
        self.statut.pack(fill="x", pady=(4, 0))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=14, pady=(4, 6))
        onglets = [ttk.Frame(self.nb) for _ in range(6)]
        for fr, nom in zip(onglets, ("⚠ Failles de sécurité", "Résumé", "Liens", "Pages (crawl)", "En-têtes HTTP", "Journal")):
            self.nb.add(fr, text=nom)

        bandeau = ttk.Frame(onglets[0], padding=(0, 6))
        bandeau.pack(fill="x")
        self.lbl_failles = tk.Label(bandeau, text="Lancez une analyse pour voir les failles détectées.", bg=BG, fg=MUTED, font=FONT, anchor="w")
        self.lbl_failles.pack(side="left")
        ttk.Button(bandeau, text="Copier le rapport des failles", command=self._copier_failles).pack(side="right")
        self.t_failles = self._arbre(onglets[0], ("gravite", "titre", "detail"), ("Gravité", "Faille", "Problème"), (110, 260, 520), multi=True)
        self.t_failles.bind("<<TreeviewSelect>>", self._detail_faille)
        for g, c in COUL_GRAV.items():
            self.t_failles.tag_configure(g, foreground=c)

        self.tree = self._arbre(onglets[1], ("valeur", "etat"), ("Valeur", "État"), (700, 130), show="tree headings", tri=False)
        self.tree.heading("#0", text="Contrôle")
        self.tree.column("#0", width=300, stretch=False)
        self.tree.tag_configure("section", foreground=ACCENT, font=FONT_B)
        self.tree.bind("<<TreeviewSelect>>", self._detail)

        barre_l = ttk.Frame(onglets[2])
        barre_l.pack(fill="x", pady=(6, 4))
        self.v_pb = tk.BooleanVar(value=False)
        ttk.Checkbutton(barre_l, text="Problèmes uniquement", variable=self.v_pb, command=self._remplir_liens).pack(side="left", padx=6)
        self.lbl_liens = tk.Label(barre_l, text="", bg=BG, fg=MUTED)
        self.lbl_liens.pack(side="left", padx=10)
        ttk.Button(barre_l, text="Copier tout", command=lambda: self._copier_tout(self.t_liens)).pack(side="right", padx=6)
        ttk.Button(barre_l, text="Copier les cassés", command=self._copier_casses).pack(side="right")
        self.t_liens = self._arbre(onglets[2], ("statut", "type", "temps", "url", "final"),
                                   ("Statut", "Type", "Temps", "URL (double-clic pour ouvrir)", "Redirigé vers"), (70, 80, 70, 560, 300), multi=True)
        self.t_pages = self._arbre(onglets[3], ("statut", "titre", "h1", "mots", "taille", "temps", "url"),
                                   ("Statut", "Titre", "H1", "Mots", "Taille", "Temps", "URL"), (70, 260, 40, 60, 80, 70, 460), multi=True)
        self.t_head = self._arbre(onglets[4], ("nom", "valeur"), ("En-tête", "Valeur"), (260, 700), multi=True)
        for t in (self.t_liens, self.t_pages):
            t.bind("<Double-1>", lambda e, t=t: self._ouvrir(e, t))

        fj = ttk.Frame(onglets[5])
        fj.pack(fill="both", expand=True)
        self.journal = tk.Text(fj, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=MONO, state="disabled", wrap="word")
        sbj = ttk.Scrollbar(fj, orient="vertical", command=self.journal.yview)
        self.journal.configure(yscrollcommand=sbj.set)
        sbj.pack(side="right", fill="y")
        self.journal.pack(fill="both", expand=True)

        self.txt_detail = tk.Text(self, height=3, bg=PANEL, fg=FG, relief="flat", font=FONT, wrap="word", state="disabled", padx=10, pady=6)
        self.txt_detail.pack(fill="x", padx=14, pady=(0, 14))
        self._set_detail("Astuce : sélectionnez des lignes (Ctrl+clic, Maj+clic, Ctrl+A) puis Ctrl+C ou clic droit pour copier. "
                         "Ce cadre est aussi sélectionnable.")
        for t in (self.tree, self.t_liens, self.t_pages, self.t_head, self.t_failles):
            self._activer_copie(t)
        for w in (self.txt_detail, self.journal):
            w.bind("<Button-1>", lambda e, w=w: w.focus_set())

    # -- actions
    def _log(self, msg):
        self.journal.configure(state="normal")
        self.journal.insert("end", f"{datetime.now():%H:%M:%S}  {msg}\n")
        self.journal.see("end")
        self.journal.configure(state="disabled")

    def _set_detail(self, txt):
        self.txt_detail.configure(state="normal")
        self.txt_detail.delete("1.0", "end")
        self.txt_detail.insert("end", txt)
        self.txt_detail.configure(state="disabled")

    def _detail_faille(self, _e):
        sel = self.t_failles.selection()
        if sel:
            f = self.t_failles.set(sel[0])
            iid = sel[0]
            d = self._failles_idx.get(iid)
            if d:
                self._set_detail(f"{GRAVITE_LABEL[d['gravite']]} — {d['titre']}\n\nProblème : {d['detail']}\n\nRecommandation : {d['recommandation']}")

    def _copier_failles(self):
        if not self.result or not self.result.get("failles"):
            self.statut.config(text="Aucune faille à copier.", fg=COUL[OK])
            return
        lignes = []
        for f in self.result["failles"]:
            lignes.append(f"{GRAVITE_LABEL[f['gravite']]} — {f['titre']}\n"
                          f"  Problème : {f['detail']}\n  Recommandation : {f['recommandation']}")
        self._presse_papiers(lignes, "faille")

    def _detail(self, _e):
        sel = self.tree.selection()
        if sel and sel[0] in self.details:
            label, val = self.details[sel[0]]
            self._set_detail(f"{label}\n{val}")

    def lancer(self):
        if self.thread and self.thread.is_alive():
            return
        try:
            url = normaliser_url(self.url.get())
        except ValueError as e:
            messagebox.showwarning("Adresse invalide", str(e))
            return
        schema_auto = "://" not in self.url.get()
        self.url.set(url)
        try:
            pages = max(2, min(200, int(self.v_pages.get())))
        except (ValueError, tk.TclError):
            pages = 20
        for t in (self.tree, self.t_liens, self.t_pages, self.t_head, self.t_failles):
            for i in t.get_children():
                t.delete(i)
        self.details.clear()
        self._failles_idx.clear()
        self.lbl_failles.config(text="Lancez une analyse pour voir les failles détectées.", fg=MUTED)
        self.result = None
        self.score.config(text="", fg=MUTED)
        self.lbl_liens.config(text="")
        self.barre["value"] = 0
        self.annule.clear()
        self.bouton.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._log(f"--- Analyse de {url}")
        opts = dict(verif_liens=self.v_liens.get(), crawl=pages if self.v_crawl.get() else 0, verifier_ssl=not self.v_ssl.get(), schema_auto=schema_auto)
        self.thread = threading.Thread(target=self._travail, args=(url, opts), daemon=True)
        self.thread.start()

    def _travail(self, url, opts):
        try:
            a = Analyseur(url, progress=lambda m, p=None: self.q.put(("progress", m, p)), annule=self.annule, **opts)
            self.q.put(("done", a.run()))
        except Annule:
            self.q.put(("cancel",))
        except ErreurAnalyse as e:
            self.q.put(("error", str(e)))
        except Exception as e:
            self.q.put(("error", f"Erreur inattendue : {type(e).__name__} : {e}", traceback.format_exc()))

    def annuler(self):
        self.annule.set()
        self.statut.config(text="Annulation en cours…")

    def _poll(self):
        try:
            while True:
                kind, *d = self.q.get_nowait()
                if kind == "progress":
                    self.statut.config(text=d[0], fg=FG)
                    if d[1] is not None:
                        self.barre["value"] = d[1]
                    self._log(d[0])
                elif kind == "done":
                    self._fin(res=d[0])
                elif kind == "cancel":
                    self._fin(msg="Analyse annulée.")
                elif kind == "error":
                    if len(d) > 1:
                        self._log(d[1])
                    self._fin(err=d[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _fin(self, res=None, err=None, msg=None):
        self.bouton.config(state="normal")
        self.btn_stop.config(state="disabled")
        if err:
            self.barre["value"] = 0
            self.statut.config(text="Échec de l'analyse.", fg=COUL[ERR])
            self._log("ERREUR : " + err.replace("\n", " "))
            messagebox.showerror("Analyse impossible", err)
        elif msg:
            self.barre["value"] = 0
            self.statut.config(text=msg, fg=COUL[WARN])
            self._log(msg)
        elif res:
            self.result = res
            self.historique = sauver_historique(self.url.get(), self.historique)
            self.combo.configure(values=self.historique)
            self._remplir(res)

    def _remplir(self, res):
        parents = {}
        for c in res["checks"]:
            sec = c["section"]
            if sec not in parents:
                sc = res["scores"].get(sec)
                parents[sec] = self.tree.insert("", "end", text=sec, values=(f"{sc} %" if sc is not None else "", ""),
                                                open=True, tags=("section",))
            val = str(c["valeur"])
            iid = self.tree.insert(parents[sec], "end", text=c["label"], values=(val.replace("\n", " "), ETIQ[c["statut"]]),
                                   tags=(c["statut"],))
            self.details[iid] = (c["label"], val)
        for k, v in res["en_tetes"].items():
            self.t_head.insert("", "end", values=(k, v))
        for f in res.get("failles", []):
            iid = self.t_failles.insert("", "end", tags=(f["gravite"],),
                values=(GRAVITE_LABEL[f["gravite"]], f["titre"], f["detail"]))
            self._failles_idx[iid] = f
        compte = Counter(f["gravite"] for f in res.get("failles", []))
        if compte:
            morceaux = [f"{compte[g]} {GRAVITE_LABEL[g]}" for g in (CRIT, HAUTE, MOY, FAIBLE) if compte.get(g)]
            self.lbl_failles.config(text="  •  ".join(morceaux), fg=COUL_GRAV.get(CRIT if compte.get(CRIT) else HAUTE if compte.get(HAUTE) else MOY, MUTED))
        else:
            self.lbl_failles.config(text="Aucune faille détectée. ✔", fg=COUL[OK])
        self._remplir_liens()
        for p in res["pages"]:
            self.t_pages.insert("", "end", tags=(classe_statut(p["statut"]),), values=(
                p["statut"] or "ERR", p["titre"], p["h1"], p["mots"], f"{p['taille'] / 1024:.1f} Ko", f"{p['temps']} s", p["url"]))
        n_err = sum(c["statut"] == ERR for c in res["checks"])
        n_warn = sum(c["statut"] == WARN for c in res["checks"])
        s = res["score"]
        self.score.config(text=f"Score {s}/100", fg=COUL[OK] if s >= 80 else COUL[WARN] if s >= 55 else COUL[ERR])
        self.statut.config(text=f"Terminé en {res['duree']} s — {n_err} problème(s), {n_warn} point(s) à améliorer.", fg=FG)
        self.barre["value"] = 100
        self.nb.select(0 if any(f["gravite"] in (CRIT, HAUTE) for f in res.get("failles", [])) else 1)

    def _remplir_liens(self):
        for i in self.t_liens.get_children():
            self.t_liens.delete(i)
        if not self.result:
            return
        liens = self.result["liens"]
        affiches = [l for l in liens if not self.v_pb.get() or classe_statut(l["statut"]) != OK]
        for l in sorted(affiches, key=lambda x: (classe_statut(x["statut"]) == OK, x["url"])):
            self.t_liens.insert("", "end", tags=(classe_statut(l["statut"]),), values=(
                l["statut"] or "ERR", l["type"], f"{l['temps']} s", l["url"], l["final"] or l["erreur"]))
        if liens:
            casses = sum(classe_statut(l["statut"]) == ERR for l in liens)
            self.lbl_liens.config(text=f"{len(affiches)} affiché(s) sur {len(liens)} — {casses} cassé(s)")

    def _trier(self, tree, col, rev):
        def cle(x):
            v = x[0].replace(" Ko", "").replace(" s", "")
            try:
                return (0, float(v), "")
            except ValueError:
                return (1, 0.0, x[0].lower())
        lignes = sorted(((tree.set(k, col), k) for k in tree.get_children("")), key=cle, reverse=rev)
        for i, (_, k) in enumerate(lignes):
            tree.move(k, "", i)
        tree.heading(col, command=lambda: self._trier(tree, col, not rev))

    def _ouvrir(self, event, tree):
        iid = tree.identify_row(event.y)
        if iid:
            url = tree.set(iid, "url")
            if url.startswith("http"):
                webbrowser.open(url)

    # -- copier / coller
    def _presse_papiers(self, lignes, nom="élément"):
        if not lignes:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(lignes))
        self.update()  # garde le contenu disponible même si la fenêtre perd le focus
        n = len(lignes)
        self.statut.config(text=f"{n} {nom}{'s' if n > 1 else ''} copié{'s' if n > 1 else ''} dans le presse-papiers.", fg=COUL[OK])

    def _lignes(self, tree, items, mode=None):
        cols = tree["columns"]
        mode = mode or ("url" if "url" in cols else "ligne")
        out = []
        for i in items:
            if tree is self.tree:
                if i not in self.details:
                    continue
                label, val = self.details[i]
                out.append(val if mode == "valeur" else f"{label} : {val}")
            elif mode == "url" and "url" in cols:
                out.append(tree.set(i, "url"))
            elif mode == "valeur" and "valeur" in cols:
                out.append(tree.set(i, "valeur"))
            else:
                out.append("\t".join(str(v) for v in tree.item(i, "values")))
        return [x for x in out if x]

    def _copier_sel(self, tree, mode=None):
        mode_eff = mode or ("url" if "url" in tree["columns"] else "ligne")
        nom = "URL" if mode_eff == "url" else "valeur" if mode_eff == "valeur" else "ligne"
        self._presse_papiers(self._lignes(tree, tree.selection(), mode), nom)
        return "break"

    def _copier_tout(self, tree):
        mode = "url" if "url" in tree["columns"] else "ligne"
        self._presse_papiers(self._lignes(tree, tree.get_children(""), mode), "URL" if mode == "url" else "ligne")

    def _copier_casses(self):
        items = [i for i in self.t_liens.get_children("") if "err" in self.t_liens.item(i, "tags")]
        if not items:
            self.statut.config(text="Aucun lien cassé à copier.", fg=COUL[OK])
            return
        self._presse_papiers([self.t_liens.set(i, "url") for i in items], "lien cassé")

    def _activer_copie(self, tree):
        for seq in ("<Control-c>", "<Command-c>"):
            try:
                tree.bind(seq, lambda e, t=tree: self._copier_sel(t))
            except tk.TclError:
                pass
        if str(tree.cget("selectmode")) == "extended":
            tree.bind("<Control-a>", lambda e, t=tree: (t.selection_set(t.get_children("")), "break")[1])
        for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            tree.bind(seq, lambda e, t=tree: self._menu_ctx(e, t))

    def _menu_ctx(self, event, tree):
        iid = tree.identify_row(event.y)
        if iid and iid not in tree.selection():
            tree.selection_set(iid)
        m = tk.Menu(self, tearoff=0, bg=PANEL2, fg=FG, activebackground=SEL, activeforeground="#ffffff", bd=0, relief="flat")
        cols = tree["columns"]
        if tree.selection():
            if "url" in cols:
                m.add_command(label="Copier l'URL", command=lambda: self._copier_sel(tree, "url"))
                m.add_command(label="Ouvrir dans le navigateur", command=lambda: [
                    webbrowser.open(tree.set(i, "url")) for i in tree.selection()[:5] if tree.set(i, "url").startswith("http")])
            if tree is self.tree or "valeur" in cols:
                m.add_command(label="Copier la valeur", command=lambda: self._copier_sel(tree, "valeur"))
            m.add_command(label="Copier la ligne", command=lambda: self._copier_sel(tree, "ligne"))
        if tree.get_children("") and tree is not self.tree:
            m.add_separator()
            m.add_command(label="Copier toutes les URLs" if "url" in cols else "Copier tout le tableau",
                          command=lambda: self._copier_tout(tree))
        if m.index("end") is not None:
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                m.grab_release()
        return "break"

    def exporter(self):
        if not self.result:
            messagebox.showinfo("Exporter", "Lancez d'abord une analyse.")
            return
        hote = urlparse(self.result["url_finale"]).hostname or "site"
        chemin = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile=f"analyse_{hote}_{datetime.now():%Y%m%d_%H%M}",
            filetypes=[("Rapport JSON", "*.json"), ("Rapport texte", "*.txt"), ("Tableur CSV", "*.csv")])
        if not chemin:
            return
        try:
            exporter_fichier(self.result, chemin)
            self.statut.config(text=f"Rapport enregistré : {chemin}", fg=COUL[OK])
        except OSError as e:
            messagebox.showerror("Export impossible", str(e))

    def copier(self):
        if not self.result:
            messagebox.showinfo("Copier", "Lancez d'abord une analyse.")
            return
        self.clipboard_clear()
        self.clipboard_append(rapport_texte(self.result))
        self.statut.config(text="Rapport copié dans le presse-papiers.", fg=COUL[OK])

    def quitter(self):
        self.annule.set()
        self.destroy()


# ----------------------------------------------------------------------------- lancement
def cli(args):
    import argparse
    ap = argparse.ArgumentParser(prog="analyseur_pro.py --cli")
    ap.add_argument("url")
    ap.add_argument("--liens", action="store_true", help="vérifier les liens")
    ap.add_argument("--crawl", type=int, default=0, help="nombre de pages à explorer")
    ap.add_argument("--no-ssl-check", action="store_true", help="ignorer les erreurs SSL")
    a = ap.parse_args(args)
    try:
        url = normaliser_url(a.url)
        res = Analyseur(url, verif_liens=a.liens, crawl=a.crawl, verifier_ssl=not a.no_ssl_check,
                        schema_auto="://" not in a.url,
                        progress=lambda m, p=None: print(f"… {m}", file=sys.stderr)).run()
    except (ValueError, ErreurAnalyse) as e:
        sys.exit(str(e))
    print(rapport_texte(res))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        return cli(sys.argv[2:])
    if tk is None:
        sys.exit("tkinter est introuvable. Sous Linux : sudo apt install python3-tk\n"
                 "Vous pouvez utiliser le mode ligne de commande : python analyseur_pro.py --cli https://exemple.com")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()

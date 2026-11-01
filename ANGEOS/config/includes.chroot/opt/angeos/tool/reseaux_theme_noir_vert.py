# network_analyzer_wifi_fixed.py
import tkinter as tk
from tkinter import ttk, messagebox
import socket
import platform
import threading
import ipaddress
import subprocess
import re
import shutil
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import psutil
except ImportError:
    psutil = None

try:
    from scapy.all import ARP, Ether, srp, conf, get_if_addr
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False


COMMON_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 135, 139, 143,
    443, 445, 554, 631, 8080, 8443, 9100
]
PORT_TIMEOUT = 0.22
ARP_TIMEOUT = 2.5
MAX_WORKERS = 64
DISCOVERY_TIMEOUT = 12
MAX_SCAN_HOSTS = 4096
NEIGHBOR_TIMEOUT = 5
PING_TIMEOUT_MS = 500
TCP_PROBE_PORTS = (22, 53, 80, 135, 139, 443, 445, 554, 631, 8080, 8443, 9100)


# ============================================================
# THÈME NOIR ET VERT
# ============================================================
# Palette façon "terminal" : fond quasi noir, texte vert, accents verts
# vifs pour les éléments actifs/sélectionnés.
BG = "#0a0f0a"          # fond général (presque noir, teinté vert)
BG_PANEL = "#101a12"    # fond des zones de saisie / listes (légèrement plus clair)
FG = "#39ff6a"          # texte vert principal
FG_DIM = "#1f8a3a"      # texte vert atténué (sous-titres, statuts secondaires)
ACCENT = "#00ff41"      # vert vif : boutons actifs, sélection, bordures
BTN_BG = "#0f2a14"      # fond des boutons (vert très sombre)
BTN_ACTIVE_BG = "#173d1d"
SELECT_BG = "#164a22"   # sélection dans les listes/arbres


class NetworkAnalyzer:
    def __init__(self, root):
        self.root = root
        self.root.title("Network Analyzer")
        self.root.geometry("1000x650")
        self.root.minsize(850, 550)
        self.devices = []
        self.scanning = False
        self.stop_event = threading.Event()

        # Deux pools de threads persistants, réutilisés à chaque scan au
        # lieu d'en recréer un à chaque appel :
        #   - self.executor        : tâches "feuilles" (ping, port, DNS)
        #   - self.device_executor : orchestration par appareil
        # Les séparer évite qu'un pool attende après lui-même (un appareil
        # analysé dans device_executor soumet des tâches à executor sans
        # jamais s'auto-bloquer), et évite de créer un nouveau pool de
        # 16 threads par appareil (explosion du nombre de threads observée
        # avec beaucoup d'appareils détectés).
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.device_executor = ThreadPoolExecutor(max_workers=16)

        self.setup_style()
        self.main_menu()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        self.stop_event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.device_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def setup_style(self):
        self.root.configure(bg=BG)

        # --- Widgets tk "classiques" (Label, Button, Frame, Entry, Text,
        # LabelFrame) : un seul réglage ici s'applique à tous les écrans du
        # programme, puisque chaque écran est reconstruit après cet appel.
        self.root.option_add("*Background", BG)
        self.root.option_add("*Foreground", FG)
        self.root.option_add("*insertBackground", FG)          # curseur de saisie
        self.root.option_add("*highlightBackground", BG)       # bordure au repos
        self.root.option_add("*highlightColor", ACCENT)        # bordure au focus
        self.root.option_add("*selectBackground", SELECT_BG)   # texte sélectionné
        self.root.option_add("*selectForeground", FG)

        self.root.option_add("*Button.Background", BTN_BG)
        self.root.option_add("*Button.Foreground", FG)
        self.root.option_add("*Button.activeBackground", BTN_ACTIVE_BG)
        self.root.option_add("*Button.activeForeground", ACCENT)
        self.root.option_add("*Button.disabledForeground", FG_DIM)
        self.root.option_add("*Button.relief", "flat")
        self.root.option_add("*Button.borderWidth", 1)
        self.root.option_add("*Button.highlightThickness", 1)
        self.root.option_add("*Button.cursor", "hand2")

        self.root.option_add("*Entry.Background", BG_PANEL)
        self.root.option_add("*Entry.Foreground", FG)
        self.root.option_add("*Entry.relief", "flat")

        self.root.option_add("*Text.Background", BG_PANEL)
        self.root.option_add("*Text.Foreground", FG)
        self.root.option_add("*Text.relief", "flat")

        self.root.option_add("*Labelframe.Background", BG)
        self.root.option_add("*Labelframe.Foreground", ACCENT)
        self.root.option_add("*Label.Background", BG)

        # --- Widgets ttk (Treeview, Scrollbar) : la base d'options tk ne
        # les couvre pas, ils passent par un style dédié.
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Treeview", rowheight=32, font=("Arial", 10),
                         background=BG_PANEL, fieldbackground=BG_PANEL,
                         foreground=FG, borderwidth=0)
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"),
                         background=BTN_BG, foreground=ACCENT, relief="flat")
        style.map("Treeview",
                  background=[("selected", SELECT_BG)],
                  foreground=[("selected", ACCENT)])
        style.map("Treeview.Heading",
                  background=[("active", BTN_ACTIVE_BG)])

        style.configure("Vertical.TScrollbar", background=BTN_BG,
                         troughcolor=BG, arrowcolor=FG, borderwidth=0)
        style.configure("Horizontal.TScrollbar", background=BTN_BG,
                         troughcolor=BG, arrowcolor=FG, borderwidth=0)

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def ui(self, fn):
        self.root.after(0, fn)

    def run_bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ---------------- MENU ----------------

    def main_menu(self):
        self.clear_window()
        tk.Label(self.root, text="🛡️ NETWORK ANALYZER",
                 font=("Arial", 24, "bold")).pack(pady=25)
        tk.Label(self.root, text="Analyse de ton réseau local",
                 font=("Arial", 12)).pack(pady=(0, 25))

        buttons = [
            ("🔎 Appareils du réseau", self.network_devices),
            ("📶 Wi-Fi Analyzer", self.wifi_analyzer),
            ("📡 Ma connexion Wi-Fi", self.wifi_connection),
            ("🌐 Test de latence", self.latency_test),
            ("🔌 Analyse des ports", self.port_scanner),
            ("💻 Ma machine", self.machine_info),
            ("📡 Interfaces réseau", self.interfaces),
        ]
        for text, command in buttons:
            tk.Button(self.root, text=text, command=command,
                      font=("Arial", 13, "bold"), width=30, pady=10).pack(pady=7)

    # ---------------- NETWORK DETECTION ----------------

    def get_interfaces(self):
        """Retourne les interfaces IPv4 réellement utilisables, avec métriques."""
        if not psutil:
            return self._get_interfaces_fallback()
        result = []
        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            for name, addresses in addrs.items():
                st = stats.get(name)
                if st and not st.isup:
                    continue
                hw_mac = ""
                for h in addresses:
                    if getattr(h, "family", None) in (getattr(psutil, "AF_LINK", object()), getattr(socket, "AF_PACKET", object())):
                        hw_mac = self.normalize_mac(getattr(h, "address", ""))
                        if hw_mac:
                            break
                for a in addresses:
                    if a.family != socket.AF_INET or not a.address or not a.netmask:
                        continue
                    ip = a.address
                    if ip.startswith("127.") or ip.startswith("169.254."):
                        continue
                    try:
                        net = ipaddress.ip_network(f"{ip}/{a.netmask}", strict=False)
                    except ValueError:
                        continue
                    lname = name.lower()
                    virtual = any(x in lname for x in ("virtual", "vmware", "vbox", "hyper-v", "docker", "vpn", "tun", "tap", "zerotier", "hamachi"))
                    wifi = any(x in lname for x in ("wi-fi", "wifi", "wlan", "wireless"))
                    ethernet = any(x in lname for x in ("ethernet", "eth", "enp", "eno", "ens"))
                    result.append({"name": name, "ip": ip, "mask": a.netmask, "network": net, "mac": hw_mac,
                                   "up": bool(st.isup) if st else True,
                                   "speed": getattr(st, "speed", 0) if st else 0,
                                   "wifi": wifi, "ethernet": ethernet, "virtual": virtual})
        except Exception as e:
            print("Interfaces:", e)
        return result

    def _get_interfaces_fallback(self):
        """Repli utilisé quand psutil n'est pas installé : déduit l'IP
        locale via un socket UDP (aucune donnée n'est réellement envoyée)
        et suppose un masque /24, le cas le plus courant sur un réseau
        domestique. Moins précis que psutil (pas de nom d'interface, pas
        de MAC, pas de détection Wi-Fi/Ethernet), mais permet au scan de
        fonctionner au lieu de ne rien détecter du tout.
        """
        ip = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                ip = None

        if not ip or ip.startswith("127."):
            return []

        try:
            network = ipaddress.ip_network(f"{ip}/24", strict=False)
        except ValueError:
            return []

        return [{
            "name": "Interface principale (psutil non installé)",
            "ip": ip, "mask": "255.255.255.0", "network": network, "mac": "",
            "up": True, "speed": 0, "wifi": False, "ethernet": False,
            "virtual": False
        }]

    def get_local_interface(self):
        """Sélectionne l'interface de la route par défaut avant les interfaces virtuelles."""
        interfaces = self.get_interfaces()
        if not interfaces:
            return None

        if SCAPY_AVAILABLE:
            try:
                route = conf.route.route("8.8.8.8")
                if len(route) >= 2:
                    iface, src = route[0], route[1]
                    for item in interfaces:
                        if item["name"] == iface or item["ip"] == src:
                            return item
            except Exception:
                pass

        def score(x):
            return (
                0 if x["virtual"] else 1,
                1 if x["wifi"] else 0,
                1 if x["ethernet"] else 0,
                1 if x["up"] else 0,
                -abs(x["network"].prefixlen - 24),
                x["speed"] or 0
            )
        return max(interfaces, key=score)

    def get_default_route_info(self):
        """Return the interface, local IP and gateway used by the default route."""
        if SCAPY_AVAILABLE:
            try:
                route = conf.route.route("8.8.8.8")
                if len(route) >= 3:
                    iface, src, gateway = route[0], route[1], route[2]
                    return {"interface": iface, "ip": src, "gateway": gateway}
            except Exception:
                pass

        # Windows fallback: parse route print.
        if platform.system().lower() == "windows" and shutil.which("route"):
            try:
                cp = subprocess.run(
                    ["route", "print", "0.0.0.0"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="ignore", timeout=4
                )
                for line in cp.stdout.splitlines():
                    m = re.match(
                        r"\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\S+)\s+(\S+)\s+\d+",
                        line
                    )
                    if m:
                        return {"interface": m.group(2), "ip": "", "gateway": m.group(1)}
            except Exception:
                pass

        return {"interface": "", "ip": "", "gateway": ""}

    def network_diagnostics(self):
        """Build a human-readable snapshot used by the GUI and troubleshooting."""
        iface = self.get_local_interface()
        route = self.get_default_route_info()
        if not iface:
            return {
                "interface": "Inconnue", "ip": "Inconnue", "mask": "Inconnue",
                "network": "Inconnu", "gateway": route.get("gateway") or "Inconnue"
            }
        gateway = route.get("gateway") or "Inconnue"
        return {
            "interface": iface["name"],
            "ip": iface["ip"],
            "mask": iface["mask"],
            "network": str(iface["network"]),
            "gateway": gateway
        }

    @staticmethod
    def normalize_mac(mac):
        if not mac:
            return ""
        mac = mac.strip().lower().replace("-", ":")
        return mac if re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac) else ""

    def system_neighbor_scan(self):
        """
        Read the operating system's neighbor/ARP table.
        This is valuable on Wi-Fi because Windows/Linux may already know clients
        that do not answer ICMP or expose any TCP service.
        """
        system = platform.system().lower()
        commands = []
        if system == "windows":
            commands = [["arp", "-a"]]
        elif system == "linux":
            commands = [["ip", "neigh", "show"], ["arp", "-an"]]
        elif system == "darwin":
            commands = [["arp", "-an"]]

        for cmd in commands:
            if not shutil.which(cmd[0]):
                continue
            try:
                cp = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="ignore", timeout=5
                )
                devices = self.parse_neighbor_output(cp.stdout)
                if devices:
                    return devices
            except Exception:
                continue
        return []

    def parse_neighbor_output(self, text):
        """Parse la table ARP/voisins.

        Logique reprise telle quelle de wifi_analize.py : une seule regex
        combinée IP + MAC + type. Ce format (MAC avec tirets, 17 caractères)
        correspond à la sortie de `arp -a` sous Windows ; sur les autres OS
        cette fonction peut ne rien trouver, les autres méthodes de scan
        (ARP actif, ping, TCP) prennent alors le relais.
        """
        own = self.get_local_interface()
        own_ip = own["ip"] if own else ""

        raw_entries = re.findall(
            r"(\d+\.\d+\.\d+\.\d+)"
            r"\s+"
            r"([\da-fA-F-]{17})"
            r"\s+"
            r"(\w+)",
            text
        )

        devices = {}

        for ip, mac_raw, _device_type in raw_entries:
            if ip == own_ip or ip.startswith("127."):
                continue

            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue

            mac = self.normalize_mac(mac_raw)
            if not mac:
                continue

            devices[ip] = {
                "ip": ip, "mac": mac,
                "hostname": "Inconnu", "ports": [],
                "source": "Table ARP"
            }

        return list(devices.values())

    # ---------------- DEVICE DISCOVERY ----------------

    @staticmethod
    def get_hostname(ip):
        try:
            return socket.gethostbyaddr(ip)[0]
        except Exception:
            return "Inconnu"

    @staticmethod
    def check_port(ip, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(PORT_TIMEOUT)
        try:
            return s.connect_ex((ip, port)) == 0
        except Exception:
            return False
        finally:
            s.close()

    def scan_ports(self, ip):
        found = []
        futures = {self.executor.submit(self.check_port, ip, p): p for p in COMMON_PORTS}
        for f in as_completed(futures):
            try:
                if f.result():
                    found.append(futures[f])
            except Exception:
                pass
        return sorted(found)

    def arp_scan(self):
        """Scan ARP renforcé : interface explicite, paquets par blocs et plusieurs tentatives."""
        if not SCAPY_AVAILABLE:
            return []
        iface = self.get_local_interface()
        if not iface:
            return []
        net = iface["network"]
        own_ip = iface["ip"]
        if net.num_addresses > MAX_SCAN_HOSTS:
            return []

        devices = {}
        try:
            conf.iface = iface["name"]
            # Évite un paquet géant sur certains pilotes Wi-Fi : on découpe le /24.
            hosts = [str(ip) for ip in net.hosts() if str(ip) != own_ip]
            chunk_size = 64
            for pos in range(0, len(hosts), chunk_size):
                chunk = hosts[pos:pos + chunk_size]
                packet = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=chunk)
                answered = srp(
                    packet, iface=iface["name"], timeout=ARP_TIMEOUT,
                    retry=2, inter=0.02, verbose=False
                )[0]
                for _, received in answered:
                    ip = str(received.psrc)
                    mac = self.normalize_mac(received.hwsrc)
                    if mac and ip != own_ip:
                        devices[ip] = {"ip": ip, "mac": mac, "hostname": "Inconnu",
                                       "ports": [], "source": "ARP actif"}
        except Exception as e:
            print("ARP scan renforcé:", e)
        return list(devices.values())

    def ping_host(self, ip):
        """Retourne (actif, latence_ms) sans afficher de sortie système."""
        system = platform.system().lower()
        if system == "windows":
            cmd = ["ping", "-n", "1", "-w", str(PING_TIMEOUT_MS), ip]
        elif system == "darwin":
            # macOS (BSD ping) : -W est en MILLISECONDES, contrairement à
            # Linux où c'est en secondes. "-W 1" y ferait échouer le ping
            # quasi systématiquement (1 ms de délai).
            cmd = ["ping", "-c", "1", "-W", str(PING_TIMEOUT_MS), ip]
        else:
            # Linux (iputils) : -W est en secondes.
            cmd = ["ping", "-c", "1", "-W", "1", ip]
        started = time.perf_counter()
        try:
            ok = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=2).returncode == 0
            latency = round((time.perf_counter() - started) * 1000, 1) if ok else None
            return ok, latency
        except Exception:
            return False, None

    def tcp_probe_host(self, ip):
        """Retourne les ports TCP détectés rapidement sur un hôte local."""
        open_ports = []
        for port in TCP_PROBE_PORTS:
            if self.stop_event.is_set():
                break
            if self.check_port(ip, port):
                open_ports.append(port)
        return open_ports

    def discover_by_tcp(self, candidate_ips=None):
        """Détection TCP secondaire, avec conservation des ports trouvés."""
        iface = self.get_local_interface()
        if not iface or iface["network"].num_addresses > MAX_SCAN_HOSTS:
            return []
        net, own_ip = iface["network"], iface["ip"]
        if candidate_ips is None:
            candidate_ips = [str(ip) for ip in net.hosts() if str(ip) != own_ip]
        found = []
        futures = {self.executor.submit(self.tcp_probe_host, ip): ip for ip in candidate_ips}
        for future in as_completed(futures):
            if self.stop_event.is_set():
                break
            ip = futures[future]
            try:
                ports = future.result()
                if ports:
                    found.append({"ip": ip, "mac": "Non disponible", "hostname": "Inconnu",
                                  "ports": ports, "source": "TCP"})
            except Exception:
                pass
        return found

    def discover_by_ping(self, candidate_ips=None):
        """Découverte ICMP parallèle, avec mesure de latence."""
        iface = self.get_local_interface()
        if not iface or iface["network"].num_addresses > MAX_SCAN_HOSTS:
            return []
        net, own_ip = iface["network"], iface["ip"]
        if candidate_ips is None:
            candidate_ips = [str(ip) for ip in net.hosts() if str(ip) != own_ip]
        found = []
        futures = {self.executor.submit(self.ping_host, ip): ip for ip in candidate_ips}
        for future in as_completed(futures):
            if self.stop_event.is_set():
                break
            ip = futures[future]
            try:
                ok, latency = future.result()
                if ok:
                    found.append({"ip": ip, "mac": "Non disponible", "hostname": "Inconnu",
                                  "ports": [], "latency_ms": latency, "source": "Ping"})
            except Exception:
                pass
        return found

    def merge_devices(self, *device_lists):
        """Merge by IP, keeping the strongest information and discovery sources.

        Gère deux formes d'entrée : les appareils "bruts" (avec un champ
        "source" singulier) et les appareils déjà fusionnés une première
        fois (avec "sources", une liste) — scan_network() appelle cette
        fonction en plusieurs étapes, donc sans cette distinction le
        deuxième appel écrasait l'historique de détection accumulé lors
        du premier avec juste "Inconnu".
        """
        merged = {}

        for devices in device_lists:
            for device in devices:
                ip = device.get("ip")
                if not ip:
                    continue

                incoming_sources = device.get("sources")
                if incoming_sources is None:
                    incoming_sources = [device.get("source", "Inconnu")]

                if ip not in merged:
                    merged[ip] = dict(device)
                    merged[ip]["sources"] = list(incoming_sources)
                    merged[ip].pop("source", None)
                    continue

                current = merged[ip]
                mac = self.normalize_mac(device.get("mac", ""))
                current_mac = self.normalize_mac(current.get("mac", ""))

                if mac and not current_mac:
                    current["mac"] = mac
                if device.get("hostname") not in (None, "", "Inconnu"):
                    current["hostname"] = device["hostname"]
                ports = set(current.get("ports", [])) | set(device.get("ports", []))
                current["ports"] = sorted(ports)
                if current.get("latency_ms") is None and device.get("latency_ms") is not None:
                    current["latency_ms"] = device["latency_ms"]

                current.setdefault("sources", [])
                for src in incoming_sources:
                    if src not in current["sources"]:
                        current["sources"].append(src)

        return list(merged.values())

    def scan_network(self):
        """
        Robust local discovery:
        - active ARP request for IP+MAC
        - OS neighbor/ARP cache
        - ICMP ping
        - TCP probes as a final fallback
        """
        started = time.time()
        self.stop_event.clear()
        iface = self.get_local_interface()
        if not iface:
            return []

        net = iface["network"]
        if net.num_addresses > MAX_SCAN_HOSTS:
            print(f"Réseau trop grand pour le scan automatique: {net}")
            return []

        arp_devices = self.arp_scan()
        neighbor_devices = self.system_neighbor_scan()

        # Merge immediately so later stages only probe unresolved IPs.
        devices = self.merge_devices(arp_devices, neighbor_devices)
        known_ips = {d["ip"] for d in devices}
        hosts = [
            str(ip) for ip in net.hosts()
            if str(ip) != iface["ip"] and str(ip) not in known_ips
        ]

        # Ping et TCP peuvent être lancés ensemble sur les hôtes encore inconnus.
        # Le résultat est ensuite fusionné pour éviter les doublons.
        with ThreadPoolExecutor(max_workers=2) as discovery_pool:
            ping_future = discovery_pool.submit(self.discover_by_ping, hosts)
            tcp_future = discovery_pool.submit(self.discover_by_tcp, hosts)
            try:
                ping_devices = ping_future.result()
            except Exception:
                ping_devices = []
            try:
                tcp_devices = tcp_future.result()
            except Exception:
                tcp_devices = []
        devices = self.merge_devices(devices, ping_devices, tcp_devices)

        if not devices:
            self.devices = []
            print("Aucun appareil détecté.")
            return []

        # Reverse DNS is optional and parallel.
        unknown = [d for d in devices if d.get("hostname") in (None, "", "Inconnu")]
        futures = {self.executor.submit(self.get_hostname, d["ip"]): d for d in unknown}
        for future in as_completed(futures):
            device = futures[future]
            try:
                hostname = future.result()
                if hostname and hostname != "Inconnu":
                    device["hostname"] = hostname
            except Exception:
                pass

        for d in devices:
            d["source"] = ", ".join(d.pop("sources", []))
            d["type"] = self.detect_device_type(
                d.get("hostname", ""), d.get("ports", [])
            )

        devices.sort(key=lambda d: int(ipaddress.ip_address(d["ip"])))
        self.devices = devices

        print(
            f"Découverte terminée en {time.time()-started:.1f}s : "
            f"{len(devices)} appareil(s) | "
            f"interface={iface['name']} | réseau={net}"
        )
        return devices

    def analyze_device(self, device):
        device["ports"] = self.scan_ports(device["ip"])
        device["type"] = self.detect_device_type(
            device.get("hostname", ""), device["ports"]
        )
        return device

    def analyze_devices(self):
        devices = self.scan_network()
        futures = [self.device_executor.submit(self.analyze_device, d) for d in devices]
        results = []
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass
        results.sort(key=lambda d: int(ipaddress.ip_address(d["ip"])))
        self.devices = results
        return results

    def detect_device_type(self, hostname, ports):
        h = (hostname or "").lower()
        if any(x in h for x in ("iphone", "ipad", "android", "phone", "mobile", "galaxy")):
            return "📱 Téléphone / tablette"
        if any(x in h for x in ("printer", "print", "epson", "canon", "brother", "hp", "xerox")):
            return "🖨️ Imprimante"
        if 631 in ports or 9100 in ports:
            return "🖨️ Imprimante"
        if 53 in ports and (80 in ports or 443 in ports):
            return "📡 Routeur / passerelle"
        if 445 in ports or 135 in ports or 139 in ports:
            return "💻 PC Windows"
        if 22 in ports:
            return "🖥️ Ordinateur / serveur"
        if any(p in ports for p in (80, 443, 8080, 8443)):
            return "🌐 Appareil réseau / serveur web"
        return "❓ Appareil inconnu"

    # ---------------- REAL WI-FI SCAN ----------------

    def scan_wifi_networks(self):
        """
        Scan nearby Wi-Fi networks, not just clients on the LAN.
        Uses native OS utilities so it can work without monitor mode.
        """
        system = platform.system().lower()
        if system == "windows":
            return self.scan_wifi_windows()
        if system == "linux":
            return self.scan_wifi_linux()
        if system == "darwin":
            return self.scan_wifi_macos()
        return []

    def scan_wifi_windows(self):
        if not shutil.which("netsh"):
            return []
        try:
            cp = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=bssid"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=15
            )
            text = cp.stdout
            if not text:
                return []

            results = []
            current_ssid = None  # infos communes à tous les BSSID de ce SSID
            current_row = None   # la ligne (un par BSSID) en cours de remplissage

            for raw in text.splitlines():
                line = raw.strip()

                m = re.match(r"SSID\s+\d+\s*:\s*(.*)", line, re.I)
                if m:
                    current_ssid = {
                        "ssid": m.group(1).strip() or "(Réseau masqué)",
                        "security": "Inconnu",
                    }
                    current_row = None
                    continue

                if current_ssid is None:
                    continue

                m = re.match(r"(?:Authentification|Authentication)\s*:\s*(.*)", line, re.I)
                if m:
                    current_ssid["security"] = m.group(1).strip()
                    if current_row:
                        current_row["security"] = current_ssid["security"]
                    continue

                m = re.match(r"BSSID\s+\d+\s*:\s*(.*)", line, re.I)
                if m:
                    # Une ligne par BSSID : un même SSID peut avoir plusieurs
                    # points d'accès (répéteur, bi-bande 2,4/5 GHz...).
                    current_row = {
                        "ssid": current_ssid["ssid"],
                        "security": current_ssid["security"],
                        "signal": "Inconnu",
                        "channel": "Inconnu",
                        "bssid": m.group(1).strip(),
                    }
                    results.append(current_row)
                    continue

                if current_row is None:
                    continue

                m = re.match(r"(?:Signal|Signal strength|Signalstärke|Signál)\s*:\s*(.*)", line, re.I)
                if m:
                    current_row["signal"] = m.group(1).strip()
                    continue

                m = re.match(r"(?:Canal|Channel|Kanal)\s*:\s*(.*)", line, re.I)
                if m:
                    current_row["channel"] = m.group(1).strip()
                    continue

            return results
        except Exception as e:
            print("Wi-Fi Windows:", e)
            return []

    def scan_wifi_linux(self):
        if not shutil.which("nmcli"):
            return []
        try:
            cp = subprocess.run(
                ["nmcli", "-t", "-f",
                 "IN-USE,SSID,BSSID,CHAN,SIGNAL,SECURITY",
                 "device", "wifi", "list", "--rescan", "yes"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=20
            )
            rows = []
            for line in cp.stdout.splitlines():
                # split(":") naïf casserait le BSSID (qui contient lui-même
                # des ':'), décalant toutes les colonnes suivantes. nmcli -t
                # échappe les ':' internes avec '\', d'où ce découpage dédié.
                parts = self._split_nmcli_line(line)
                if len(parts) < 6:
                    continue
                _in_use, ssid, bssid, channel, signal, security = parts[:6]
                rows.append({
                    "ssid": ssid or "(Réseau masqué)",
                    "bssid": bssid,
                    "channel": channel,
                    "signal": f"{signal}%" if signal else "Inconnu",
                    "security": security or "Ouvert"
                })
            return rows
        except Exception as e:
            print("Wi-Fi Linux:", e)
            return []

    def scan_wifi_macos(self):
        airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        try:
            if not os.path.exists(airport):
                return []
            cp = subprocess.run(
                [airport, "-s"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=15
            )
            rows = []
            for line in cp.stdout.splitlines()[1:]:
                line = line.rstrip()
                # airport columns are whitespace aligned; SSID can contain spaces,
                # so parse from the right.
                m = re.match(
                    r"^(.*?)\s+([0-9a-fA-F:]{17})\s+(-?\d+)\s+(\d+)\s+(.+?)\s+([A-Z0-9]+)\s*$",
                    line
                )
                if m:
                    rows.append({
                        "ssid": m.group(1).strip() or "(Réseau masqué)",
                        "bssid": m.group(2),
                        "signal": m.group(3) + " dBm",
                        "channel": m.group(4),
                        "security": m.group(6)
                    })
            return rows
        except Exception as e:
            print("Wi-Fi macOS:", e)
            return []

    # ---------------- MA CONNEXION WI-FI + AUDIT ----------------
    # Fusion de wifi_analize.py : détails de la connexion active et
    # audit de sécurité, rendus multiplateformes (Windows/Linux/macOS)
    # au lieu de la version Windows uniquement d'origine.

    @staticmethod
    def _split_nmcli_line(line):
        """Découpe une ligne nmcli -t en respectant l'échappement des ':'
        (nécessaire car un BSSID contient lui-même des ':')."""
        parts = []
        current = ""
        escape = False
        for ch in line:
            if escape:
                current += ch
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == ":":
                parts.append(current)
                current = ""
            else:
                current += ch
        parts.append(current)
        return parts

    def current_wifi_connection_windows(self):
        if not shutil.which("netsh"):
            return None
        try:
            cp = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, encoding="utf-8",
                errors="ignore", timeout=10
            )
        except Exception:
            return None

        text = cp.stdout
        if not text:
            return None

        patterns = {
            "ssid": r"^\s*SSID\s*:\s*(.*)$",
            "bssid": r"^\s*BSSID\s*:\s*(.*)$",
            "signal": r"^\s*Signal\s*:\s*(.*)$",
            "channel": r"^\s*(?:Canal|Channel)\s*:\s*(.*)$",
            "security": r"^\s*(?:Authentification|Authentication)\s*:\s*(.*)$",
            "state": r"^\s*(?:État|State)\s*:\s*(.*)$",
        }

        info = {}
        for line in text.splitlines():
            for key, pattern in patterns.items():
                m = re.match(pattern, line, re.IGNORECASE)
                if m:
                    info[key] = m.group(1).strip()

        return info or None

    def current_wifi_connection_linux(self):
        if not shutil.which("nmcli"):
            return None
        try:
            cp = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid,bssid,chan,signal,security",
                 "dev", "wifi"],
                capture_output=True, text=True, encoding="utf-8",
                errors="ignore", timeout=15
            )
        except Exception:
            return None

        for raw in cp.stdout.splitlines():
            parts = self._split_nmcli_line(raw)
            if len(parts) < 6:
                continue
            active, ssid, bssid, chan, signal, security = parts[:6]
            if active.strip().lower() == "yes":
                return {
                    "ssid": ssid or "(Réseau masqué)",
                    "bssid": bssid,
                    "channel": chan,
                    "signal": f"{signal}%" if signal else "Inconnu",
                    "security": security or "Ouvert",
                    "state": "Connecté",
                }
        return None

    def current_wifi_connection_macos(self):
        airport = (
            "/System/Library/PrivateFrameworks/Apple80211.framework/"
            "Versions/Current/Resources/airport"
        )
        if not os.path.exists(airport):
            return None
        try:
            cp = subprocess.run(
                [airport, "-I"],
                capture_output=True, text=True, encoding="utf-8",
                errors="ignore", timeout=10
            )
        except Exception:
            return None

        text = cp.stdout
        if not text or "AirPort: Off" in text:
            return None

        info = {}
        for raw in text.splitlines():
            line = raw.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "ssid":
                info["ssid"] = value
            elif key == "bssid":
                info["bssid"] = value
            elif key == "channel":
                info["channel"] = value
            elif key == "agrctlrssi":
                info["signal"] = f"{value} dBm"
            elif key == "link auth":
                info["security"] = value

        if not info.get("ssid"):
            return None

        info.setdefault("state", "Connecté")
        return info

    def current_wifi_connection(self):
        system = platform.system().lower()
        if system == "windows":
            return self.current_wifi_connection_windows()
        if system == "linux":
            return self.current_wifi_connection_linux()
        if system == "darwin":
            return self.current_wifi_connection_macos()
        return None

    def security_audit_lines(self, info):
        auth = (info.get("security") or "").upper()
        lines = [
            f"Réseau            : {info.get('ssid', '?')}",
            f"Sécurité déclarée : {auth or 'Inconnue'}",
            "",
            "Analyse :",
        ]

        if "WPA3" in auth:
            lines.append("🟢 WPA3 détecté — protocole Wi-Fi moderne.")
        elif "WPA2" in auth:
            lines.append(
                "🟡 WPA2 détecté — encore largement utilisé, "
                "correct si le mot de passe est long."
            )
        elif "WEP" in auth:
            lines.append(
                "🔴 WEP détecté — protocole ancien et vulnérable, à éviter."
            )
        elif "WPA" in auth:
            lines.append(
                "🟠 Ancien WPA détecté — à migrer vers WPA2/WPA3 si possible."
            )
        elif "OPEN" in auth or "OUVERT" in auth or not auth:
            lines.append(
                "🔴 Réseau ouvert ou sécurité non détectée — "
                "trafic potentiellement non chiffré."
            )
        else:
            lines.append(f"⚪ Sécurité non reconnue : {auth}")

        lines += [
            "",
            "Recommandations :",
            "• Utiliser WPA3 si le routeur le permet.",
            "• Sinon utiliser WPA2-AES (éviter TKIP).",
            "• Choisir un mot de passe Wi-Fi long et unique.",
            "• Maintenir le firmware du routeur à jour.",
        ]
        return lines

    def wifi_connection(self):
        self.clear_window()
        tk.Label(
            self.root, text="📡 Ma connexion Wi-Fi",
            font=("Arial", 22, "bold")
        ).pack(pady=15)
        tk.Label(
            self.root,
            text="Détails de la connexion actuelle et audit de sécurité",
            font=("Arial", 11)
        ).pack(pady=(0, 10))

        result = tk.Text(self.root, height=20, width=80, font=("Consolas", 11))
        result.pack(padx=20, pady=10)

        def display(info):
            result.delete("1.0", tk.END)
            if not info:
                result.insert(
                    tk.END,
                    "❌ Aucune connexion Wi-Fi détectée (Wi-Fi désactivé, "
                    "câble Ethernet branché, ou outil système indisponible)."
                )
                button.config(state="normal")
                return

            result.insert(tk.END, f"SSID   : {info.get('ssid', 'Inconnu')}\n")
            result.insert(tk.END, f"BSSID  : {info.get('bssid', 'Inconnu')}\n")
            result.insert(tk.END, f"Signal : {info.get('signal', 'Inconnu')}\n")
            result.insert(tk.END, f"Canal  : {info.get('channel', 'Inconnu')}\n")
            result.insert(tk.END, f"État   : {info.get('state', 'Inconnu')}\n\n")

            for line in self.security_audit_lines(info):
                result.insert(tk.END, line + "\n")

            button.config(state="normal")

        def scan():
            button.config(state="disabled")
            result.delete("1.0", tk.END)
            result.insert(tk.END, "🔄 Récupération des informations...\n")

            def work():
                info = self.current_wifi_connection()
                self.ui(lambda: display(info))

            self.run_bg(work)

        buttons = tk.Frame(self.root)
        buttons.pack(pady=10)
        button = tk.Button(
            buttons, text="🔄 Actualiser", command=scan,
            font=("Arial", 12, "bold"), padx=20, pady=8
        )
        button.pack(side="left", padx=10)
        tk.Button(
            buttons, text="⬅ Retour", command=self.main_menu,
            padx=20, pady=8
        ).pack(side="left", padx=10)

        scan()

    # ---------------- TEST DE LATENCE ----------------
    # Réutilise self.ping_host (déjà multiplateforme) au lieu de reparser
    # la sortie brute de ping, ce qui évite de dupliquer la logique
    # Windows/Linux/macOS déjà présente ailleurs dans le script.

    def latency_test(self):
        self.clear_window()
        tk.Label(
            self.root, text="🌐 Test de latence",
            font=("Arial", 22, "bold")
        ).pack(pady=20)

        f = tk.Frame(self.root)
        f.pack(pady=10)
        tk.Label(f, text="Adresse à tester :", font=("Arial", 12)).pack(
            side="left", padx=5
        )
        entry = tk.Entry(f, width=25, font=("Arial", 12))
        entry.insert(0, "8.8.8.8")
        entry.pack(side="left", padx=5)

        result = tk.Text(self.root, height=18, width=80, font=("Consolas", 10))
        result.pack(padx=20, pady=15)

        def run():
            host = entry.get().strip() or "8.8.8.8"
            button.config(state="disabled")
            result.delete("1.0", tk.END)
            result.insert(tk.END, f"🔄 Test de {host}...\n\n")

            def work():
                attempts = 4
                latencies = []
                lines = []

                for i in range(attempts):
                    ok, latency = self.ping_host(host)
                    if ok and latency is not None:
                        latencies.append(latency)
                        lines.append(f"Essai {i + 1} : {latency} ms")
                    else:
                        lines.append(f"Essai {i + 1} : ❌ pas de réponse")

                def show():
                    result.delete("1.0", tk.END)
                    result.insert(tk.END, f"🔍 Test de {host} terminé.\n\n")
                    result.insert(tk.END, "\n".join(lines) + "\n\n")

                    if not latencies:
                        result.insert(tk.END, "❌ Aucune réponse reçue.")
                    else:
                        mini = min(latencies)
                        maxi = max(latencies)
                        moyenne = sum(latencies) / len(latencies)
                        perte = attempts - len(latencies)

                        result.insert(tk.END, f"Paquets perdus : {perte}/{attempts}\n")
                        result.insert(tk.END, f"Minimum : {mini} ms\n")
                        result.insert(tk.END, f"Moyenne : {moyenne:.1f} ms\n")
                        result.insert(tk.END, f"Maximum : {maxi} ms\n\n")

                        if moyenne < 30:
                            result.insert(tk.END, "🟢 Très bonne latence.")
                        elif moyenne < 70:
                            result.insert(tk.END, "🟡 Latence correcte.")
                        elif moyenne < 150:
                            result.insert(tk.END, "🟠 Latence élevée.")
                        else:
                            result.insert(tk.END, "🔴 Latence très élevée.")

                    button.config(state="normal")

                self.ui(show)

            self.run_bg(work)

        button = tk.Button(
            self.root, text="🔍 Tester", command=run,
            font=("Arial", 12, "bold"), padx=20, pady=8
        )
        button.pack(pady=5)
        tk.Button(
            self.root, text="⬅ Retour", command=self.main_menu,
            padx=20, pady=8
        ).pack(pady=10)

    # ---------------- APPAREILS ----------------

    def network_devices(self):
        self.clear_window()
        tk.Label(
            self.root, text="🔎 Appareils du réseau",
            font=("Arial", 22, "bold")
        ).pack(pady=(12, 5))

        diag = self.network_diagnostics()
        info_text = (
            f"Interface : {diag['interface']}   •   "
            f"IP : {diag['ip']}   •   "
            f"Réseau : {diag['network']}   •   "
            f"Passerelle : {diag['gateway']}"
        )
        info = tk.Label(self.root, text=info_text, font=("Arial", 9))
        info.pack(pady=(0, 8))

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=15, pady=5)

        columns = ("status", "ip", "mac", "hostname", "type", "source", "latency")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        headings = {
            "status": "Statut", "ip": "IP", "mac": "MAC",
            "hostname": "Nom", "type": "Type", "source": "Détection", "latency": "Latence"
        }
        widths = {
            "status": 105, "ip": 120, "mac": 150,
            "hostname": 180, "type": 175, "source": 190, "latency": 85
        }
        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor="center")

        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        status = tk.Label(
            self.root,
            text="Prêt. Plusieurs méthodes seront utilisées.",
            font=("Arial", 10)
        )
        status.pack(pady=4)

        def display(devices):
            for item in tree.get_children():
                tree.delete(item)

            for d in devices:
                tree.insert("", "end", values=(
                    "🟢 EN LIGNE",
                    d.get("ip", ""),
                    d.get("mac", "Non disponible"),
                    d.get("hostname", "Inconnu"),
                    d.get("type", "❓ Appareil inconnu"),
                    d.get("source", ""),
                    f"{d.get('latency_ms')} ms" if d.get("latency_ms") is not None else "—"
                ))

            status.config(
                text=f"✅ {len(devices)} appareil(s) détecté(s) — "
                     f"ARP + table système + ping + TCP"
            )
            button.config(state="normal")
            self.scanning = False

        def scan():
            if self.scanning:
                return
            self.scanning = True
            button.config(state="disabled")
            status.config(text="🔄 ARP → table système → ping → TCP…")

            def work():
                try:
                    devices = self.scan_network()

                    # Detailed port analysis only for devices discovered.
                    if devices:
                        status_text = f"🔍 Analyse des services de {len(devices)} appareil(s)…"
                        self.ui(lambda: status.config(text=status_text))
                        futures = {
                            self.device_executor.submit(self.analyze_device, d): d
                            for d in devices
                        }
                        for future in as_completed(futures):
                            try:
                                future.result()
                            except Exception:
                                pass
                        devices.sort(
                            key=lambda d: int(ipaddress.ip_address(d["ip"]))
                        )
                except Exception as e:
                    print("Scan réseau:", e)
                    devices = []

                self.ui(lambda: display(devices))

            self.run_bg(work)

        buttons = tk.Frame(self.root)
        buttons.pack(pady=8)
        button = tk.Button(
            buttons, text="🔍 Scanner le réseau",
            command=scan, font=("Arial", 12, "bold"),
            padx=18, pady=8
        )
        button.pack(side="left", padx=8)

        tk.Button(
            buttons, text="🛠 Diagnostic",
            command=lambda: messagebox.showinfo(
                "Diagnostic réseau",
                "\n".join(f"{k.capitalize()} : {v}" for k, v in diag.items()) +
                "\n\nSi le nombre d'appareils est inférieur à celui affiché "
                "dans le routeur, vérifie que l'isolation des clients/AP "
                "ou le réseau invité est désactivé."
            ),
            padx=15, pady=8
        ).pack(side="left", padx=8)

        tk.Button(
            buttons, text="⏹ Arrêter",
            command=lambda: self.stop_event.set(), padx=15, pady=8
        ).pack(side="left", padx=8)
        tk.Button(
            buttons, text="⬅ Retour",
            command=self.main_menu, padx=18, pady=8
        ).pack(side="left", padx=8)

    # ---------------- WIFI ANALYZER ----------------

    def wifi_analyzer(self):
        self.clear_window()
        tk.Label(self.root, text="📶 Wi-Fi Analyzer",
                 font=("Arial", 22, "bold")).pack(pady=15)
        tk.Label(self.root,
                 text="Réseaux Wi-Fi visibles à proximité",
                 font=("Arial", 11)).pack(pady=(0, 10))

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=20, pady=10)
        columns = ("ssid", "signal", "channel", "security", "bssid")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=15)
        headings = {
            "ssid": "SSID", "signal": "Signal",
            "channel": "Canal", "security": "Sécurité", "bssid": "BSSID"
        }
        widths = {"ssid": 250, "signal": 100, "channel": 90,
                  "security": 180, "bssid": 160}
        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        status = tk.Label(self.root, text="Prêt à analyser.", font=("Arial", 10))
        status.pack(pady=5)

        def display(rows):
            for item in tree.get_children():
                tree.delete(item)
            for r in rows:
                tree.insert("", "end", values=(
                    r.get("ssid", "Inconnu"),
                    r.get("signal", "Inconnu"),
                    r.get("channel", "Inconnu"),
                    r.get("security", "Inconnu"),
                    r.get("bssid", "Inconnu")
                ))
            if rows:
                status.config(text=f"✅ {len(rows)} point(s) d'accès détecté(s)")
            else:
                status.config(
                    text="⚠️ Aucun réseau trouvé. Vérifie que le Wi-Fi est activé."
                )
            button.config(state="normal")

        def scan():
            button.config(state="disabled")
            status.config(text="🔄 Recherche des réseaux Wi-Fi...")
            def work():
                rows = self.scan_wifi_networks()
                self.ui(lambda: display(rows))
            self.run_bg(work)

        buttons = tk.Frame(self.root)
        buttons.pack(pady=10)
        button = tk.Button(buttons, text="🔍 Scanner le Wi-Fi",
                            command=scan, font=("Arial", 12, "bold"),
                            padx=20, pady=8)
        button.pack(side="left", padx=10)
        tk.Button(buttons, text="⬅ Retour", command=self.main_menu,
                  padx=20, pady=8).pack(side="left", padx=10)

    # ---------------- PORT SCANNER ----------------

    def port_scanner(self):
        self.clear_window()
        tk.Label(self.root, text="🔌 Analyse des ports",
                 font=("Arial", 22, "bold")).pack(pady=20)

        f = tk.Frame(self.root); f.pack(pady=10)
        tk.Label(f, text="Adresse IP :", font=("Arial", 12)).pack(side="left", padx=5)
        entry = tk.Entry(f, width=25, font=("Arial", 12)); entry.pack(side="left", padx=5)
        result = tk.Text(self.root, height=18, width=80, font=("Consolas", 10))
        result.pack(padx=20, pady=15)

        def scan():
            ip = entry.get().strip()
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                messagebox.showwarning("Adresse IP invalide", "Entre une adresse IPv4 valide.")
                return
            button.config(state="disabled")
            result.delete("1.0", tk.END)
            result.insert(tk.END, f"🔍 Scan de {ip}...\n\n")
            def work():
                ports = self.scan_ports(ip)
                def show():
                    result.delete("1.0", tk.END)
                    result.insert(tk.END, f"🔍 Scan de {ip} terminé.\n\n")
                    if ports:
                        result.insert(tk.END, "Ports ouverts :\n\n")
                        for p in ports:
                            try:
                                service = socket.getservbyport(p, "tcp")
                            except Exception:
                                service = "inconnu"
                            result.insert(tk.END, f"🟢 TCP {p:<5} {service}\n")
                    else:
                        result.insert(tk.END, "Aucun port courant ouvert trouvé.")
                    button.config(state="normal")
                self.ui(show)
            self.run_bg(work)

        button = tk.Button(self.root, text="🔍 Scanner", command=scan,
                           font=("Arial", 12, "bold"), padx=20, pady=8)
        button.pack(pady=5)
        tk.Button(self.root, text="⬅ Retour", command=self.main_menu,
                  padx=20, pady=8).pack(pady=10)

    # ---------------- MACHINE ----------------

    def machine_info(self):
        self.clear_window()
        tk.Label(self.root, text="💻 Ma machine",
                 font=("Arial", 22, "bold")).pack(pady=20)
        info = tk.Text(self.root, height=20, width=80, font=("Consolas", 11))
        info.pack(padx=20, pady=10)
        hostname = socket.gethostname()
        try:
            local_ip = self.get_local_interface()["ip"]
        except Exception:
            local_ip = "Inconnu"
        info.insert(tk.END, f"Nom de la machine : {hostname}\n")
        info.insert(tk.END, f"Adresse IP : {local_ip}\n")
        info.insert(tk.END, f"Système : {platform.system()}\n")
        info.insert(tk.END, f"Version : {platform.release()}\n")
        info.insert(tk.END, f"Architecture : {platform.machine()}\n")
        info.insert(tk.END, f"Processeur : {platform.processor()}\n")
        if psutil:
            try:
                info.insert(tk.END, f"\nCPU : {psutil.cpu_percent(interval=0.2)} %\n")
                info.insert(tk.END, f"RAM : {psutil.virtual_memory().percent} %\n")
            except Exception:
                pass
        tk.Button(self.root, text="⬅ Retour", command=self.main_menu,
                  padx=20, pady=8).pack(pady=15)

    # ---------------- INTERFACES ----------------

    def interfaces(self):
        self.clear_window()
        tk.Label(self.root, text="📡 Interfaces réseau", font=("Arial", 22, "bold")).pack(pady=12)

        info = self.network_diagnostics()
        box = tk.LabelFrame(self.root, text="Route active", padx=12, pady=8)
        box.pack(fill="x", padx=20, pady=8)
        tk.Label(box, text=f"Interface utilisée : {info['interface']}   |   IP : {info['ip']}   |   Réseau : {info['network']}   |   Passerelle : {info['gateway']}", anchor="w", justify="left").pack(fill="x")

        columns = ("interface", "etat", "type", "ip", "mask", "network", "mac", "speed")
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=20, pady=8)
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        headers = {"interface":"Interface", "etat":"État", "type":"Type", "ip":"IPv4", "mask":"Masque", "network":"Réseau", "mac":"MAC", "speed":"Débit"}
        widths = {"interface":170,"etat":80,"type":95,"ip":135,"mask":120,"network":175,"mac":145,"speed":85}
        for c in columns:
            tree.heading(c, text=headers[c])
            tree.column(c, width=widths[c], anchor="center")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for i in self.get_interfaces():
            typ = "Wi-Fi" if i["wifi"] else ("Ethernet" if i["ethernet"] else ("Virtuelle" if i["virtual"] else "Autre"))
            speed = f"{i['speed']} Mb/s" if i["speed"] else "—"
            tree.insert("", "end", values=(i["name"], "ACTIVE" if i["up"] else "OFF", typ, i["ip"], i["mask"], str(i["network"]), i.get("mac") or "—", speed))

        status = tk.Label(self.root, text="Les interfaces virtuelles sont conservées mais ne sont pas prioritaires pour le scan.")
        status.pack(pady=5)
        buttons = tk.Frame(self.root); buttons.pack(pady=10)
        tk.Button(buttons, text="🔄 Actualiser", command=self.interfaces, padx=18, pady=7).pack(side="left", padx=6)
        tk.Button(buttons, text="⬅ Retour", command=self.main_menu, padx=18, pady=7).pack(side="left", padx=6)


if __name__ == "__main__":
    root = tk.Tk()
    app = NetworkAnalyzer(root)
    root.mainloop()
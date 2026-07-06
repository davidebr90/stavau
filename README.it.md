<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="logo/stavau_dark_transparent.png">
  <img src="logo/stavau_light_transparent.png" alt="stavau" width="480">
</picture>

**Privacy by proximity.**

*"Stavau"* — dialetto brindisino per **"sto andando (via)"**.
Dillo, e il tuo PC si blocca da solo.

[![CI](https://github.com/davidebr90/stavau/actions/workflows/ci.yml/badge.svg)](https://github.com/davidebr90/stavau/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#piattaforme-supportate)

🇬🇧 [Read it in English](README.md)

</div>

---

## Vision

**stavau blocca automaticamente la tua postazione quando ti allontani**, rilevando via Bluetooth Low Energy la prossimità di un dispositivo personale fidato (smartphone o smartwatch) — così dimenticarsi di premere `Win+L` non espone mai più i tuoi dati.

**Per chi è:** chi lavora su postazioni condivise o pubbliche — uffici open space, coworking, biblioteche, laboratori, postazioni multiutente — oltre a IT manager che vogliono una rete di sicurezza aggiuntiva e utenti attenti alla privacy.

## Perché

Allontanarsi da uno schermo sbloccato, anche per due minuti, espone email, documenti, sessioni attive (VPN, SSO, password manager) e tutto ciò che un passante può leggere, fotografare o digitare. I timeout di blocco schermo sono o troppo lunghi per proteggerti o troppo corti per essere usabili. **stavau sostituisce il timer con la presenza fisica**: il telefono è con te; quando lui (e tu) uscite dal raggio di sicurezza, lo schermo si blocca in pochi secondi.

## Come funziona

1. **Associ** il tuo telefono/orologio una sola volta (wizard guidato, bonding Bluetooth standard del sistema operativo).
2. stavau mantiene un collegamento a basso consumo con il dispositivo e **campiona l'RSSI** (potenza del segnale).
3. L'RSSI viene filtrato (media mobile) e convertito in **distanza stimata** tramite una calibrazione guidata che esegui una volta sola ("mettiti a 1 m… ora a 3 m…").
4. Quando la distanza stimata supera il **raggio di sicurezza (1–10 m)** per un **tempo di grazia configurabile** (default 10 s), stavau attiva il **blocco schermo nativo** del sistema operativo.
5. Al rientro sblocchi normalmente (password/PIN/biometria). Lo **sblocco automatico al rientro** è una feature *avanzata* **disattivata di default**, **solo Linux** e protetta da un riconoscimento esplicito del rischio — annulla soltanto un blocco fatto da stavau stesso, mai uno manuale (vedi [Sblocco automatico](#sblocco-automatico-avanzato-disattivato-di-default)).

stavau sceglie il canale di presenza giusto per ciascun dispositivo (advertisement BLE, Bluetooth Classic bonded, connessione GATT mantenuta o monitor BlueZ offloadato al controller) — vedi [Strategie di prossimità](#strategie-di-prossimità-funziona-con-ogni-dispositivo).

> ⚠️ **Nota di design sulla randomizzazione MAC.** I dispositivi iOS/Android moderni ruotano l'indirizzo MAC Bluetooth pubblicizzato ogni pochi minuti: stavau quindi **non** traccia gli indirizzi MAC degli advertisement, ma si affida al **bond** a livello di sistema operativo e campiona l'RSSI sul collegamento stabilito. È più affidabile e più rispettoso della privacy. Dettagli in [docs/threat-model.md](docs/threat-model.md).

## Funzionalità

- 🔒 **Auto-blocco all'allontanamento** — blocco nativo su **Windows, macOS e Linux**.
- 📡 **Quattro strategie di prossimità, scelte automaticamente per dispositivo** — advertisement BLE, Bluetooth Classic bonded, connessione GATT mantenuta e monitoraggio BlueZ offloadato al controller. Funziona con iPhone, telefoni Android, wearable e tag a basso consumo (vedi [Strategie di prossimità](#strategie-di-prossimità-funziona-con-ogni-dispositivo)).
- 📏 **Raggio di sicurezza configurabile** — da 1 a 10 metri, con calibrazione per ambiente.
- ⏱️ **Motore anti falsi positivi** — smoothing RSSI a media mobile + isteresi temporale + tempo minimo fuori raggio.
- 🛡️ **Guardrail anti-runaway** — un circuit breaker mette in pausa i blocchi dopo 3 blocchi ravvicinati, così un bug o un segnale instabile non possono mai chiuderti fuori dal tuo PC.
- 🔁 **Stato di blocco a loop chiuso** — observer per-OS dicono a stavau se lo schermo è davvero bloccato, così non emette mai blocchi ridondanti.
- 📶 **Rilevamento Bluetooth spento** — quando il Bluetooth è disattivato, l'interfaccia mostra **BLUETOOTH OFF** invece di un vago "nessun segnale".
- 🖼️ **App grafica + system tray** — `stavau gui` (PySide6): selezione dispositivo, slider raggio, monitor live, wizard di calibrazione e **icona nella taskbar/tray colorata per stato** che passa da blu → grigio → verde → giallo → arancione → rosso al variare della distanza.
- 🧙 **Wizard al primo avvio** — pairing del dispositivo e calibrazione RSSI→distanza, passo passo.
- 🔓 **Sblocco automatico al rientro opzionale** — avanzato, disattivato di default, solo Linux, fortemente protetto (vedi sotto).
- 📜 **Log eventi locale** — storico lock/unlock salvato solo sulla tua macchina.
- 🌓 **Dark/light mode**, interfaccia accessibile.
- 🌍 **i18n** — rileva automaticamente la lingua del sistema operativo, con fallback all'inglese; **italiano incluso**, e aggiungere una traduzione della community è solo un file JSON.
- 🕵️ **Zero telemetria** — nessuna chiamata di rete, nessun account, nessun cloud. Mai. (Verificabile: è AGPL.)

## Piattaforme supportate

| Piattaforma | Versione minima | Blocco | Feedback stato-blocco | Sblocco automatico |
|---|---|---|---|---|
| Windows | 10 (1809+) | `LockWorkStation()` (user32) | notifiche di sessione WTS | ❌ (nessuna API pubblica di unlock) |
| macOS | 10.15 Catalina | `CGSession -suspend` / `pmset displaysleepnow` | notifiche `com.apple.screenIsLocked` | ❌ (nessuna API pubblica di unlock) |
| Linux | BlueZ ≥ 5.55 | `loginctl lock-session` (systemd-logind), fallback per DE | logind `LockedHint` + segnali | ✅ `loginctl unlock-session` |

Il BLE è fornito da [Bleak](https://github.com/hbldh/bleak) (WinRT / CoreBluetooth / BlueZ dietro un'unica API) su ogni piattaforma.

**Trust device:** qualsiasi dispositivo Android o Apple (iPhone, Apple Watch, telefono/orologio Android) con supporto BLE. Nessuna companion app richiesta.

## Installazione

### Dai sorgenti (tutte le piattaforme)

```bash
git clone https://github.com/davidebr90/stavau.git
cd stavau
python -m venv .venv
# Windows: .venv\Scripts\activate    |    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
stavau --help
```

**Extra opzionali** (combinabili, es. `pip install -e ".[tray,gui]"`):

| Extra | Aggiunge |
|---|---|
| `tray` | icona nella system tray (`stavau tray`) — pystray + Pillow |
| `gui` | app grafica (`stavau gui`) — PySide6 |
| `macos` | notifiche stato-blocco su macOS — pyobjc |

### Bundle precompilati

Le release taggate pubblicano **bundle PyInstaller autosufficienti per Windows, Linux e macOS** (con checksum SHA-256) nella pagina [Releases](https://github.com/davidebr90/stavau/releases). Vedi [docs/install.md](docs/install.md) per download, verifica checksum e avvio automatico (collegamento in Esecuzione automatica / unit utente systemd / LaunchAgent macOS).

## Avvio rapido

Preferisci una finestra? Lancia **`stavau gui`** (richiede l'extra `gui`): racchiude tutto il resto con selezione dispositivo, slider raggio, monitor live e wizard di calibrazione, e si apre nella lingua del tuo sistema operativo. Oppure usa la CLI:

```bash
stavau setup      # wizard guidato: scegli il dispositivo, calibra le distanze
stavau run        # avvia il monitoraggio (--dry-run per loggare senza bloccare)
stavau status     # stato connessione, RSSI, distanza stimata, strategia
stavau log        # eventi lock/unlock recenti (--clear per svuotare, --export per JSONL)
stavau tray       # esegui con icona nella tray colorata per stato (richiede extra tray)
stavau pair       # associa (bonding) il trust device via lo stack Bluetooth dell'OS
```

Flag utili di `setup`:

- `--strategy {auto,adv_scan,classic_link,adv_monitor,gatt_link}` — forza un canale di prossimità (default `auto` lo rileva).
- `--radius <1..10>` — raggio di sicurezza in metri.
- `--pair` — associa il dispositivo durante il setup per un'identità stabile.
- `--enable-auto-unlock --i-understand-the-risk` — abilita lo sblocco automatico (solo Linux, solo dispositivo associato).

| Impostazione | Default | Range |
|---|---|---|
| `radius_m` — raggio di sicurezza | 3 | 1–10 m |
| `grace_seconds` — tempo fuori raggio prima del blocco | 10 | 3–60 s |
| `smoothing_window` — campioni media mobile RSSI | 8 | 3–30 |
| `language` — lingua interfaccia (`auto` = segue l'OS) | `auto` | qualsiasi catalogo |
| `auto_unlock` — sblocco al rientro (**avanzato, solo Linux**) | `false` | — |

## Modello di sicurezza — da leggere

stavau è un **livello di comodità (convenience layer)**, non un sistema di autenticazione.

- ✅ Rende innocuo il dimenticarsi di bloccare lo schermo.
- ❌ **Non** sostituisce password, PIN, biometria o cifratura del disco.
- ❌ Non deve **mai** essere l'unica difesa contro un attaccante determinato.

Limiti noti (documentati in [docs/threat-model.md](docs/threat-model.md)): attacchi relay/amplificazione BLE (rilevanti soprattutto con auto-unlock attivo — per questo è disattivato), rumore intrinseco dell'RSSI (target di precisione: ±1,5 m indoor), e politica **fail-safe**: se il collegamento cade, il Bluetooth si spegne o stavau va in crash, lo schermo **si blocca** (mai il contrario).

## Strategie di prossimità (funziona con ogni dispositivo)

Dispositivi diversi espongono la propria presenza su canali diversi, quindi stavau sceglie quello giusto per ciascuno (e puoi forzarlo):

| Strategia | Ideale per | Come rileva la presenza | Qualità del segnale |
|---|---|---|---|
| `adv_scan` | iPhone/iPad/Watch, beacon, wearable, tag a basso consumo | scansione advertisement BLE + RSSI | RSSI reale → distanza (tutti gli OS) |
| `classic_link` | Android idle, dispositivi Classic legacy | link Bluetooth Classic bonded | RSSI reale su **Linux** (`hcitool`); **solo reachability su Windows** (in-portata / fuori-portata, non distanza) |
| `adv_monitor` | dispositivi a basso/bassissimo consumo, setup attenti alla batteria | `AdvertisementMonitor1` di BlueZ, **offloadato al controller** (basso consumo) | in/fuori-portata su soglie RSSI — **solo Linux** |
| `gatt_link` | dispositivi connettibili che non fanno advertising utile | RSSI su **connessione GATT mantenuta** | RSSI di connessione reale su **macOS/Linux**; non supportato su Windows (nessuna API pubblica) |

`stavau setup` sonda il dispositivo e sceglie automaticamente. Puoi forzare con `--strategy`; per un Android idle che non fa advertising durante il setup, forza `classic_link`. Le strategie non disponibili fanno fallback ad `adv_scan` e lo segnalano. Matrice completa di capacità per-OS: [docs/device-compatibility.md](docs/device-compatibility.md) e [docs/os-native-apis.md](docs/os-native-apis.md).

## Sblocco automatico (avanzato, disattivato di default)

stavau può opzionalmente **sbloccare** lo schermo quando il trust device rientra — ma è la cosa più rischiosa che uno strumento di prossimità possa fare, quindi è volutamente difficile da attivare e limitato nello scopo. Devono valere **tutte** queste condizioni:

- **Disattivato di default** e abilitabile solo con riconoscimento esplicito: `stavau setup --enable-auto-unlock --i-understand-the-risk`.
- **Solo Linux.** Windows e macOS non espongono alcuna API pubblica per sbloccare una sessione senza credenziali (by design), quindi stavau lì **rifiuta** di sbloccare invece di memorizzare la tua password.
- **Solo dispositivo associato (bonded)** — un'identità advertisement senza pairing è troppo facile da spoofare.
- **Annulla solo il blocco di stavau.** Se premi `Win+L`, o uno screensaver o un altro strumento blocca lo schermo, stavau lo classifica come *estraneo* e **non** lo sblocca mai automaticamente.
- **Prossimità più stretta + permanenza.** Il dispositivo deve essere ben *dentro* il raggio (una frazione più stretta) in modo continuativo per un tempo di permanenza; **nessun segnale non sblocca mai** (assenza di prova non è presenza).

Il rischio residuo è un **attacco relay** BLE (far sembrare vicino un dispositivo lontano) — è esattamente ciò di cui avverte il riconoscimento. Vedi [docs/threat-model.md](docs/threat-model.md) (T9).

## Privacy

- Nessuna telemetria, nessuna analytics, nessun crash reporting di default. Eventuali diagnostiche future saranno opt-in, esplicite e documentate.
- I log eventi sono locali e cancellabili con `stavau log --clear`.
- AGPL-3.0 garantisce a te (e al tuo reparto IT) di poter ispezionare ogni riga di codice che osserva la tua presenza.

## Roadmap

| Versione | Contenuto | Stato |
|---|---|---|
| **v0.1 (MVP)** | Monitoraggio BLE + stima distanza RSSI + blocco schermo, CLI | ✅ fatto |
| **v0.2** | **Strategy engine** di prossimità (`adv_scan` / `classic_link` / `adv_monitor` / `gatt_link`), device intelligence, associazione pairing/senza-pairing, backend di blocco per **tutti e tre gli OS**, feedback stato-blocco a loop chiuso, guardrail anti-runaway, rilevamento Bluetooth spento, sblocco automatico sicuro (Linux) | ✅ in gran parte fatto |
| **v0.3** | **GUI** (`stavau gui`): selezione dispositivo, slider raggio, monitor live, wizard di calibrazione, i18n | ✅ rilasciata (MVP) |
| **v0.4** | System tray con icona colorata per stato, log eventi, dark mode, i18n (EN/IT) | ✅ rilasciata |
| **v1.0** | Hardening sicurezza, matrice test hardware multi-OS completa, docs, submission ad awesome-list | ⏳ |

**Guardrail di sicurezza rilasciato:** un circuit breaker anti-runaway mette in pausa i blocchi dopo 3 blocchi ravvicinati (configurabile), così un bug o un segnale instabile non possono mai chiuderti fuori dal tuo PC — vedi [docs/threat-model.md](docs/threat-model.md) (T10).

## Contribuire

Leggi [CONTRIBUTING.md](CONTRIBUTING.md) e [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Per le vulnerabilità segui [SECURITY.md](SECURITY.md) — niente issue pubbliche.

## Licenza

[AGPL-3.0](LICENSE): copyleft forte per garantire che ogni derivato di uno strumento privacy resti ispezionabile e libero, anche se esposto come servizio di rete.

---

<div align="center">
<sub>Fatto in Puglia 🇮🇹 · <em>Stavau. Il PC lo sa.</em></sub>
</div>

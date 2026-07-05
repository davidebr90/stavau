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
5. Al rientro sblocchi normalmente (password/PIN/biometria). Lo sblocco automatico al rientro è una feature *avanzata* pianificata, **disattivata di default**, con warning di sicurezza espliciti.

> ⚠️ **Nota di design sulla randomizzazione MAC.** I dispositivi iOS/Android moderni ruotano l'indirizzo MAC Bluetooth pubblicizzato ogni pochi minuti: stavau quindi **non** traccia gli indirizzi MAC degli advertisement, ma si affida al **bond** a livello di sistema operativo e campiona l'RSSI sul collegamento stabilito. È più affidabile e più rispettoso della privacy. Dettagli in [docs/threat-model.md](docs/threat-model.md).

## Funzionalità

- 🔒 **Auto-blocco all'allontanamento** — blocco nativo su Windows, macOS e Linux.
- 📏 **Raggio di sicurezza configurabile** — da 1 a 10 metri, con calibrazione per ambiente.
- ⏱️ **Motore anti falsi positivi** — smoothing RSSI a media mobile + isteresi temporale + tempo minimo fuori raggio.
- 🧙 **Wizard al primo avvio** — pairing del dispositivo e calibrazione RSSI→distanza, passo passo.
- 🖥️ **Icona nella system tray / barra menu** — stato connessione, RSSI corrente e distanza stimata a colpo d'occhio.
- 📜 **Log eventi locale** — storico lock/unlock salvato solo sulla tua macchina.
- 🌓 **Dark/light mode**, interfaccia accessibile.
- 🌍 **i18n** — prima inglese e italiano, traduzioni della community benvenute.
- 🕵️ **Zero telemetria** — nessuna chiamata di rete, nessun account, nessun cloud. Mai. (Verificabile: è AGPL.)

## Piattaforme supportate

| Piattaforma | Versione minima | Meccanismo di blocco | Backend BLE |
|---|---|---|---|
| Windows | 10 (1809+) | `LockWorkStation()` (user32) | WinRT via [Bleak](https://github.com/hbldh/bleak) |
| macOS | 10.15 Catalina | `SACLockScreenImmediate` / `pmset displaysleepnow` + richiesta password | CoreBluetooth via Bleak |
| Linux | BlueZ ≥ 5.55 | `loginctl lock-session` (systemd-logind), fallback per DE | BlueZ/D-Bus via Bleak |

**Trust device:** qualsiasi dispositivo Android o Apple (iPhone, Apple Watch, telefono/orologio Android) con supporto al bonding BLE. Nessuna companion app richiesta per la v1.x.

## Installazione

> stavau è in **pre-alpha**: non ci sono ancora release binarie. Vedi la [Roadmap](#roadmap).

```bash
git clone https://github.com/davidebr90/stavau.git
cd stavau
python -m venv .venv
# Windows: .venv\Scripts\activate    |    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
stavau --help
```

## Avvio rapido

```bash
stavau setup      # wizard guidato: scegli il dispositivo, bonding, calibrazione
stavau run        # avvia il monitoraggio (foreground; --daemon per background)
stavau status     # stato connessione, RSSI, distanza stimata
stavau log        # eventi lock/unlock recenti
```

| Impostazione | Default | Range |
|---|---|---|
| `radius_m` — raggio di sicurezza | 3 | 1–10 m |
| `grace_seconds` — tempo fuori raggio prima del blocco | 10 | 3–60 s |
| `smoothing_window` — campioni media mobile RSSI | 8 | 3–30 |
| `auto_unlock` — sblocco al rientro (**avanzato, sconsigliato**) | `false` | — |

## Modello di sicurezza — da leggere

stavau è un **livello di comodità (convenience layer)**, non un sistema di autenticazione.

- ✅ Rende innocuo il dimenticarsi di bloccare lo schermo.
- ❌ **Non** sostituisce password, PIN, biometria o cifratura del disco.
- ❌ Non deve **mai** essere l'unica difesa contro un attaccante determinato.

Limiti noti (documentati in [docs/threat-model.md](docs/threat-model.md)): attacchi relay/amplificazione BLE (rilevanti soprattutto con auto-unlock attivo — per questo è disattivato), rumore intrinseco dell'RSSI (target di precisione: ±1,5 m indoor), e politica **fail-safe**: se il collegamento cade, il Bluetooth si spegne o stavau va in crash, lo schermo **si blocca** (mai il contrario).

## Privacy

- Nessuna telemetria, nessuna analytics, nessun crash reporting di default. Eventuali diagnostiche future saranno opt-in, esplicite e documentate.
- I log eventi sono locali e cancellabili con `stavau log --clear`.
- AGPL-3.0 garantisce a te (e al tuo reparto IT) di poter ispezionare ogni riga di codice che osserva la tua presenza.

## Roadmap

| Versione | Contenuto | Stato |
|---|---|---|
| **v0.1 (MVP)** | Monitoraggio BLE + stima distanza RSSI + blocco schermo, **CLI** (target Linux; backend Windows arrivato in anticipo) | ✅ implementata — release dopo i test di accettazione |
| **v0.2** | **Strategy engine** di prossimità — device intelligence + associazione pairing/senza-pairing ✅ fatto; strategie GATT-link / Bluetooth Classic e backend macOS ⏳ (vedi [docs/device-compatibility.md](docs/device-compatibility.md)) | 🚧 in corso |
| **v0.3** | GUI: slider raggio, wizard di calibrazione | ⏳ |
| **v0.4** | System tray ✅ (preview: `stavau tray`), viewer log eventi, dark mode, i18n (EN/IT) | 🚧 |

**Guardrail di sicurezza (anticipato):** un circuit breaker anti-runaway mette in pausa i blocchi dopo 3 blocchi ravvicinati (configurabile), così un bug o un segnale instabile non possono mai chiuderti fuori dal tuo PC. Vedi [docs/threat-model.md](docs/threat-model.md) (T10).
| **v1.0** | Hardening sicurezza, matrice test multi-OS completa, docs, submission ad awesome-list | ⏳ |

## Contribuire

Leggi [CONTRIBUTING.md](CONTRIBUTING.md) e [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Per le vulnerabilità segui [SECURITY.md](SECURITY.md) — niente issue pubbliche.

## Licenza

[AGPL-3.0](LICENSE): copyleft forte per garantire che ogni derivato di uno strumento privacy resti ispezionabile e libero, anche se esposto come servizio di rete.

---

<div align="center">
<sub>Fatto in Puglia 🇮🇹 · <em>Stavau. Il PC lo sa.</em></sub>
</div>

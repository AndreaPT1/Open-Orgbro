# ORGBRO X3 Lab
Reverse engineering di interoperabilità per pilotare una stampante termica ORGBRO X3 senza passare dall'app Snap & Tag.

![ORGBRO X3 che stampa Open-Orgbro](assets/images/open-orgbro-printer.png)

## A cosa serve
Questo progetto serve a capire e documentare come parlare con la ORGBRO X3 da strumenti locali e script semplici, senza dipendere dal software proprietario del produttore. L'obiettivo pratico e' rendere la stampante utilizzabile in flussi piu' aperti, ripetibili e automatizzabili: test BLE, feed carta, replay di job catturati e generazione/stampa di testo raster da Python.

Il repository e' un laboratorio di reverse engineering applicato: contiene prove, catture, script diagnostici e un primo percorso funzionante per stampare testo localmente. Non e' ancora un SDK rifinito o un prodotto finale, ma una base tecnica utile per chi vuole studiare il protocollo, contribuire o costruire tool piu' user-friendly sopra questo lavoro.

## In una frase
Se vuoi provare subito qualcosa di utile: questo repo oggi sa rilevare la stampante, fare feed carta e stampare testo raster locale sulla ORGBRO X3 da Python.

## Stato del progetto
- progetto sperimentale e in evoluzione;
- alcune parti sono nate in modo molto pragmatico e iterativo, seguendo test reali piu' che un design formale a priori;
- il codice e la documentazione descrivono cio' che siamo riusciti a verificare, ma non garantiscono una spiegazione completa di ogni dettaglio interno;
- usalo come base tecnica e come diario di reverse engineering, non come prodotto supportato.

In breve: funziona, ma e' anche un repo "vibe coded" nel senso piu' onesto del termine. Abbiamo privilegiato velocita' di esplorazione, prove sul campo e documentazione dei risultati. Se apri issue o PR per chiarire, ripulire o consolidare il codice, sei nel posto giusto.

## Cosa funziona oggi
- scansione BLE e individuazione della X3;
- ispezione GATT della stampante;
- comando feed carta confermato;
- replay di alcuni job/catture;
- generazione e stampa di testo raster locale senza Snap & Tag;
- preview locale PNG senza Bluetooth per verificare il layout prima di stampare.

## Quick start
Prerequisiti:
- Python 3.11+ consigliato;
- macOS con Bluetooth disponibile;
- stampante ORGBRO X3 accesa;
- app Snap & Tag chiusa durante i test BLE.

Installazione:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Importante su macOS:
- lancia i comandi BLE da `Terminal.app`;
- non lanciare la stampa BLE da host che spawnano Python senza permessi Bluetooth/macOS TCC gia' a posto;
- se `Snap & Tag` e' aperta, chiudila prima dei test, cosi' non occupa la connessione.

Ordine consigliato:
1. genera una preview locale senza Bluetooth;
2. prova un feed carta;
3. stampa testo da `Terminal.app`.

Preview locale senza Bluetooth:
```bash
python3 scripts/q2_print_text.py "Hello world" --height-rows 120 --font-size 64 --preview /tmp/x3-preview.png --preview-only
```

Sanity check, solo feed carta:
```bash
python3 scripts/q2_feed.py --filter x3 --steps 24 --wait-after 2
```

Stampa testo da Terminal.app:
```bash
python3 scripts/q2_print_text.py "Hello world" --height-rows 120 --font-size 64 --feed-steps 160
```

Per una foto del repo:
```bash
python3 scripts/q2_print_text.py "Open-Orgbro" --height-rows 140 --font-size 72 --feed-steps 180
```

## Supporto e aspettative
Questo repository viene pubblicato per condividere il lavoro, non come servizio con SLA o supporto garantito. Possiamo non sapere spiegare subito ogni scelta o ogni byte del protocollo, soprattutto nelle parti nate da reverse engineering rapido e iterativo. Se qualcosa non e' chiaro, il posto migliore per migliorarlo e' una issue o una PR con contesto, test o catture aggiuntive.

## Privacy e file pubblicati
Gli identificativi specifici del dispositivo e alcuni dettagli ambientali presenti durante i test locali sono stati redatti nei file condivisi pubblicamente. Le catture di esempio pensate per il repository pubblico sono in `captures/public/`.

Nota pratica: il bundle locale `tools/X3Python.app` puo' restare utile sulla macchina di sviluppo, ma e' trattato come artefatto locale e non e' pensato per essere versionato o pubblicato.

## Troubleshooting rapido
- Se Python crasha con un errore TCC o con un riferimento a `NSBluetoothAlwaysUsageDescription`, non e' necessariamente un bug del repo: su macOS devi lanciare gli script BLE da `Terminal.app`.
- Se la stampante non risponde, verifica che sia accesa e che `Snap & Tag` sia chiusa.
- Se vuoi controllare il layout prima di usare il Bluetooth, usa `--preview-only`.

## Obiettivo
Costruire un tool locale, inizialmente CLI Python, che sappia:
- rilevare la stampante via Bluetooth Low Energy;
- identificare service e characteristic GATT;
- testare in modo controllato i protocolli candidati;
- stampare testo e immagini raster in bianco/nero.
## Dati verificati sulla nostra unità
- nome BLE visibile: `X3`;
- address BLE macOS osservato: `REDACTED-BLE-ADDRESS`;
- MAC nel manufacturer data / app: `REDACTED-MANUFACTURER-MAC`;
- service custom `0000ff00-0000-1000-8000-00805f9b34fb`;
- notify characteristic `0000ff01-0000-1000-8000-00805f9b34fb`;
- write characteristic `0000ff02-0000-1000-8000-00805f9b34fb`;
- notify characteristic aggiuntiva `0000ff03-0000-1000-8000-00805f9b34fb`.
## App Snap & Tag
- bundle: `/Applications/Snap & Tag.app/Wrapper/SnapTag.app`;
- executable: `/Applications/Snap & Tag.app/Wrapper/SnapTag.app/SnapTag`;
- bundle id: `com.snap-Tag.www`;
- versione/build osservata: `2.3.2` / `0417100302`;
- framework e simboli rilevanti: `YKPrinterKit`, `YZWManager`, `YKInstructTool`.
## Protocollo confermato
La X3 non usa ESC/POS puro per i comandi utili e i tentativi PrintMaster `51 78 ... ff` non hanno mosso carta. L'app usa frame YK/YZW/Q2 su `ff02`:
`64 <cmd> <seq> <len_lo> <len_hi> <payload...> 00 00 00 00 9b`
La lunghezza totale è `10 + payload_len`. `seq` è mascherato a 6 bit.
Comandi confermati:
- `0x80` payload `01`: token/init;
- `0x10` payload vuoto: status;
- `0x11` payload vuoto: firmware;
- `0x09` payload 1 byte: density;
- `0x0a` payload 1 byte: speed;
- `0x02` payload 2 byte little-endian: feed carta.
Probe firmware:
- write `6411030000000000009b`;
- response `64f1330200222dec5834129b`;
- payload `22 2d`, visualizzato dall'app come firmware `45.34`.
## Feed carta confermato
Il primo movimento fisico locale è stato ottenuto con BLE command `0x02` e payload `18 00` (24 step), preceduto dal token:
`python3 scripts/q2_frame_probe.py --filter x3 --sequence token,0x02:1800 --out captures/q2_frame_probe_token_feed02_24.json --wait-after 4`
Frame inviati:
- token: `648001010001000000009b`;
- feed 24 step: `64020202001800000000009b`.
Risposte/status osservati:
- `64ff21080076070a1009034643cb5934129b`;
- `64ff2408007207021009034643c25934129b`.
Comando riusabile:
`python3 scripts/q2_feed.py --filter x3 --steps 24 --out captures/q2_feed_24.json`
Equivalente con probe generico:
`python3 scripts/q2_frame_probe.py --filter x3 --sequence token,feed:24 --out captures/q2_feed_24.json`
## Comandi/protocolli scartati per ora
- ESC/POS `DLE EOT` e raster standard: accettati a livello GATT ma senza comportamento utile;
- PrintMaster `51 78 ... ff` feed/raster: nessun movimento confermato;
- candidate YK/Q2 `0x0c:00`, `0x50:a1`, `0x51`, `0x52:00`: nessun movimento confermato;
- `0x1f` zlib PrintMaster-like: nessuna stampa utile.
## Raster immagine
Static analysis indica che il percorso immagine passa da `0x101066be8`, che divide il raster in chunk e li wrappa con il frame YK/Q2. Il comando BLE per i chunk viene letto dal globale `0x10198c350`.
La funzione `0x101068798` imposta quel globale a:
- `0x05` quando il suo argomento è non-zero;
- `0x00` quando il suo argomento è zero.
Quindi `0x05` è il candidato principale per i chunk raster immagine.
Il builder `0x101066a70` converte l’immagine in raster 1-bit MSB-first: bit impostato = nero, larghezza riga `ceil(width_dots / 8)`. Per X3 il default app è 384 dot, quindi 48 byte per riga.
Test minimo candidato:
`python3 scripts/q2_raster_test.py --filter x3 --pattern center --rows 8 --post-feed 24 --out captures/q2_raster_center_8.json`
Se i chunk sono accettati ma non compare nero, provare anche il task-end osservato nel generator:
`python3 scripts/q2_raster_test.py --filter x3 --pattern black --rows 16 --chunk-rows 4 --task-end 0x51 --post-feed 64 --out captures/q2_raster_black_16_end51.json`
Altro test con start/setup osservato:
`python3 scripts/q2_raster_test.py --filter x3 --pattern black --rows 16 --chunk-rows 4 --start 0x50:a1 --task-end 0x51 --end 0x52:00 --post-feed 64 --out captures/q2_raster_black_16_start50_end51_52.json`
Esito attuale:
- i chunk `0x05` vengono accettati dalla stampante;
- gli ACK BLE arrivano e il feed finale muove carta;
- non compare però alcun nero visibile.

## Nuovi indizi dal path reale Q2
Analisi successiva del binario mostra che il percorso alto livello dell'app è:
- `printPhotoProcQ2:` -> `-[YKPrintManager universalPrintImage:params:]`
- `-[YKPrintManager universalPrintImage:params:]` -> `-[YKPrintManager universalPrintDataForImage:params:]`
- `universalPrintDataForImage:params:` costruisce il job tramite `YKInstructTool`.

Il path Q2 passa un oggetto `YKParamsConfig` con almeno questi campi:
- `printerTypeName`
- `speed`
- `density`
- `isFirst`
- `isLast`
- `isCut`
- `rightMarign`

Nel binario compaiono inoltre nomi di step/comandi che sembrano appartenere al preamble della stampa immagine:
- `pointsPerMM`
- `dotsNum`
- `pkgLength`
- `setSpeed`
- `setDensity`
- `setDevicePaperType`
- `specFeedPaper`
- `feedToMid`
- `feedWithParams`
- `feed`
- `ImgA01`
- `taskEnd`

Sono presenti anche stringhe di sequenziamento che suggeriscono logica di job multi-step, non solo chunk raster grezzi:
- `first&setSpeed`
- `first&setDensity`
- `first&setDevicePaperType`
- `first&specFeedPaper`
- `first&feedToMid`
- `nonFirst&feedWithParams`
- `nonFirst&feed`
- `nonFirst&feedToMid`
- `last&feedToMid`
- `last&taskEnd`

Questa traccia spiega bene perche' `0x05` da solo non basta: e' molto probabile che prima dei chunk immagine serva almeno una parte del setup di job/pagina/testina.
## Cattura PacketLogger: `Hello world`
Abbiamo catturato una stampa reale da Snap & Tag con PacketLogger e analizzato il file `.pklg`.

Fatti nuovi confermati:
- la stampa testo reale non usa `0x05` nel job osservato;
- dopo connessione, MTU e subscribe, l'app manda un probe firmware `0x11`;
- il job vero parte poi con:
  - `0x0a` payload `78`
  - `0x09` payload `0c`
  - una serie di frame `0x00`
  - feed finale `0x02` payload `c8 00`
- il job catturato contiene:
  - `22` gruppi di write BLE raw verso `ff02`;
  - `65` chunk raw totali;
  - `38` frame YK completi ricostruibili;
  - `35` frame di stampa `0x00`, quasi tutti da `432` byte di payload, piu' l'ultimo da `324`.

Distribuzione dei frame ricostruiti:
- `seq 15`: `0x0a:78`
- `seq 16`: `0x09:0c`
- `seq 17..51`: `0x00` con payload raster/chunk da `432` byte (`seq 51` da `324`)
- `seq 52`: `0x02:c800`

Osservazione importante:
- i frame `0x00` non sono tutti pieni di nero; le righe non vuote sono concentrate circa da `seq 30` a `seq 41`, coerenti con la zona del testo `Hello world` nel layout.

Script utile aggiunto:
- estrazione / replay raw da PacketLogger:
  `python3 scripts/q2_replay_pklg.py --summary-only /percorso/al/file.pklg`
- replay del job catturato:
  `python3 scripts/q2_replay_pklg.py --replay /percorso/al/file.pklg`

Questo e' il punto piu' forte raggiunto finora: adesso abbiamo un percorso per ristampare un job reale catturato dall'app anche prima di aver capito semanticamente tutti i comandi del protocollo.

## Generatore testo locale funzionante
Abbiamo creato `scripts/q2_print_text.py`, che genera raster 1-bit con Pillow e stampa senza passare da Snap & Tag.

Scoperte confermate dal primo successo:
- `0x00` e' il comando raster giusto per il path testo/Q2 osservato;
- il payload raster usa righe da `432` dot (`54` byte) e chunk logici da `8` righe (`432` byte);
- il BLE write deve essere spezzato in chunk raw da circa `240` byte, come faceva Snap & Tag nella cattura PacketLogger;
- il lancio da `Codex -> Python` crasha per TCC/Bluetooth su macOS, mentre da `Terminal.app -> Python` funziona e mostra/usa il permesso Bluetooth correttamente.

Comando funzionante:
`python3 scripts/q2_print_text.py "Hello world" --width-dots 432 --height-rows 180 --x 48 --y 48 --font-size 48 --feed-steps 200 --raw-chunk-size 240`

Nota del primo test:
- la stampa e' uscita corretta ma probabilmente duplicata perche' un tentativo precedente era rimasto in coda/buffer o perche' avevamo piu' tab Terminal aperti con job vicini;
- per test puliti, chiudere i tab Terminal vecchi e lanciare un solo job alla volta da Terminal.

## Stato finale raggiunto: `Hello world` centrato
Successo confermato: la X3 stampa testo generato localmente da Python, senza Snap & Tag.

Il punto chiave corretto dopo i primi test:
- la larghezza logica corretta del raster non e' `432` dot;
- la stampante interpreta i frame `0x00` come raster largo `864` dot;
- ogni frame raster resta da `432` byte, quindi:
  - `864` dot = `108` byte per riga;
  - `432` byte per frame = `4` righe per frame;
- usando `432` dot di larghezza, due righe consecutive venivano interpretate come due mezze righe affiancate, producendo due `Hello world` uno accanto all'altro.

Parametri ora funzionanti in `scripts/q2_print_text.py`:
- `--width-dots 864` default;
- `--rows-per-chunk 4` default;
- `--raw-chunk-size 240` default;
- `--align center` default;
- `--valign middle` default;
- `--x` / `--y` sono override manuali opzionali;
- il centramento e' calcolato misurando il bounding box reale del testo con Pillow.

Comando finale buono, da lanciare da Terminal:
`python3 scripts/q2_print_text.py "Hello world" --height-rows 120 --font-size 64 --feed-steps 160 --raw-chunk-size 240`

Per stampare altro testo:
`python3 scripts/q2_print_text.py "Testo qui" --height-rows 120 --font-size 64 --feed-steps 160`

Importante su macOS / TCC:
- lanciare gli script BLE da `Terminal.app`, non dal processo Codex;
- `Codex -> Python -> CoreBluetooth` puo' crashare con TCC:
  `NSBluetoothAlwaysUsageDescription`;
- `Terminal.app -> Python` funziona e ha gia' permesso di parlare con la X3;
- se compare il popup Bluetooth, premere `Allow`.

Sequenza pratica per ripartire in una nuova sessione:
1. accendere la X3;
2. chiudere Snap & Tag, cosi' non occupa la connessione BLE;
3. aprire Terminal;
4. eseguire:
   `python3 scripts/q2_print_text.py "Hello world" --height-rows 120 --font-size 64 --feed-steps 160`
5. se il testo e' troppo grande/piccolo, cambiare `--font-size`;
6. se serve piu' o meno carta dopo la stampa, cambiare `--feed-steps`.

Comando di sanity check, solo feed carta:
`python3 scripts/q2_feed.py --filter x3 --steps 24 --wait-after 2`

Comando per preview locale senza Bluetooth:
`python3 scripts/q2_print_text.py "Hello world" --height-rows 120 --font-size 64 --preview /tmp/x3-preview.png --preview-only`

## Prossimo passo
Mosse sensate da qui:
- stabilizzare `scripts/q2_print_text.py` come entrypoint principale;
- calibrare margine, dimensione font, altezza pagina e feed finale;
- mappare semanticamente i byte reali dei comandi di setup (`setSpeed`, `setDensity`, `pointsPerMM`, `dotsNum`, `pkgLength`, `ImgA01`, `taskEnd`) dentro `YKInstructTool`;
- verificare se `0x00` e `0x05` sono due modalita' di trasporto raster diverse oppure se `0x05` appartiene a un altro path del firmware.

AUDIO CHIP DECODER 0.4.2
========================

Reads a memory chip, finds the audio stored on it, and exports audio files.
One program: it reads the chip, works out how the sound is stored, shows
its best reading already applied, splits it into its separate sounds, lets
you confirm the speed by ear, and writes the files with a report.


INSTALL
-------
Easiest: download AudioChipDecoder-Setup-x.y.z.exe from the Releases page
of the project on GitHub and run it. One file, no Python needed, installs
for your user only. Windows SmartScreen may warn because the installer is
not code-signed: choose "More info", then "Run anyway".

From this folder instead:
1. Unzip the whole download into a folder.
2. Double-click  Install.bat
   - If Windows shows a security prompt, choose "More info" then
     "Run anyway", or "Run".
   - No administrator rights needed. Installs for your user only, into
     %LOCALAPPDATA%\AudioChipDecoder
   - Needs internet the first time: it fetches Python if you do not have
     it, plus the libraries the app uses. About 160 MB.
3. Start "Audio Chip Decoder" from the desktop or the Start menu.

Remove it with  Uninstall.bat .  Build-Exe.bat (after installing) makes a
standalone folder, dist\AudioChipDecoder, for a PC with no internet.

Once a day the app asks GitHub whether a newer release exists and shows a
button if so. Only version numbers are compared; nothing else is sent.


READ A CHIP
-----------
Press "Read a chip" and pick your reader in "Read with":

XGecu T48, TL866II Plus, TL866A/CS  (the usual reader for old sound chips)
   Reads 24/28/32/40-pin parallel EPROMs and flash (27C, 28F, 29F, 39SF...)
   and serial chips. Type what is printed on the chip, for example 27C256;
   the list narrows to matching types out of about 18,000. Pick the package
   (DIP, SOIC, PLCC, TSOP) to see only that shape; SOIC, PLCC and TSOP
   chips sit in an adapter board in the socket. Press Read chip.
   - The programmer's USB driver must be on the PC. It comes with XGecu's
     Xgpro setup and only needs installing once. Xgpro itself is not used.
   - No ID request is made unless you tick the box: on EPROMs that request
     puts 12 V on a pin, and reading never needs it.
   - On a TL866II Plus the pins are tested for contact before reading.

CH341A programmer  (8-pin serial chips)
   If its driver is missing, "Install the driver" downloads WCH's own
   driver and starts it; press INSTALL in WCH's window, replug. Once per PC.
   For chips in SOIC-8 (with the clip) or DIP-8 (in the socket). SOIC-8 is
   the shape of the body; the part number printed on it says what is inside.
   "Detect chip" identifies 25-series flash and AT45DB DataFlash. 24-series
   and 25/95-series EEPROMs carry no ID: type what is printed on the chip
   (24C256, 25LC512, M95160...) and the type and size are set for you.
   If the programmer has the generic WinUSB driver instead (installed by
   Zadig for other tools), the app uses that: slower, SPI chips only.

CH347 adapter  (8-pin serial chips, faster)
   Same driver and same steps as the CH341A. Its mode jumper must select
   SPI/I2C.

FT232H / FT2232H / FT4232H board  (SPI chips, wired by hand)
   Port A: AD0 clock, AD1 to the chip, AD2 from the chip, AD3 chip select.
   Uses FTDI's driver, which Windows normally installs by itself.

Raspberry Pi Pico or Arduino running serprog firmware  (SPI chips)
   Pick the board's COM port. pico-serprog and frser-duino are the usual
   firmwares.

Every reader reads twice and compares, reads a third time if they differ,
and refuses to save a blank or unreliable read. For a sound spread over
several chips, keep the same Track name and read them in order. Then press
"Decode what was read".

Memory cards: choose "a memory card or USB drive" on the same screen. This
reads the card's raw contents through a USB card reader. Windows requires
administrator rights for that; the screen has a button to restart the app
with them.

The app only ever sends read commands. It cannot erase or write a chip.


DECODE AND EXPORT
-----------------
- The headline states the encoding and speed in force, with a confidence
  level for the encoding. Press Play.
- "Sounds in the selection" lists the separate sounds on the chip. Chips
  with their own index (NEC uPD7759, OKI MSM6295, LPC speech) are split
  exactly; others at silent gaps. Double-click a sound to play it.
- "Set speed by ear": the speed is not stored on most chips, so the app
  estimates it and you set it. Press Play and drag the slider; the sound
  keeps playing while the speed changes. Every standard speed (8,000 up to
  48,000 Hz, including 24,000) is one click away, -1% / +1% make fine
  steps, and an exact speed can be typed. If you know how long the sound
  should last, typing that length sets the speed exactly. With several
  boards open, a speed can be set for one track only. "Change speed
  without a gap" keeps the sound unbroken while you drag; untick it to use
  Windows' basic player instead (a short gap at each change). Formats that
  state their own speed (uPD7759, LPC speech) need no check.
- "About this file" (or track) under the headline lists the facts: size,
  the chip types that size points to, sound data and filler, length,
  storage rate, loudest peak, average level, frequency range, fingerprint.
  Hover a line for what it means; Copy puts them on the clipboard.
- Sounds wrong? Help in the app lists fixes by symptom.
- "Export audio" writes WAV, FLAC or MP3: one file per track, per chip
  and, if ticked, per separate sound, with a Report.txt and manifest.json
  recording every setting and a fingerprint of every file. Optional
  clean-up (offset and rumble, mains hum, clicks, level) is off unless you
  tick it; the plain export is the chip's exact output.
- "Details" shows why the reading was chosen, every candidate and its
  score, fingerprints, padding, the decode formula with a worked example,
  a hex view and a session log.
- "Device profiles" saves a confirmed encoding and speed under a name.

You can also open dump files you already have (.bin, Intel HEX, Motorola
S-record), drag them onto the window, or reopen them from "Recent".

Command line:  python -m audiochip convert <folder> -o <out>
               [--format wav|flac|mp3] [--sounds] [--clean offset,level,...]


WHAT IT COVERS
--------------
Reads directly:  Through an XGecu T48, TL866II Plus or TL866A/CS: parallel
                   EPROM, EEPROM and flash, and serial chips, from the
                   programmer's own list of about 18,000 memory types.
                 Through a CH341A or CH347: 25-series SPI flash and AT45DB
                   DataFlash (identified automatically), 24-series I2C
                   EEPROM, 25/95-series SPI EEPROM.
                 Through an FT232H board or a serprog board: the SPI chips
                   above (no I2C).
                 Memory cards and USB drives, first 16, 64 or 256 MB.
Decodes:         8-bit unsigned, signed, sign-and-magnitude
                 8-bit mu-law and A-law, standard and inverted storage
                 16-bit signed and unsigned, either byte order
                 4-bit ADPCM: OKI, IMA and Yamaha, either nibble order
                 1-bit CVSD delta modulation (HC55516, MC3417), either
                   bit order
                 NEC uPD7759 voice ROMs (index, blocks and speed)
                 OKI MSM6295 voice ROMs (phrase table)
                 TI LPC speech: TMS5220/5200, TMS5110, TMC0281/TMS5100
                 The 8-bit ones also with reversed bit order or swapped
                 nibbles; any plain format as two interleaved channels.
                 WAV and MP3 files stored whole inside a dump or card.

NOT COVERED
-----------
- Other programmer brands: GQ-4X4, RT809H/F, TOP, Willem, Dataman, Batronix,
  Xeltek and similar. Their makers publish no way for other software to
  drive them, so the app cannot read through them. Dumps saved from their
  own software open and decode here.
- XGecu T56 and T76: they need algorithm files this build does not have.
- 93-series Microwire EEPROMs through the 8-pin readers. They hold at most
  2 KB, a quarter of a second of sound, and need a rewired socket. An XGecu
  programmer reads them.
- 24-series I2C chips through an FT232H or serprog board.
- Sound formats not listed above, for example Sega/Namco custom chips,
  ADPCM kinds other than the four listed, and MPEG audio chips. These show
  as "not recognised".
- LPC speech is found by searching for phrases. In a ROM that mixes speech
  with program code, a few short false phrases can appear in the list.


HOW FAR IT HAS BEEN TESTED
--------------------------
On Windows, automatically, for every version (GitHub's Windows build
machine): all 124 automated checks pass; the bundled XGecu helper starts
and answers "No programmer found"; the standalone program is built, opens
its window, decodes a test signal, writes FLAC and MP3, and loads its USB
and serial-port libraries; the installer is built from it.

NOT tested with real hardware by the developer: reading through any
programmer. Each reader (CH341A with WCH's driver, CH341A with WinUSB,
CH347, FT232H, serprog boards, XGecu) was written from the published
description of its commands and tested against a simulation of that
device. The first read with a real programmer is the real test. Running
the installer, sound playback, the driver install and card reading have
also not been exercised by the developer on a Windows desktop.

Decoders:
- 8-bit, mu-law and 16-bit: checked on real EPROM dumps.
- OKI, IMA and Yamaha ADPCM, CVSD, uPD7759 and MSM6295: checked on test
  data made with matching encoders, not on dumps of real chips.
- LPC speech: the phrase finder was checked on 971 real TI vocabulary
  words (over 99% found) and on random data. The synthesiser follows the
  documented chip behaviour; its output has been measured, not listened to.
  TMS5110 and TMC0281 data look the same, so try both if one sounds off.

If something fails: the installer names the step that stopped; the Read
screen says what went wrong; and errors inside the app are written to
%APPDATA%\AudioChipDecoder\error.log


LICENCES
--------
See THIRD-PARTY.txt. In short: minipro (the XGecu helper program) is GNU
GPL version 3 or later and runs as a separate program; its source is in
tools\minipro-build. The sound-chip tables come from the MAME project
(BSD 3-Clause). The other libraries are BSD, LGPL or Apache licensed.

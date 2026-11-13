AUDIO CHIP DECODER 0.3.2
========================

Reads a memory chip, finds the audio stored on it, and exports WAV files.
One program: it reads the chip, works out how the sound is stored, shows
its best reading already applied, lets you confirm the speed by ear, and
writes the WAV files with a report.


INSTALL
-------
1. Unzip the whole download into a folder.
2. Double-click  Install.bat
   - If Windows shows a security prompt, choose "More info" then
     "Run anyway", or "Run".
   - No administrator rights needed. Installs for your user only, into
     %LOCALAPPDATA%\AudioChipDecoder
   - Needs internet the first time: it fetches Python if you do not have
     it, plus NumPy and Qt. About 150 MB.
3. Start "Audio Chip Decoder" from the desktop or the Start menu.

Remove it with  Uninstall.bat .  Build-Exe.bat (after installing) makes a
standalone folder, dist\AudioChipDecoder, for a PC with no internet.


READ A CHIP
-----------
Press "Read a chip" and pick your reader in "Read with":

XGecu T48, TL866II Plus, TL866A/CS  (the usual reader for old sound chips)
   Reads 24/28/32/40-pin parallel EPROMs and flash (27C, 28F, 29F, 39SF...)
   and serial chips. Type what is printed on the chip, for example 27C256;
   the list narrows to matching types out of about 18,000. Press Read chip.
   - The programmer's USB driver must be on the PC. It comes with XGecu's
     Xgpro setup and only needs installing once. Xgpro itself is not used.
   - No ID request is made unless you tick the box: on EPROMs that request
     puts 12 V on a pin, and reading never needs it.
   - On a TL866II Plus the pins are tested for contact before reading.

CH341A programmer  (8-pin serial chips)
   If its driver is missing, "Install the driver" downloads WCH's own
   driver and starts it; press INSTALL in WCH's window, replug. Once per PC.
   "Detect chip" identifies 25-series flash. 24-series and 25/95-series
   EEPROMs carry no ID: pick the type printed on the chip.

Either way it reads twice and compares, reads a third time if they differ,
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
- "Check speed by ear": the speed is never stored on a chip, so the app
  estimates it and you confirm. Pick the option that sounds natural.
- Sounds wrong? Help in the app lists fixes by symptom.
- "Export WAV" writes one WAV per track and per chip, a Report.txt and a
  manifest.json with every setting and a fingerprint of every file.
- "Details" shows why the reading was chosen, every candidate and its
  score, fingerprints, padding, the decode formula with a worked example,
  a hex view and a session log.
- "Device profiles" saves a confirmed encoding and speed under a name.

You can also open dump files you already have (.bin, Intel HEX, Motorola
S-record), or drag them onto the window.


WHAT IT COVERS
--------------
Reads directly:  Through an XGecu T48, TL866II Plus or TL866A/CS: parallel
                   EPROM, EEPROM and flash, and serial chips, from the
                   programmer's own list of about 18,000 memory types.
                 Through a CH341A: 25-series SPI flash (identified
                   automatically), 24-series I2C EEPROM, 25/95-series SPI
                   EEPROM.
                 Memory cards and USB drives, first 16, 64 or 256 MB.
Decodes:         8-bit unsigned, signed, sign-and-magnitude
                 8-bit mu-law and A-law, standard and inverted storage
                 16-bit signed and unsigned, either byte order
                 4-bit OKI ADPCM, either nibble order
                 The 8-bit ones also with reversed bit order or swapped
                 nibbles; any of them as two interleaved channels.
                 WAV and MP3 files stored whole inside a dump or card.

NOT COVERED YET
---------------
- Other programmer brands: GQ-4X4, RT809H/F, TOP, Willem, Dataman, Batronix,
  Xeltek and similar. Their makers publish no way for other software to
  drive them, so the app cannot read through them. Dumps saved from their
  own software open and decode here.
- XGecu T56 and T76: they need algorithm files this build does not have.
- Through a CH341A: 93-series Microwire EEPROMs and AT45 DataFlash. An
  XGecu programmer reads both.
- Other compressed formats: other ADPCM families, delta modulation, LPC
  speech. These show as "not recognised".


HOW FAR IT HAS BEEN TESTED
--------------------------
NOT tested on real hardware or on Windows. CH341A reading was written from
WCH's published library header and tested against a simulated programmer.
XGecu reading is done by minipro, the established open-source tool for
these programmers, built here for Windows from its unmodified source; that
build has not been run on a Windows PC or with a real programmer.
Install.bat, the driver install, sound playback and card reading have not
been run on Windows either.

Tested here: 100 automated checks. Chip reading end to end against the
simulated programmer (every chip type reads back exactly; only read
commands are sent; read verification catches bad contacts). The decoder on
real EPROM dumps and on synthetic dumps for every encoding. The app's
screens, run without a display.

If something fails: the installer names the step that stopped; the Read
screen says what went wrong; and errors inside the app are written to
%APPDATA%\AudioChipDecoder\error.log


LICENCES
--------
minipro (audiochip\readers\xgecu\minipro.exe and its chip database) is
free software under the GNU GPL version 3 or later, from
https://gitlab.com/DavidGriffith/minipro . Its licence is next to it, and
its source and build notes are in tools\minipro-build. The app runs it as
a separate program.

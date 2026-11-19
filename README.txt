AUDIO CHIP DECODER 0.6.2
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

FT232H / FT2232H / FT4232H board  (SPI and 24-series I2C chips, wired by hand)
   SPI, port A: AD0 clock, AD1 to the chip, AD2 from the chip, AD3 chip select.
   I2C (24-series), port A: AD0 to SCL (pin 6); AD1 and AD2 joined together
   to SDA (pin 5), with a resistor of about 4.7 k from SDA to 3.3 V; pins 1,
   2, 3, 4 and 7 to GND; pin 8 to 3.3 V. Boards with an I2C switch join the
   two pins and supply the resistor when the switch is on. The Read screen
   shows the wiring for the chip family chosen.
   Uses FTDI's driver, which Windows normally installs by itself.

Raspberry Pi Pico or Arduino running serprog firmware  (SPI chips)
   Pick the board's COM port. pico-serprog and frser-duino are the usual
   firmwares.

ON AN ARM PC  (Surface Pro 11, Snapdragon / Copilot+ laptops)
   Windows on ARM cannot load the x64 kernel drivers these programmers
   normally use, so:
   - CH341A: WCH's driver has no ARM build. The app uses Windows' own
     WinUSB driver instead - on the Read screen the button becomes "Set up
     WinUSB". This reads 25-series SPI chips only (no 24-series I2C).
   - FT232H / FT2232H: the smoothest choice on ARM. FTDI ships ARM drivers
     (from ftdichip.com, "Windows (Desktop)" ARM64), and both SPI and I2C
     work once that driver is installed.
   - CH347: needs WCH's driver and has no WinUSB mode here, so it cannot be
     used on ARM. Use a CH341A or FT232H, or read on an Intel/AMD PC.
   - XGecu and serprog boards use their makers' USB-serial / WinUSB
     drivers; serprog's COM-port driver has an ARM build, XGecu's may not.
   None of the ARM paths have been tested by the author on an ARM machine
   yet.

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
- The app starts filling the screen. Restored to a window, it is sized to
  fit the screen; in a small window the main view and the dialogs scroll
  instead of being cut off. The Details panel always starts closed, and
  opens as its own window when there is no room beside the main view.
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
  state their own speed (uPD7759, LPC speech) need no check. The estimate
  treats 12,000 and 24,000 Hz as ordinary speeds, so a 24 kHz board is no
  longer nudged down to 22,050; where two speeds are within about 8% (such
  as 22,050 and 24,000) it offers both and lets your ear decide.
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
                 Through an FT232H board: the same SPI chips, and
                   24-series I2C EEPROM.
                 Through a serprog board: the SPI chips above.
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
Also opens:      Ordinary audio files: WAV (PCM 8/16/24/32-bit, Microsoft
                   ADPCM, IMA ADPCM, G.721, G.723, GSM 06.10, A-law,
                   mu-law), FLAC, Ogg Vorbis, Opus, MP3, AIFF, AU, CAF
                   (including ALAC), VOC. These carry their own speed.
                 Sony PlayStation ADPCM: VAG files and raw SPU blocks.
                 Through FFmpeg, which the app carries as a small helper:
                   AAC / HE-AAC (ADTS, M4A, MP4, 3GP), WMA 1, 2, Pro,
                   Lossless and Voice (ASF, XWMA), AMR-NB and AMR-WB,
                   Speex (Ogg), QCELP and EVRC (QCP, 3G2), CRI ADX, EA
                   ADPCM (SCHl), Bink Audio, Creative ADPCM (VOC, WAV),
                   Nintendo DSP ADPCM in BRSTM / BFSTM / GENH, and WAV or
                   CAF files holding other codecs (Yamaha ADPCM, G.723.1,
                   G.729, TrueSpeech, Apple Lossless...). These files are
                   also found when they sit inside a larger dump: each one
                   becomes a sound in the list.
                 GeneralPlus A1800, the codec of many talking toys: clips
                   stored in a flash dump as FF 40/42 00 E0 2E containers
                   (as in Fisher-Price Little People toys), and .a18 files.
                   Each clip becomes a sound in the list, at 16,000 Hz.
                 AMR-NB frames stored without a file header (recognised by
                   the unbroken chain of frame headers).
                 SNES BRR sample files (.brr, with or without the 2-byte
                   loop header).
                 Nintendo DSP ADPCM in a standard single-channel .dsp file.
Pick by hand:    These are in the Encoding list but are never chosen
                 automatically, because nothing in the bytes proves them:
                 G.721 / G.726 at 32 kbit/s (either nibble order), G.723 at
                   24 and 40 kbit/s, GSM 06.10 full rate
                 Raw PCM 24-bit and 32-bit, signed or unsigned, either
                   byte order (the top 16 bits are kept)
                 G.726 at 16 kbit/s, either bit packing
                 Yamaha ADPCM-A (YM2610 / YM2608 sample ROMs): played at
                   18,518 Hz, the rate with the usual 8 MHz clock
                 SNES BRR data that is not a single sample file (several
                   samples end to end are split at their end flags)
Writes:          WAV, FLAC, MP3, Ogg Vorbis, Opus.
Unknown formats: when nothing decodes, the app looks for fixed-size frames
                 with a repeating marker and reports the frame size and
                 marker bytes. It does not guess a decoder.

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
- 24-series I2C chips through a serprog board: the serprog protocol has
  no I2C command, so no software can do this. Use a CH341A, CH347 or
  FT232H. On an FT232H, Microchip's 24LC1025 / 24AA1025 are not supported
  (their upper-half select bit sits elsewhere); AT24C1024 / AT24CM01 are.
- The FFmpeg formats are recognised by their file header or sync pattern.
  Bare frames with no header (other than AMR-NB) cannot be told apart from
  noise, so they are not decoded.
- Included but not yet tried on a real file, because no sample could be
  obtained: QCELP / EVRC, EA ADPCM, Bink Audio, Creative ADPCM, WMA Voice,
  XWMA, BRSTM / BFSTM / GENH. FFmpeg's own decoders handle them; if one
  fails, the app says so and nothing else is affected.
- Two-channel .dsp variants and ADX files that are encrypted.
- Other GeneralPlus / Sunplus codecs (S480, S530, S720, A1600, A3600...)
  and A1800 containers with a different header than the one above.
- Generalplus / Sunplus speech (GPDS, unSP, A1600, A1800 and relatives):
  no published, verified decoder is built in. Frames of these formats are
  detected and reported (size and marker bytes) but not decoded.
- Other custom sound chips (Sega, Namco and similar). These show as "not
  recognised".
- LPC speech is found by searching for phrases. In a ROM that mixes speech
  with program code, a few short false phrases can appear in the list.


HOW FAR IT HAS BEEN TESTED
--------------------------
On Windows, automatically, for every version (GitHub's Windows build
machine): all the automated checks pass; the bundled XGecu helper starts
and answers "No programmer found"; the bundled FFmpeg helper starts and
decodes; the standalone program is built, opens its window, decodes a test
signal, writes FLAC and MP3, and loads its USB and serial-port libraries;
the installer is built from it.

NOT tested with real hardware by the developer: reading through any
programmer. Each reader (CH341A with WCH's driver, CH341A with WinUSB,
CH347, FT232H, serprog boards, XGecu) was written from the published
description of its commands and tested against a simulation of that
device. For FT232H I2C that simulation works at the level of the two bus
lines and a 24-series chip obeying the I2C rules. The first read with a real programmer is the real test. Running
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
- Formats decoded by FFmpeg (0.6.0): tried on real files made by other
  encoders for AAC, WMA 2 / Pro / Lossless, Speex, AMR-NB and AMR-WB, and
  on files made by FFmpeg's own encoders for ADX, WMA 1, Apple Lossless,
  Yamaha ADPCM, G.723.1 and G.726. Not tried on a real file: QCELP / EVRC,
  EA ADPCM, Bink Audio, Creative ADPCM, WMA Voice, XWMA, BRSTM / BFSTM /
  GENH.
- GeneralPlus A1800 (0.6.1): the decoder is the open-source a1800_codec.
  Its output was checked against audio made by a second, independently
  written A1800 encoder (clean match, exact 320-sample delay). The
  container search follows a write-up of a real Fisher-Price flash dump
  that held 164 clips; it has not yet been run on that dump itself.
- SNES BRR, Yamaha ADPCM-A and Nintendo DSP ADPCM (0.6.0): each decoder
  gives exactly the same samples as its reference implementation (Snes9x's
  sound chip code, ymfm, vgmstream) on over a million samples of test data
  covering every setting. Not tried on dumps of real cartridges or boards.

Robustness (0.5.1): every entry of the Encoding list is tried on every
kind of file (voice, random, tiny, one byte, blank, odd-sized, whole and
damaged audio files) in the automated checks; 840 deliberately corrupted
audio files were decoded without a crash; decoders that work one sample at
a time run in the background behind a progress window with Cancel; an
error while decoding shows as a line under the headline.
Robustness (0.6.0): the same sweep with every new kind of file; 1,440
deliberately corrupted files of the new formats decoded without a crash,
a hang or a temporary file left behind. If the FFmpeg helper is missing,
fails or stalls, the app says so, stops it at a time limit, and carries on.

If something fails: the installer names the step that stopped; the Read
screen says what went wrong; and errors inside the app are written to
%APPDATA%\AudioChipDecoder\error.log


THIS PROGRAM'S LICENCE

This program's own code is all rights reserved: shown publicly for reference
only, not open source. You may run the installer, but not copy, change,
redistribute or sell the code without permission. See LICENSE.txt. The bundled
helper programs below keep their own open-source licences.

THIRD-PARTY LICENCES
--------
See THIRD-PARTY.txt. In short: minipro (the XGecu helper program) is GNU
GPL version 3 or later and runs as a separate program; its source is in
tools\minipro-build. FFmpeg (the audio helper program) is LGPL 2.1 or
later, built with only audio decoders, and also runs as a separate
program; tools\build-ffmpeg.sh builds it. a1800_codec (the A1800 helper
program) is Apache License 2.0; tools\build-a1800.sh builds it. The sound-chip tables come from
the MAME project (BSD 3-Clause). The other libraries are BSD, LGPL or Apache licensed.

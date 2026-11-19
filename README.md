# Audio Chip Decoder

**Reads a memory chip, works out how the audio on it is stored, decodes it, and saves it as normal audio files — all in one Windows program.**

Point it at a chip dump (or read the chip directly with a cheap USB programmer), and it figures out the format on its own, shows you the sounds it found, lets you set the playback speed by ear, and exports WAV, FLAC, MP3, Ogg or Opus.

## Download

**[⬇ Get the latest installer](https://github.com/ItsVogue/Audio-Chip-Decoder/releases/latest)** — on that page, under **Assets**, click `AudioChipDecoder-Setup-x.y.z.exe` and run it.

One file, no Python to install, installs for your user only. Windows SmartScreen may warn because the installer isn't code-signed — choose **More info → Run anyway**.

> The installer lives on the **Releases** page (the link above), not in the file list at the top of the repo.

## What it does

- **Finds the audio automatically.** It tests the dump against many encodings and tells you which fits, with its reasoning and how sure it is — you don't have to know the format up front.
- **Decodes a wide range of formats.** Raw PCM, µ-law/A-law, OKI/IMA/Yamaha ADPCM, CVSD, TI LPC, NEC µPD7759, OKI MSM6295, PlayStation ADPCM, SNES BRR, Yamaha ADPCM-A, Nintendo DSP, GeneralPlus A1800 (talking toys), and — through a bundled mini-FFmpeg — AAC, WMA, AMR, Speex, QCELP, CRI ADX, EA ADPCM, Bink and more. Many of these are also found when they sit *inside* a larger dump.
- **Splits out individual sounds** and exports them one by one or as one track.
- **Speed by ear.** The playback speed isn't stored on a chip, so it estimates a shortlist and gives you a slider and a listening check to lock it in.
- **Reads chips directly** over common USB programmers, or opens a dump file you already have.

## Reading hardware

| Programmer | Chips | Notes |
|---|---|---|
| CH341A (the common black SOIC-8 clip kit) | 25-series SPI flash, 24-series I2C EEPROM | WCH driver, or WinUSB |
| CH347 | 25-series SPI, 24-series I2C | WCH driver |
| FT232H / FT2232H | 25-series SPI, 24-series I2C | FTDI's driver (has an ARM build) |
| XGecu TL866 / T48 / T56 | EPROMs and flash in its own huge chip list | needs XGecu's driver |
| Pico / Arduino running serprog | 25-series SPI | over a COM port |

**On an ARM PC (Surface Pro 11, Snapdragon laptops):** WCH's CH341 driver has no ARM version, so the CH341A uses Windows' built-in WinUSB instead — the app sets that up for you (SPI chips only). The **FT232H** is the smoothest ARM option, since FTDI ships ARM drivers. See [README.txt](README.txt) for the details.

## Licence

The code here is published **for reference only — it is not open source, all rights reserved.** You may read it and run the released installer, but you may not copy, modify, redistribute, or sell it without permission. See [LICENSE.txt](LICENSE.txt). The bundled helper programs (FFmpeg, minipro, a1800_codec) keep their own open-source licences, listed in [THIRD-PARTY.txt](THIRD-PARTY.txt).

## More

Full instructions, the complete format list, the readers, and exactly how far each part has been tested: **[README.txt](README.txt)**.

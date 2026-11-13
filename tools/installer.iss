; Inno Setup script: wraps the PyInstaller folder (dist\AudioChipDecoder) into one Setup.exe.
; Built by .github/workflows/build.yml:   ISCC /DAppVersion=1.2.3 tools\installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C2B5E0E-6F4C-4B4E-9C55-3B0F6D0A7A41}
AppName=Audio Chip Decoder
AppVersion={#AppVersion}
AppPublisher=Audio Chip Decoder
DefaultDirName={localappdata}\Programs\Audio Chip Decoder
DefaultGroupName=Audio Chip Decoder
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=AudioChipDecoder-Setup-{#AppVersion}
SetupIconFile=..\audiochip\gui\icon.ico
UninstallDisplayIcon={app}\AudioChipDecoder.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\AudioChipDecoder\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Audio Chip Decoder"; Filename: "{app}\AudioChipDecoder.exe"
Name: "{group}\Read me"; Filename: "{app}\README.txt"
Name: "{userdesktop}\Audio Chip Decoder"; Filename: "{app}\AudioChipDecoder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AudioChipDecoder.exe"; Description: "Start Audio Chip Decoder"; Flags: nowait postinstall skipifsilent

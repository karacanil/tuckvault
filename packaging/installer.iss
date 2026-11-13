; Inno Setup script for the Safekeep Windows installer.
;
;   iscc /DAppVersion=1.1.0 packaging\installer.iss
;
; Needs dist\Safekeep from PyInstaller (see safekeep.spec). Installs for the
; current user only, so no administrator prompt. Settings and the backup key
; live in %APPDATA%\Safekeep and are deliberately left alone on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F3A9C52-8D41-4B7E-9E2A-5C1D7B0A4F38}
AppName=Safekeep
AppVersion={#AppVersion}
AppPublisher=Safekeep
DefaultDirName={localappdata}\Programs\Safekeep
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Safekeep-{#AppVersion}-setup
SetupIconFile=..\safekeep\assets\safekeep.ico
UninstallDisplayIcon={app}\Safekeep.exe
UninstallDisplayName=Safekeep
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Safekeep\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Safekeep"; Filename: "{app}\Safekeep.exe"
Name: "{autodesktop}\Safekeep"; Filename: "{app}\Safekeep.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Safekeep.exe"; Description: "Open Safekeep"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Scheduled backups would otherwise keep pointing at a program that is gone.
Filename: "{app}\safekeep-cli.exe"; Parameters: "schedule remove"; Flags: runhidden; RunOnceId: "RemoveScheduledBackups"

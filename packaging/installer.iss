; Inno Setup script for the Tuckvault Windows installer.
;
;   iscc /DAppVersion=1.1.0 packaging\installer.iss
;
; Needs dist\Tuckvault from PyInstaller (see tuckvault.spec). Installs for the
; current user only, so no administrator prompt. Settings and the backup key
; live in %APPDATA%\Tuckvault and are deliberately left alone on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{45BE877C-0DEA-4929-83C8-9BAE488EA0A0}
AppName=Tuckvault
AppVersion={#AppVersion}
AppPublisher=Tuckvault
DefaultDirName={localappdata}\Programs\Tuckvault
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Tuckvault-{#AppVersion}-setup
SetupIconFile=..\tuckvault\assets\tuckvault.ico
UninstallDisplayIcon={app}\Tuckvault.exe
UninstallDisplayName=Tuckvault
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Tuckvault\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Tuckvault"; Filename: "{app}\Tuckvault.exe"
Name: "{autodesktop}\Tuckvault"; Filename: "{app}\Tuckvault.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Tuckvault.exe"; Description: "Open Tuckvault"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Scheduled backups would otherwise keep pointing at a program that is gone.
Filename: "{app}\tuckvault-cli.exe"; Parameters: "schedule remove"; Flags: runhidden; RunOnceId: "RemoveScheduledBackups"

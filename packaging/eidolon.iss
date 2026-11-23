; EIDOLON — Windows installer (Inno Setup 6). Built by packaging\build.ps1:
;   ISCC.exe /DAppVersion=1.0.0 packaging\eidolon.iss
;
; Installs for the current user only (no admin prompt) into
; %LOCALAPPDATA%\Programs\EIDOLON — a folder the app can write to, because its
; configuration (your API key), memory and logs live next to it.
; Uninstalling removes the program but keeps that personal data, so a
; reinstall or an update starts where you left off.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "EIDOLON"
#define AppExe "EIDOLON.exe"
#define AppPublisher "Aymerick (@AYMRIKNG)"
#define AppURL "https://github.com/AYMRIKNG/EIDOLON"

[Setup]
AppId={{F0FF56DD-62BB-49A9-B371-B6C31CD5D850}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\config\jarvis.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Lancer {#AppName} à l'ouverture de la session"; GroupDescription: "Démarrage :"; Flags: unchecked

[Files]
Source: "..\dist\EIDOLON\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; program-generated caches only — config\, memory\, output\ (your data) are kept
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\config\ide_bridge.*.running"

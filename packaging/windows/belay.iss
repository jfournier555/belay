; Belay Windows installer.
;
; Built by the release workflow on windows-latest: choco installs
; Inno Setup, iscc compiles this with /DMyAppVersion=<version>.
; Per-user install, no admin: the app goes to
; %LOCALAPPDATA%\Programs\Belay, with a Start Menu shortcut and an
; optional desktop icon and PATH entry. Unsigned first, same as the
; bare binary: SmartScreen shows the "Unknown publisher" warning and
; the user clicks Run anyway.
;
; The installer never touches the data folder (%APPDATA%\Belay):
; uninstalling removes the app only, never your drafts or keys.
; Source paths are relative to this file: belay.exe is the packaged
; binary at the repo root (the workflow copies it there first),
; belay.ico sits next to this script.

#define MyAppName "Belay"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "Justin Fournier"
#define MyAppURL "https://github.com/jfournier555/belay"

[Setup]
AppId={{F2C7EB13-4B0D-46BB-B83A-1D15E14CC700}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\..
OutputBaseFilename=Belay-Setup-{#MyAppVersion}
SetupIconFile=belay.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\belay.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add Belay to PATH (run `belay` from any terminal)"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\belay.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "belay.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\belay.exe"; Parameters: "launch"; IconFilename: "{app}\belay.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\belay.exe"; Parameters: "launch"; IconFilename: "{app}\belay.ico"; Tasks: desktopicon

[Registry]
; PATH task: append {app} to the user PATH, once. The Check keeps a
; re-install from adding it twice.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath('{app}')

[Run]
Filename: "{app}\belay.exe"; Parameters: "launch"; Description: "Launch Belay"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

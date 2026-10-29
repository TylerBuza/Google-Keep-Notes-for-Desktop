; Inno Setup script for KeepDesktop
; Build with:  ISCC.exe installer.iss
; Requires:    Inno Setup 6 (https://jrsoftware.org/isdl.php)
;
; The version is read from MyAppVersion below — keep it in sync with
; config.py's APP_VERSION (or pass /DMyAppVersion=x.y.z on the command line).

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName        "KeepDesktop"
#define MyAppPublisher   "TylerBuza"
#define MyAppURL         "https://github.com/TylerBuza/Google-Keep-Notes-for-Desktop"
#define MyAppExeName     "KeepDesktop.exe"

#ifndef MyAppNumericVersion
  #define MyAppNumericVersion "2.2.0.0"
#endif

[Setup]
; Private-build identity; never register as an upgrade to the upstream installer.
AppId={{E7720D5B-8CF2-4C7A-A6DA-6CA4B9A6A4DD}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppNumericVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=Output
OutputBaseFilename=KeepDesktop-Setup-{#MyAppVersion}
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
; Allow updates to overwrite the running app
CloseApplications=yes
RestartApplications=yes
; Don't force a reboot if files are in use; we'll handle it
CloseApplicationsFilter=*.exe,*.dll,*.pyd

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup";     Description: "Start {#MyAppName} when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; PyInstaller's --onedir output goes to dist\KeepDesktop\
Source: "dist\KeepDesktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE";            DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";          DestDir: "{app}"; Flags: ignoreversion
Source: "CREDITS.md";         DestDir: "{app}"; Flags: ignoreversion
Source: "SECURITY.md";        DestDir: "{app}"; Flags: ignoreversion
Source: "security\LOGIN-REVIEW.md"; DestDir: "{app}\security"; Flags: ignoreversion
Source: "security\dependency-audit.json"; DestDir: "{app}\security"; Flags: ignoreversion
Source: "Output\security-build-audit.json"; DestDir: "{app}\security"; Flags: ignoreversion
Source: "Output\security-frozen-inventory.json"; DestDir: "{app}\security"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}";    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; Tasks: startup

[Run]
; nowait  -> don't block the installer waiting for the app
; postinstall -> run as the very last step
; runasoriginaluser -> if installer was elevated, launch the app as the
;                      logged-in user so it doesn't run elevated forever
; Unattended installs must not open a live Google login session.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall runasoriginaluser skipifsilent

; Uninstall removes installer-owned files only. Leave user note data and any
; user-added files intact; never recursively delete the entire install folder.

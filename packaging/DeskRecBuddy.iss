#ifndef AppVersion
  #error AppVersion must be supplied by build_release.ps1
#endif
#ifndef SourceRoot
  #error SourceRoot must be supplied by build_release.ps1
#endif

[Setup]
AppId={{6C799F17-789D-4AC0-8E25-799A94498439}
AppName=DeskRec Buddy
AppVersion={#AppVersion}
AppPublisher=RootforgeIO
AppPublisherURL=https://github.com/RootforgeIO
VersionInfoVersion={#AppVersion}
VersionInfoDescription=DeskRec Buddy Setup
DefaultDirName={localappdata}\Programs\DeskRec Buddy
DefaultGroupName=DeskRec Buddy
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#ReleaseRoot}
OutputBaseFilename=DeskRec-Buddy-{#AppVersion}-setup
SetupIconFile=..\assets\deskrec-buddy.ico
UninstallDisplayIcon={app}\DeskRec Buddy.exe
UninstallDisplayName=DeskRec Buddy
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\DeskRecBuddy.rootforged.io
SetupMutex=Local\DeskRecBuddy.Setup.rootforged.io
CloseApplications=no
RestartApplications=no
SetupLogging=yes
DisableWelcomePage=no

[Languages]
Name: russian; MessagesFile: compiler:Languages\Russian.isl
Name: english; MessagesFile: compiler:Default.isl

[Files]
Source: {#SourceRoot}\DeskRec Buddy.exe; DestDir: {app}; Flags: ignoreversion
Source: {#SourceRoot}\_internal\*; DestDir: {app}\_internal; Flags: ignoreversion recursesubdirs createallsubdirs
Source: {#SourceRoot}\LICENSES\*; DestDir: {app}\LICENSES; Flags: ignoreversion recursesubdirs createallsubdirs
Source: {#SourceRoot}\README.md; DestDir: {app}; Flags: ignoreversion
Source: {#SourceRoot}\THIRD_PARTY.md; DestDir: {app}; Flags: ignoreversion
Source: {#SourceRoot}\CHANGELOG.md; DestDir: {app}; Flags: ignoreversion
Source: {#SourceRoot}\assets\deskrec-buddy.svg; DestDir: {app}\assets; Flags: ignoreversion

[Icons]
Name: {autoprograms}\DeskRec Buddy; Filename: {app}\DeskRec Buddy.exe; WorkingDir: {app}
Name: {autodesktop}\DeskRec Buddy; Filename: {app}\DeskRec Buddy.exe; WorkingDir: {app}

[Run]
Filename: {app}\DeskRec Buddy.exe; Description: {cm:LaunchProgram,DeskRec Buddy}; Flags: nowait postinstall skipifsilent

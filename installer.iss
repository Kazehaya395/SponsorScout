;
; SponsorScout Inno Setup Installer Script
; https://jrsoftware.org/isinfo.php
;
; Production-ready installer:
; - stable AppId
; - no duplicate uninstall registry entry
; - force-close running app during uninstall/install
; - remove AppData user-data folder on uninstall
; - UninstallRun kills SponsorScout.exe before file deletion
;

#define MyAppName      "SponsorScout"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.1"
#endif
#define MyAppPublisher "SponsorScout"
#define MyAppURL       "https://github.com/Kazehaya395/SponsorScout"
#define MyAppExeName   "SponsorScout.exe"
#define MyAppIcoName   "sponsorscout.ico"
#define MyAppDataDirName "SponsorScout"

[Setup]
AppId=SponsorScout
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=auto
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=sponsorscout-{#MyAppVersion}-setup
SetupIconFile=sponsorscout\data\sponsorscout.ico
WizardImageFile=sponsorscout\data\sponsorscout.png
WizardSmallImageFile=sponsorscout\data\sponsorscout.png
UninstallDisplayIcon={app}\{#MyAppIcoName}
UninstallDisplayName={#MyAppName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin
Compression=lzma2
SolidCompression=yes
ShowTasksTreeLines=yes
DisableFinishedPage=no

; Detect a running instance before installing over it.
;
; NOTE: there is deliberately NO AppMutex= entry. Inno's CloseApplications
; detects the app through its top-level window, which is enough here, and an
; AppMutex naming a mutex the app never creates (it only sets an AppUserModelID
; via SetCurrentProcessExplicitAppUserModelID) would advertise a guarantee the
; app does not provide. If a real single-instance mutex is ever added to
; main.py, add the matching AppMutex line here.
;
; Force-close the app during both install and uninstall
CloseApplications=force
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
; With --onedir PyInstaller build, the entire build directory is copied.
; Recurse into subdirs and create all subdirectories at install time.
Source: "dist\SponsorScout-InstallerFiles\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcoName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcoName}"; Tasks: desktopicon

[Registry]
; Point Playwright at the bundled _playwright directory so the app works on the
; very first launch and offline (no ~130 MB download).
;
; This MUST be HKCU, not HKLM:
;   * {app} is {autopf}\SponsorScout — a PER-USER directory. A machine-wide
;     (HKLM) variable would point every account on the PC at whichever user
;     installed last, and every other user would fail to find the browser.
;   * An HKLM value with `Permissions: everyone-modify` lets any non-admin
;     rewrite an environment variable that is injected into EVERY new process
;     on the machine. HKCU needs no Permissions directive at all.
;   * `uninsdeletevalue` then only clears the installing user's own value
;     instead of deleting the shared one out from under other installations.
;
; Note: a registry change does not reach a process launched by a shell that has
; not re-read the environment, so the very first run after install would miss
; it. sponsorscout/paths.py handles exactly that by falling back to
; exe_dir\_playwright, so this entry is a convenience, not a dependency.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "PLAYWRIGHT_BROWSERS_PATH"; ValueData: "{app}\_playwright"; Flags: uninsdeletevalue

[Tasks]
Name: "desktopicon"; Description: "Create Desktop Shortcut"; GroupDescription: "Additional Icons"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; Kill SponsorScout.exe BEFORE uninstall file deletion begins.
; This ensures the main exe is not locked and can be fully removed.
[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im SponsorScout.exe"; Flags: runhidden runminimized skipifdoesntexist
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im Sponsorscout.exe"; Flags: runhidden runminimized skipifdoesntexist

[UninstallDelete]
; Remove the installed bundle. User data is purged again from [Code] so
; explicitly configured data paths are handled as well.
; Keep the shared Playwright browser cache: other applications may use it.
Type: filesandordirs; Name: "{app}"

[Code]
const
  AppDataDirName = 'SponsorScout';

function GetUserAppDataDir(): string;
begin
  Result := ExpandConstant('{userappdata}\' + AppDataDirName);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    AppDir := GetUserAppDataDir();
    if not DirExists(AppDir) then
    begin
      if CreateDir(AppDir) then
      begin
        Log('Created AppData dir: ' + AppDir);
        SaveStringToFile(AppDir + '\.keep', #13#10, False);
      end
      else
      begin
        Log('Failed to create AppData dir: ' + AppDir);
        MsgBox('Failed to create AppData folder. Database features may be restricted.', mbCriticalError, 'Warning');
      end;
    end;
  end;
end;

function IsUnsafePurgePath(const PathValue: string): boolean;
begin
  Result := (PathValue = '') or (PathValue = '\');
  if (Length(PathValue) >= 2) and (PathValue[1] = '\') and (PathValue[2] = '\') then
    Result := True;
  if (Length(PathValue) = 2) and (PathValue[2] = ':') then
    Result := True;
  if (Length(PathValue) = 3) and (PathValue[2] = ':') and (PathValue[3] = '\') then
    Result := True;
end;

procedure PurgePath(const LabelText, PathValue: string);
var
  FullPath: string;
begin
  if PathValue = '' then
    Exit;
  FullPath := ExpandConstant(PathValue);
  if IsUnsafePurgePath(FullPath) then
  begin
    Log('Refusing unsafe purge path: ' + FullPath);
    Exit;
  end;
  if DirExists(FullPath) then
  begin
    Log('Purging ' + LabelText + ': ' + FullPath);
    DelTree(FullPath, True, True, True);
  end;
end;

procedure PurgeUserData;
var
  CustomDataDir: string;
  CustomDbPath: string;
begin
  // Default Windows locations used by sponsorscout/paths.py.
  PurgePath('application data', '{userappdata}\{#MyAppDataDirName}');
  PurgePath('local application data', '{localappdata}\{#MyAppDataDirName}');
  PurgePath('user profile data', '{%USERPROFILE}\.sponsorscout');

  // Also honor explicit overrides when they are present in the uninstaller's
  // environment. Never purge a drive root or the user's entire profile.
  CustomDataDir := ExpandConstant('{env:SPONSORSCOUT_DATA_DIR}');
  if CustomDataDir <> '' then
    PurgePath('configured data directory', CustomDataDir);

  CustomDbPath := ExpandConstant('{env:SPONSORSCOUT_DB_PATH}');
  if CustomDbPath <> '' then
  begin
    if FileExists(CustomDbPath) then
      DeleteFile(CustomDbPath);
    if FileExists(CustomDbPath + '-wal') then
      DeleteFile(CustomDbPath + '-wal');
    if FileExists(CustomDbPath + '-shm') then
      DeleteFile(CustomDbPath + '-shm');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Ensure the GUI is closed before Inno removes the application files.
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im SponsorScout.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im Sponsorscout.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  if CurUninstallStep = usPostUninstall then
    PurgeUserData;
end;
; Inno Setup script for RDS Master.
;
; Built by packaging/build.py, which passes the version and paths in:
;     ISCC rds-master.iss /DAppVersion=1.5 /DSourceDir=... /DOutputDir=...
;
; Two ways to run it unattended, because they suit different soundcards:
;
;   Session mode  - a scheduled task at logon. Runs in your own session, so
;                   every host API works, including WASAPI and MME. Needs
;                   someone logged in (or auto-logon).
;   Service mode  - a real Windows service. Starts before anyone logs in, but
;                   session 0 has no audio engine, so the output device must be
;                   a WDM-KS one. This is how StereoTool and BreakawayOne do it.

#ifndef AppVersion
  #define AppVersion "1.5"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\rds-master"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\installers"
#endif

[Setup]
; A fixed identifier, so an upgrade recognises the existing install and
; replaces it rather than putting a second copy beside it. Never change this.
AppId={{8F3A6C21-5D4E-4B77-9E2A-6B10D7C4E901}
AppName=RDS Master
AppVersion={#AppVersion}
AppPublisher=FMDX.ie
AppPublisherURL=https://github.com/fmdx-ie/rds-master
DefaultDirName={autopf}\RDS Master
DefaultGroupName=RDS Master
UninstallDisplayIcon={app}\rds-master.exe
OutputDir={#OutputDir}
OutputBaseFilename=rds-master-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; Needed to register a service and to write under ProgramData.
PrivilegesRequired=admin
WizardStyle=modern
SetupIconFile=..\icons\rdsm.ico
DisableProgramGroupPage=yes
; The directory page stays on: more than one install on a machine is a
; real arrangement, and each needs its own folder and its own port.
DisableDirPage=no
; Offer to close anything holding the files rather than demanding a reboot.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "tray";    Description: "Show the RDS Master icon in the notification area at logon"; GroupDescription: "Startup"
Name: "desktopicon"; Description: "Create a desktop shortcut to the web interface"; GroupDescription: "Shortcuts"
Name: "session"; Description: "Start the encoder at logon (session mode - any soundcard)"; GroupDescription: "Startup"
Name: "service"; Description: "Install as a Windows service (starts before logon - needs a WDM-KS device)"; GroupDescription: "Startup"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; The configuration lives here, not under Program Files: a service account
; cannot write there and an upgrade would overwrite it.
;
; users-modify matters: the installer runs elevated, so without it everything
; created here would belong to Administrators, and in session mode the encoder
; runs as the logged-in user and could not write its own configuration.
Name: "{commonappdata}\RDS Master"; Permissions: users-modify

[Icons]
Name: "{group}\RDS Master";          Filename: "{app}\rds-master-tray.exe"
Name: "{group}\RDS Master tray"; Filename: "{app}\rds-master-tray.exe"
; A shortcut straight to the web interface, on whichever port was chosen.
Name: "{autodesktop}\RDS Master";  Filename: "{code:GetWebUrl}"; \
  IconFilename: "{app}\rds-master.exe"; Tasks: desktopicon
Name: "{group}\RDS Master web interface"; Filename: "{code:GetWebUrl}"; \
  IconFilename: "{app}\rds-master.exe"

[Run]
; Session mode: a scheduled task, which can run at logon without a service.
Filename: "schtasks"; \
  Parameters: "/Create /F /TN ""RDS Master encoder"" /TR ""\""{app}\rds-master.exe\"""" /SC ONLOGON /RL HIGHEST"; \
  Flags: runhidden; Tasks: session; StatusMsg: "Setting the encoder to start at logon..."

Filename: "schtasks"; \
  Parameters: "/Create /F /TN ""RDS Master tray"" /TR ""\""{app}\rds-master-tray.exe\"""" /SC ONLOGON"; \
  Flags: runhidden; Tasks: tray; StatusMsg: "Setting the tray icon to start at logon..."

; Service mode. The wrapper is only in the build when pywin32 was there
; when it was made, so the step is skipped rather than failing without it.
; Remove any service from a previous version before installing this one:
; installing over an existing service is an error, not an upgrade.
Filename: "{app}\rds-master-service.exe"; Parameters: "remove"; \
  Flags: runhidden skipifdoesntexist; Tasks: service
Filename: "{app}\rds-master-service.exe"; Parameters: "--startup=auto install"; \
  Flags: runhidden skipifdoesntexist; Tasks: service; \
  StatusMsg: "Installing the Windows service..."
Filename: "sc"; Parameters: "start RDSMaster"; \
  Flags: runhidden; Tasks: service; StatusMsg: "Starting the service..."

; Start it now rather than waiting for the next logon. Service mode has already
; been started by sc above, so this would be a second copy fighting for the port.
Filename: "{app}\rds-master.exe"; \
  Flags: nowait runhidden; Check: not WizardIsTaskSelected('service'); \
  StatusMsg: "Starting the encoder..."

Filename: "{app}\rds-master-tray.exe"; \
  Flags: nowait runhidden; Tasks: tray; StatusMsg: "Starting the tray icon..."

; The tray knows which port the configuration asks for, and waits for the
; encoder to answer before opening it - a browser pointed at a port still
; binding looks exactly like a failed install.
Filename: "{app}\rds-master-tray.exe"; Parameters: "--open"; \
  Description: "Open RDS Master in my browser"; \
  Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "schtasks"; Parameters: "/Delete /F /TN ""RDS Master encoder"""; Flags: runhidden; RunOnceId: "taskencoder"
Filename: "schtasks"; Parameters: "/Delete /F /TN ""RDS Master tray"""; Flags: runhidden; RunOnceId: "tasktray"
Filename: "sc"; Parameters: "stop RDSMaster"; Flags: runhidden; RunOnceId: "svcstop"
Filename: "{app}\rds-master-service.exe"; Parameters: "remove"; Flags: runhidden skipifdoesntexist; RunOnceId: "svcremove"
Filename: "sc"; Parameters: "delete RDSMaster"; Flags: runhidden; RunOnceId: "svcdelete"

[Code]
var
  PortPage: TInputQueryWizardPage;

// The port an existing installation is already using. Reading it back means an
// upgrade does not ask for something the operator has already decided, and a
// second installation on the same machine starts from a different number
// rather than colliding with the first.
function ExistingPort(): String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line, Value: String;
begin
  Result := '';
  if not LoadStringsFromFile(ExpandConstant('{commonappdata}') +
                             '\RDS Master\datasets.json', Lines) then
    Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Trim(Lines[I]);
    if Pos('"http_port"', Line) = 1 then
    begin
      Value := Copy(Line, Pos(':', Line) + 1, Length(Line));
      StringChangeEx(Value, ',', '', True);
      Value := Trim(Value);
      if Value <> '' then
        Result := Value;
      Exit;
    end;
  end;
end;

procedure InitializeWizard();
var
  Found: String;
begin
  PortPage := CreateInputQueryPage(wpSelectTasks,
    'Web interface', 'Which port should the web interface listen on?',
    'Leave this alone unless the port is already taken, or you are installing ' +
    'a second copy on this machine.');
  PortPage.Add('Port:', False);
  Found := ExistingPort();
  if Found <> '' then
  begin
    PortPage.Values[0] := Found;
    PortPage.SubCaptionLabel.Caption :=
      'An existing configuration was found, already set to port ' + Found +
      '. It is kept unless you change it here.';
  end
  else
    PortPage.Values[0] := '5000';
end;

function ChosenPort(): String;
begin
  Result := Trim(PortPage.Values[0]);
  if Result = '' then
    Result := '5000';
end;

function GetWebUrl(Param: String): String;
begin
  Result := 'http://127.0.0.1:' + ChosenPort() + '/';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Number: Integer;
begin
  Result := True;
  if CurPageID = PortPage.ID then
  begin
    Number := StrToIntDef(ChosenPort(), -1);
    if (Number < 1) or (Number > 65535) then
    begin
      MsgBox('A port must be a number between 1 and 65535.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

// An upgrade has to stop the running encoder first: Windows will not let the
// installer overwrite an executable that is in use, and the result is either a
// failed install or one that only finishes after a reboot.
procedure StopRunningCopy;
var
  Code: Integer;
begin
  Exec('sc', 'stop RDSMaster', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('schtasks', '/End /TN "RDS Master encoder"', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Exec('schtasks', '/End /TN "RDS Master tray"', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Exec('taskkill', '/F /IM rds-master.exe', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Exec('taskkill', '/F /IM rds-master-tray.exe', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Exec('taskkill', '/F /IM rds-master-service.exe', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  // Give Windows a moment to release the file handles.
  Sleep(1500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningCopy;
  Result := '';
end;

// The configuration carries the station's profiles, its web login and the
// Flask secret key. An upgrade keeps it and leaves a dated copy beside it.
// The port goes into the configuration the encoder reads. On a fresh install
// that is a small file it will fill out itself on first run; on an upgrade only
// the one line changes, and the whole file was copied aside a moment earlier.
procedure WriteChosenPort();
var
  Config: String;
  Lines: TArrayOfString;
  I: Integer;
  Done: Boolean;
begin
  Config := ExpandConstant('{commonappdata}\RDS Master\datasets.json');
  if not FileExists(Config) then
  begin
    SetArrayLength(Lines, 3);
    Lines[0] := '{';
    Lines[1] := '  "http_port": ' + ChosenPort() + ',';
    Lines[2] := '  "http_host": "0.0.0.0"' + #13#10 + '}';
    SaveStringsToFile(Config, Lines, False);
    Exit;
  end;

  if not LoadStringsFromFile(Config, Lines) then
    Exit;
  Done := False;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    if Pos('"http_port"', Trim(Lines[I])) = 1 then
    begin
      Lines[I] := '  "http_port": ' + ChosenPort() + ',';
      Done := True;
      Break;
    end;
  end;
  if Done then
    SaveStringsToFile(Config, Lines, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Config, Backup: String;
begin
  if CurStep = ssInstall then
  begin
    Config := ExpandConstant('{commonappdata}\RDS Master\datasets.json');
    if FileExists(Config) then
    begin
      Backup := Config + '.backup-' + GetDateTimeString('yyyymmdd-hhnnss', #0, #0);
      CopyFile(Config, Backup, False);
    end;
  end;
  if CurStep = ssPostInstall then
    WriteChosenPort();
end;

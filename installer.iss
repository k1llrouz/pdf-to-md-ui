; Inno Setup script for the PDF to Markdown converter.
;
; Produces a single Setup.exe that installs the app, the bundled JRE, a
; desktop shortcut and an Explorer context menu entry. Compile with:
;   iscc installer.iss
;
; Saved as UTF-8 with a BOM: Inno Setup reads it as ANSI otherwise, and the
; translated context menu captions below would come out garbled.
;
; Expects the PyInstaller output in dist\PDF to Markdown\, with the trimmed
; jre\ runtime already copied inside it — build.bat does both (see BUILD.md).

#define AppName "PDF to Markdown"
#define AppExe "PDF to Markdown.exe"

; CI passes the version with /DAppVersion=...; this is the local fallback.
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
; Spelled out, so it never changes with the name: it is how a new version's
; Setup finds the copy already installed (its folder, language and tasks)
; and installs over it. Left out, Inno Setup took AppName - the same value,
; so copies installed before it was written down are found too.
AppId=PDF to Markdown
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputBaseFilename=PDF-to-Markdown-Setup
Compression=lzma2/max
SolidCompression=yes
; Per-user install needs no admin rights, which avoids a UAC prompt.
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The GPL text on its own wizard page; the notices for the bundled components
; (opendataloader-pdf, veraPDF, Qt, the Java runtime) are installed with the app.
LicenseFile=LICENSE
; Picks the Windows display language by itself; asks only when none matches.
ShowLanguageDialog=auto
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
; Tells Explorer to pick up the context menu entries without a restart.
ChangesAssociations=yes
; A running copy holds its files; Setup closes it before replacing them. The
; program's own updater starts Setup and quits by itself (updates.py), so this
; only matters for a copy still on its way out, or one opened twice.
CloseApplications=yes

[Languages]
; Same languages as the app. The task and run captions below use Inno Setup's
; own translated messages, so nothing here needs translating by hand.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "contextmenu"; Description: "{cm:ContextMenuTask}"

[Registry]
; Right-click a PDF, or a folder, -> "Open in PDF -> Markdown". HKA is the
; user's own hive for this per-user install, so no admin rights are needed.
; Several PDFs picked at once start the program several times; the running
; one collects them all into its window (single_instance.py). "Player" lifts
; Explorer's 15-file limit on such a selection.
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFtoMarkdown"; ValueType: string; ValueName: ""; ValueData: "{cm:ContextOpen}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFtoMarkdown"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFtoMarkdown"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFtoMarkdown\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\PDFtoMarkdown"; ValueType: string; ValueName: ""; ValueData: "{cm:ContextOpen}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\PDFtoMarkdown"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\PDFtoMarkdown\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu

[CustomMessages]
english.ContextOpen=Open in PDF → Markdown
english.ContextMenuTask=Add “Open in PDF → Markdown” to the Explorer context menu
russian.ContextOpen=Открыть в PDF → Markdown
russian.ContextMenuTask=Добавить «Открыть в PDF → Markdown» в контекстное меню Проводника
polish.ContextOpen=Otwórz w PDF → Markdown
polish.ContextMenuTask=Dodaj „Otwórz w PDF → Markdown” do menu kontekstowego Eksploratora
french.ContextOpen=Ouvrir dans PDF → Markdown
french.ContextMenuTask=Ajouter « Ouvrir dans PDF → Markdown » au menu contextuel de l’Explorateur
spanish.ContextOpen=Abrir en PDF → Markdown
spanish.ContextMenuTask=Añadir «Abrir en PDF → Markdown» al menú contextual del Explorador
italian.ContextOpen=Apri in PDF → Markdown
italian.ContextMenuTask=Aggiungi «Apri in PDF → Markdown» al menu contestuale di Esplora file

; The program's own files are cleared before the new ones go in: [Files] only
; adds and overwrites, so a file the new version no longer has would stay - an
; old opendataloader_pdf-*.dist-info made About show the old version, and an
; old patch folder could be picked up again. Settings are not kept here.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Updating from within the program (updates.py) runs Setup with /SILENT
; /RELAUNCH=1: the program closed for the update, so it opens again after.
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent; Check: RelaunchRequested

[Code]
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

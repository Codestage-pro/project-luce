; Instalador do Project LUCE (Windows). Compilar com: ISCC /DAppVersion=1.0.18 luce.iss
; Entrada: ..\..\dist\LUCE (montada por .github/workflows/windows-build.yml).
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0C7B1E-52B4-4C0E-9B8F-4C1D5A0E7A31}
AppName=Project LUCE
AppVersion={#AppVersion}
AppPublisher=Codestage.pro
; Padrão: Arquivos de Programas (pede administrador). A janela inicial deixa escolher "só para mim" (vai para AppData\Local\Programs).
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\Project LUCE
DefaultGroupName=Project LUCE
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=Luce-Setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Project LUCE
SetupIconFile=luce.ico
UninstallDisplayIcon={app}\luce.ico

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\..\dist\LUCE\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Servidor padrão, lido pelo luce/config.py por baixo do config.toml da pessoa (que nunca é tocado)
Source: "..\config.toml"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Project LUCE"; Filename: "{app}\ucrt64\bin\luce.exe"; Parameters: """{app}\luce_launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\luce.ico"; AppUserModelID: "pro.codestage.Luce"
Name: "{autodesktop}\Project LUCE"; Filename: "{app}\ucrt64\bin\luce.exe"; Parameters: """{app}\luce_launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\luce.ico"; AppUserModelID: "pro.codestage.Luce"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na área de trabalho"; Flags: unchecked

[UninstallDelete]
; registro do GStreamer e logs
Type: filesandordirs; Name: "{localappdata}\LUCE"

[Run]
Filename: "{app}\ucrt64\bin\luce.exe"; Parameters: """{app}\luce_launcher.pyw"""; WorkingDir: "{app}"; Description: "Abrir o Project LUCE"; Flags: nowait postinstall

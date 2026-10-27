; Instalador do Project LUCE (Windows). Compilar com: ISCC /DAppVersion=1.0.28 luce.iss
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
Compression=lzma2/ultra64
SolidCompression=yes
; Só o luce.exe precisa ser checado pelo Restart Manager; com o padrão (*.exe,*.dll) ele varria todos os arquivos antes de instalar
CloseApplicationsFilter=luce.exe
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

[InstallDelete]
; O Inno só sobrescreve: arquivo que saiu do bundle numa versão nova ficaria para sempre (a 1.0.19 deixava ~900 MB).
; O runtime e o código são recriados inteiros a cada instalação; os dados da pessoa ficam em %LOCALAPPDATA%\LUCE.
Type: filesandordirs; Name: "{app}\ucrt64"
Type: filesandordirs; Name: "{app}\luce"

[UninstallDelete]
; registro do GStreamer e logs
Type: filesandordirs; Name: "{localappdata}\LUCE"

[Run]
Filename: "{app}\ucrt64\bin\luce.exe"; Parameters: """{app}\luce_launcher.pyw"""; WorkingDir: "{app}"; Description: "Abrir o Project LUCE"; Flags: nowait postinstall

# ============================================================
# ContaStock Pro - Instalador de Windows (Inno Setup)
#
# Se ejecuta desde packaging/build_windows.bat después de PyInstaller.
# Requiere el compilador ISCC.exe de Inno Setup 6.
# ============================================================

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\dist"
#endif
#ifndef PyInstallerDir
  #define PyInstallerDir "..\dist\ContaStockPro"
#endif

[Setup]
AppId={{7E2A1F6C-3B24-4C9A-8D5A-1F2B3C4D5E6F}
AppName=ContaStock Pro
AppVersion={#MyAppVersion}
AppPublisher=ElectroGalindez
AppComments=Sistema profesional de contabilidad, ventas e inventario.
DefaultDirName={autopf}\ContaStock Pro
DefaultGroupName=ContaStock Pro
OutputDir={#MyOutputDir}
OutputBaseFilename=ContaStockPro-setup
SetupIconFile=..\packaging\icons_win\contastock.ico
UninstallDisplayIcon={app}\ContaStockPro.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Opciones adicionales:"

[Files]
Source: "{#PyInstallerDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ContaStock Pro"; Filename: "{app}\ContaStockPro.exe"
Name: "{autodesktop}\ContaStock Pro"; Filename: "{app}\ContaStockPro.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ContaStockPro.exe"; Description: "Iniciar ContaStock Pro ahora"; Flags: nowait postinstall skipifsilent
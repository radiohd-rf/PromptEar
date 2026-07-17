; Inno Setup скрипт установщика PromptEar
; Требуется Inno Setup 6+ (https://jrsoftware.org/isdl.php)

#define MyAppName "PromptEar"
#define MyAppVersion "0.8.0"
#define MyAppPublisher "PromptEar"
#define MyAppExeName "run.vbs"

[Setup]
AppId={{B8F3A2D1-C5E6-4F7A-9B0C-1D2E3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=G:\opencode\PromptEar\dist
OutputBaseFilename=PromptEar_Setup_v0.8.0
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=no
UninstallDisplayIcon={app}\run.vbs
ChangesEnvironment=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: checkedonce

[Files]
; Исходные файлы приложения
Source: "main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "ui\*.py"; DestDir: "{app}\ui"; Flags: ignoreversion
Source: "processing\*.py"; DestDir: "{app}\processing"; Flags: ignoreversion
Source: "utils\*.py"; DestDir: "{app}\utils"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
; Лаунчер и установщик зависимостей
Source: "run.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "run.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "run.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "bootstrap.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall.bat"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; Установка Python + зависимостей + Ollama
Filename: "{app}\bootstrap.bat"; StatusMsg: "Установка Python + библиотек + Ollama + модели Qwen…"; Flags: runascurrentuser shellexec waituntilterminated
; Запуск PromptEar после установки
Filename: "{app}\run.vbs"; Description: "Запустить PromptEar"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Умное удаление: выбор компонентов (через uninstall.bat --silent — по умолчанию: программа + кеш + настройки)
Filename: "{app}\uninstall.bat"; Parameters: "--silent"; Flags: runascurrentuser shellexec waituntilterminated

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\run.vbs"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\run.vbs"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autoprograms}\Удалить {#MyAppName}"; Filename: "{app}\uninstall.bat"; IconFilename: "{app}\unins000.exe"
Name: "{autodesktop}\Удалить {#MyAppName}"; Filename: "{app}\uninstall.bat"; IconFilename: "{app}\unins000.exe"

[UninstallDelete]
Type: files; Name: "{app}\bootstrap.bat"
Type: files; Name: "{app}\run.bat"
Type: files; Name: "{app}\run.pyw"
Type: files; Name: "{app}\run.vbs"
Type: files; Name: "{app}\uninstall.bat"
Type: filesandordirs; Name: "{app}\venv"
Type: dirifempty; Name: "{app}"

; Inno Setup скрипт установщика PromptEar
; Требуется Inno Setup 6+ (https://jrsoftware.org/isdl.php)

#define MyAppName "PromptEar"
#define MyAppVersion "1.0"
#define MyAppPublisher "PromptEar"
#define MyAppURL ""
#define MyAppExeName "PromptEar.exe"

[Setup]
AppId={{B8F3A2D1-C5E6-4F7A-9B0C-1D2E3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=.\dist
OutputBaseFilename=PromptEar_Setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=no
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: checkedonce

[Files]
; Враппер и модули
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "ui\*.py"; DestDir: "{app}\ui"; Flags: ignoreversion
Source: "processing\*.py"; DestDir: "{app}\processing"; Flags: ignoreversion
Source: "utils\*.py"; DestDir: "{app}\utils"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
; Служебные файлы
Source: "bootstrap.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall.bat"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; Установка Python, зависимостей, Ollama и модели
Filename: "{app}\bootstrap.bat"; StatusMsg: "Установка Python + библиотек + Ollama + модели Qwen…"; Flags: runascurrentuser shellexec waituntilterminated
; Запуск PromptEar после установки
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить PromptEar"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Закрываем PromptEar перед удалением
Filename: "{cmd}"; Parameters: "/C taskkill /f /im PromptEar.exe 2>nul"; Flags: runascurrentuser shellexec
; Удаляем кеш моделей Whisper
Filename: "{cmd}"; Parameters: "/C if exist ""%USERPROFILE%\.cache\whisper"" rmdir /s /q ""%USERPROFILE%\.cache\whisper"""; Flags: shellexec

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\Удалить {#MyAppName}"; Filename: "{app}\uninstall.bat"; IconFilename: "{app}\unins000.exe"
Name: "{autodesktop}\Удалить {#MyAppName}"; Filename: "{app}\uninstall.bat"; IconFilename: "{app}\unins000.exe"

[UninstallDelete]
Type: files; Name: "{app}\bootstrap.bat"
Type: files; Name: "{app}\uninstall.bat"
Type: dirifempty; Name: "{app}"

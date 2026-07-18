' Запуск PromptEar без консоли
Dim shell, fso, pythonw, script, appDir
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = appDir & "\venv\Scripts\pythonw.exe"
script  = appDir & "\run.pyw"
If Not fso.FileExists(pythonw) Then
    shell.Popup "Виртуальное окружение не найдено. Запустите bootstrap.bat", 10, "PromptEar", 16
    WScript.Quit 1
End If
shell.Run """" & pythonw & """ """ & script & """", 0, False

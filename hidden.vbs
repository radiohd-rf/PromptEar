' Запуск run.bat в скрытом окне (wscript, windowStyle=0), чтобы без терминала.
Set sh = CreateObject("WScript.Shell")
sh.Run """" & WScript.Arguments(0) & """", 0, False
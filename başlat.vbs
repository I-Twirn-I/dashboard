Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & """ && python app.py", 0, False
WScript.Sleep 2000
WshShell.Run "http://localhost:5000"

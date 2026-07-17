' Scripto launcher (Windows, no console window).
' Runs Scripto.bat hidden; use Scripto.bat directly to see errors.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & dir & "\Scripto.bat""", 0, False

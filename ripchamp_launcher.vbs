' ripchamp_launcher.vbs
'
' Opens the interactive ripchamp prompt (ripchamp_tools.ps1 -Mode Prompt)
' at window style 4 -- visible, but doesn't steal focus from whatever you
' were doing (e.g. your game). Used by Custom Context Menu, drag-and-drop,
' and the folder watcher.
'
' Must be kept in the same folder as ripchamp_tools.ps1 and ripchamp.py.

Dim shell, fso, scriptDir, cmdLine, q, i
q = Chr(34)

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

If WScript.Arguments.Count = 0 Then
    MsgBox "Drag a video file onto this, or use it via Send To / right-click.", 0, "RIPChamp"
    WScript.Quit
End If

For i = 0 To WScript.Arguments.Count - 1
    cmdLine = "powershell -ExecutionPolicy Bypass -NoProfile -File " & q & scriptDir & "\ripchamp_tools.ps1" & q & _
              " -Mode Prompt -Path " & q & WScript.Arguments(i) & q
    shell.Run cmdLine, 4, False
Next

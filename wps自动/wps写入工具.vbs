Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = FindFile(baseDir, "wps_excel_to_kdocs_gui.py")

If scriptPath = "" Then
  MsgBox "Could not find wps_excel_to_kdocs_gui.py under:" & vbCrLf & baseDir, vbCritical, "WPS writer"
  WScript.Quit 1
End If

If shell.Environment("PROCESS")("WPS_WRITER_SMOKE") = "1" Then
  WScript.Echo "WPS_WRITER_SCRIPT=" & scriptPath
  WScript.Quit 0
End If

pythonw = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe"
If Not fso.FileExists(pythonw) Then
  pythonw = "pythonw.exe"
End If

shell.CurrentDirectory = fso.GetParentFolderName(scriptPath)
shell.Run """" & pythonw & """ """ & scriptPath & """", 0, False

Function FindFile(folderPath, fileName)
  Dim folder, file, subFolder, found
  Set folder = fso.GetFolder(folderPath)
  For Each file In folder.Files
    If LCase(file.Name) = LCase(fileName) Then
      FindFile = file.Path
      Exit Function
    End If
  Next
  For Each subFolder In folder.SubFolders
    found = FindFile(subFolder.Path, fileName)
    If found <> "" Then
      FindFile = found
      Exit Function
    End If
  Next
  FindFile = ""
End Function
